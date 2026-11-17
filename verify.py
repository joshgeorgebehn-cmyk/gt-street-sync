"""
Independent check after every live run: did Shopify end up with exactly what each supplier's website shows right now?

Re-checks EVERY variant the run amended (price or stock), plus a random sample of unchanged variants
(VERIFY_UNCHANGED_CNC, default 40, and VERIFY_UNCHANGED_SUPPLIER, default 10 per supplier). For each it reads the
supplier's page again from scratch, reads the variant back from Shopify, and compares:
  price   Shopify price == the price the supplier's page shows, less $1 (matched if the supplier is $1 or less)
  stock   Shopify state == the supplier's state: In stock (20), Low stock (their exact quantity),
          Back order (0, still orderable), Out of stock (0, not orderable)
Any mismatch fails the workflow and is listed in verify.md (and in the daily email).
"""
import os, sys, json, random
import concurrent.futures as cf
from sync import gql, http, cnc_page, supplier_page_stock, stock_state, DRY_RUN, STOCK_QTY

VQ = """query($ids:[ID!]!){ nodes(ids:$ids){ ... on ProductVariant { id price inventoryPolicy availableForSale
  inventoryQuantity inventoryItem{ tracked } } } }"""
WORKERS = int(os.environ.get("VERIFY_WORKERS", "6"))
OFFSET = float(os.environ.get("PRICE_OFFSET", "1"))


def shopify(ids):
    out = {}
    for i in range(0, len(ids), 100):
        for n in gql(VQ, {"ids": ids[i:i + 100]})["nodes"]:
            if n: out[n["id"]] = n
    return out


def want_price(src, offset=OFFSET):
    p = round(src - offset, 2)
    return p if p > 0 else round(src, 2)


def shop_state(n):
    if not n["availableForSale"]: return "out"
    q = n.get("inventoryQuantity") or 0
    if n["inventoryItem"]["tracked"] and q <= 0 and n["inventoryPolicy"] == "CONTINUE": return "backorder"
    return "in" if q > 5 or not n["inventoryItem"]["tracked"] else "low"


def main():
    if DRY_RUN:
        print("Dry run: nothing was written, so there is nothing to verify."); return
    try:
        r = json.load(open("report.json"))
    except Exception:
        print("No report.json: the sync did not finish, so nothing to verify."); sys.exit(1)
    amended = {c.get("id") for c in r.get("price_changes", [])} | {c.get("id") for c in r.get("stock_changes", [])}
    amended.discard(None)
    rnd = random.Random()
    cnc_all = r.get("cnc_applied", [])
    cnc = [a for a in cnc_all if a["variant"] in amended]
    rest = [a for a in cnc_all if a["variant"] not in amended]
    cnc += rnd.sample(rest, min(int(os.environ.get("VERIFY_UNCHANGED_CNC", "40")), len(rest)))
    sup = {}
    for a in r.get("supplier_applied", []):
        sup.setdefault(a["store"], []).append(a)
    for k, v in list(sup.items()):
        ch = [a for a in v if a["variant"] in amended]
        rest = [a for a in v if a["variant"] not in amended]
        sup[k] = ch + rnd.sample(rest, min(int(os.environ.get("VERIFY_UNCHANGED_SUPPLIER", "10")), len(rest)))
    ids = [a["variant"] for a in cnc] + [a["variant"] for v in sup.values() for a in v]
    live = shopify(ids)
    print(f"Re-checking {len(ids)} variants ({len(amended)} amended this run) against the supplier websites", flush=True)
    checked, bad, unreadable = 0, [], 0

    # CNC: one fresh read per page (a page covers several offsets).
    urls = sorted({a["url"] for a in cnc})
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        pages = dict(zip(urls, ex.map(cnc_page, urls)))
    for a in cnc:
        pg, n = pages.get(a["url"], {}), live.get(a["variant"])
        if not pg.get("ok") or not n:
            unreadable += 1; continue
        checked += 1
        w = want_price(pg["price"])
        if abs(float(n["price"]) - w) > 0.005:
            bad.append(f"PRICE {a['name']}: GT Street ${float(n['price']):.2f}, CNC page shows ${pg['price']:.2f} (expected ${w:.2f})")
        if a.get("own"): continue  # GT Street's own counted stock: price checked, stock not synced
        cs = "out" if pg["out"] else ("backorder" if pg.get("backorder") else "in")
        ss = shop_state(n)
        if ss != cs:
            bad.append(f"STOCK {a['name']}: GT Street {ss}, CNC page shows {cs}")

    # Shopify-platform suppliers: product .js for price and availability, product page for quantity and back order.
    for store, items in sup.items():
        handles = sorted({a["handle"] for a in items})
        def read(h):
            st, txt = http(f"{store}/products/{h}.js")
            js = json.loads(txt) if st == 200 and txt else None
            return h, js, (supplier_page_stock(store, h) if any(a["stock"] for a in items if a["handle"] == h) else {})
        with cf.ThreadPoolExecutor(WORKERS) as ex:
            got = {h: (js, pg) for h, js, pg in ex.map(read, handles)}
        for a in items:
            js, pg = got.get(a["handle"], (None, {}))
            n = live.get(a["variant"])
            v = next((x for x in (js or {}).get("variants", []) if x["id"] == a.get("vid")), None) if js else None
            if v is None or not n:
                unreadable += 1; continue
            checked += 1
            w = want_price(v["price"] / 100, a["offset"])
            if abs(float(n["price"]) - w) > 0.005:
                bad.append(f"PRICE {a['name']}: GT Street ${float(n['price']):.2f}, {store} shows ${v['price']/100:.2f} (expected ${w:.2f})")
            if a["stock"]:
                q = pg.get(a.get("vid"))
                st = stock_state(bool(v["available"]), q[0], q[1]) if q else None
                if st is None: continue  # supplier page and catalogue disagree today: the sync leaves these alone too
                ss = shop_state(n)
                if ss != st[0] or (st[0] == "low" and (n.get("inventoryQuantity") or 0) != st[1]):
                    bad.append(f"STOCK {a['name']}: GT Street {ss} ({n.get('inventoryQuantity')}), {store} shows {st[0]} ({st[1]})")
    lines = ["# Independent verification", "", f"- Variants re-checked against the supplier: {checked}",
             f"- Of which amended this run: {len(amended)}", f"- Pages unreadable during the re-check (not counted): {unreadable}",
             f"- Mismatches: {len(bad)}"]
    if bad:
        lines += ["", "## Mismatches (fix before the next run)"] + [f"- {b}" for b in bad[:500]]
    out = "\n".join(lines) + "\n"
    print(out)
    open("verify.md", "w").write(out)
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
