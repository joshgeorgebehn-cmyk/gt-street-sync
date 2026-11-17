"""
Independent check after every live run: did Shopify end up with exactly what the supplier shows right now?

Takes a random sample of the variants the run just updated, reads the supplier's page again from scratch, reads the
variant back from Shopify, and compares:
  price      Shopify price == supplier price minus the offset (unless the run held that change for review)
  stock      Shopify sellable == supplier in stock (for suppliers whose stock is synced)
Any mismatch fails the workflow (red cross in GitHub, which emails the repo owner) and is listed in verify.md.
Sample size: VERIFY_CNC (default 40) CNC variants and VERIFY_SUPPLIER (default 20) per Shopify-platform supplier.
"""
import os, sys, json, random
from sync import gql, http, cnc_page, DRY_RUN

VQ = """query($ids:[ID!]!){ nodes(ids:$ids){ ... on ProductVariant { id price inventoryPolicy availableForSale
  inventoryQuantity inventoryItem{ tracked } } } }"""


def shopify(ids):
    out = {}
    for i in range(0, len(ids), 100):
        for n in gql(VQ, {"ids": ids[i:i + 100]})["nodes"]:
            if n: out[n["id"]] = n
    return out


def main():
    if DRY_RUN:
        print("Dry run: nothing was written, so there is nothing to verify."); return
    try:
        r = json.load(open("report.json"))
    except Exception:
        print("No report.json: the sync did not finish, so nothing to verify."); sys.exit(1)
    held = {h.get("id") for h in r.get("held", [])}
    rnd = random.Random()
    cnc = r.get("cnc_applied", [])
    cnc = rnd.sample(cnc, min(int(os.environ.get("VERIFY_CNC", "40")), len(cnc)))
    sup = {}
    for a in r.get("supplier_applied", []):
        sup.setdefault(a["store"], []).append(a)
    sup = {k: rnd.sample(v, min(int(os.environ.get("VERIFY_SUPPLIER", "20")), len(v))) for k, v in sup.items()}
    ids = [a["variant"] for a in cnc] + [a["variant"] for v in sup.values() for a in v]
    live = shopify(ids)
    offset = float(os.environ.get("PRICE_OFFSET", "1"))
    checked, bad = 0, []

    def sellable(n):
        return n["availableForSale"]

    for a in cnc:
        pg = cnc_page(a["url"]); n = live.get(a["variant"])
        if not pg.get("ok") or not n:
            continue  # page unreadable now: the next run will skip it the same way
        checked += 1
        want = round(pg["price"] - offset, 2)
        if a["variant"] not in held and abs(float(n["price"]) - want) > 0.005:
            bad.append(f"PRICE {a['name']}: Shopify ${float(n['price']):.2f}, CNC now ${pg['price']:.2f} (expected ${want:.2f})")
        if sellable(n) == pg["out"]:
            bad.append(f"STOCK {a['name']}: Shopify {'sellable' if sellable(n) else 'sold out'}, CNC {'out of stock' if pg['out'] else 'in stock'}")
    for store, items in sup.items():
        for a in items:
            st, txt = http(f"{store}/products/{a['handle']}.js"); n = live.get(a["variant"])
            if st != 200 or not n:
                continue
            p = json.loads(txt)
            v = next((x for x in p["variants"] if (x.get("sku") or "").strip().strip("[]").upper() == (a["sku"] or "").upper()), None)
            if v is None and len(p["variants"]) == 1: v = p["variants"][0]
            if v is None: continue
            checked += 1
            want = round(v["price"] / 100 - a["offset"], 2)
            if a["variant"] not in held and abs(float(n["price"]) - want) > 0.005:
                bad.append(f"PRICE {a['name']}: Shopify ${float(n['price']):.2f}, {store} now ${v['price']/100:.2f} (expected ${want:.2f})")
            if a["stock"] and sellable(n) != bool(v["available"]):
                bad.append(f"STOCK {a['name']}: Shopify {'sellable' if sellable(n) else 'sold out'}, {store} {'in stock' if v['available'] else 'sold out'}")
    lines = ["# Independent verification", "", f"- Variants re-checked against the supplier: {checked}", f"- Mismatches: {len(bad)}"]
    if bad:
        lines += ["", "## Mismatches (fix before the next run)"] + [f"- {b}" for b in bad]
    out = "\n".join(lines) + "\n"
    print(out)
    open("verify.md", "w").write(out)
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
