"""Dealer portal stock: NYC Dealerline (wheels) and Equipit (Tough Dog and other 4x4 lines).

Runs after sync.py in every check. Review mode is the default (PORTALS_APPLY unset or 0): the portals are read,
matched to GT Street's variants and compared, and the result is written to portals.json and portals.md for the
daily digest. Nothing in Shopify changes until PORTALS_APPLY=1.

Rules
  * NYC Dealerline is stock only. Prices for GT Form, JSR and Black Rock stay with the CNC price rule.
  * Equipit is stock only for now. Its prices are GT Street's dealer buy prices, not retail, so the "supplier
    price less $1" rule must never be applied to them.
  * Stock: more than LOW_QTY = In stock (20). 1 to LOW_QTY = Low stock (exact quantity). 0 = Out of stock, or
    Back order where the supplier keeps selling (Equipit "sellable" items, or CNC already shows back order).
  * A portal that cannot be read, or that matches suspiciously few products, changes nothing.
  * Logins come from GitHub secrets only. Nothing here prints page content, supplier codes, buy prices or tokens.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

import portal_login as pl

APPLY = os.environ.get("PORTALS_APPLY", "0") == "1"
LOW_QTY = int(os.environ.get("LOW_QTY", "5"))
STOCK_QTY = int(os.environ.get("STOCK_QTY", "20"))
OWN_STOCK_TAG = os.environ.get("OWN_STOCK_TAG", "own-stock").lower()
NYC_VENDORS = [v.strip() for v in os.environ.get("NYC_VENDORS", "GT Form,JSR,Black Rock").split(",") if v.strip()]
EQUIPIT_VENDORS = [v.strip() for v in os.environ.get("EQUIPIT_VENDORS", "Tough Dog").split(",") if v.strip()]
EXAMPLES = 40


# ---------------------------------------------------------------- shared helpers
def norm(s):
    return re.sub(r"[^a-z0-9.+x]", " ", (s or "").lower())


def code_norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def shape(s):
    return re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "9", s or ""))


def top_shapes(values, n=8):
    from collections import Counter
    return Counter(shape(v) for v in values if v).most_common(n)


def kit_codes(sku):
    """Part numbers in a kit SKU such as TDC1063/1064/1065/1066 or FOR-013/14/15/16. Later parts that are only
    digits take the first part's letters and leading digits, padded to the same length."""
    parts = [x.strip() for x in re.split(r"[/,+&]", sku or "") if x.strip()]
    if len(parts) < 2:
        return []
    first = parts[0]
    out = [first]
    for x in parts[1:]:
        if re.fullmatch(r"\d+", x) and len(x) <= len(first):
            out.append(first[: len(first) - len(x)] + x)
        else:
            out.append(x)
    return out


def state_from_qty(qty, sellable_when_out=False):
    """(state, quantity) in the same model as sync.py."""
    q = int(qty or 0)
    if q > LOW_QTY:
        return ("in", STOCK_QTY)
    if q > 0:
        return ("low", q)
    return ("backorder", 0) if sellable_when_out else ("out", 0)


def our_state(v):
    if v["tracked"] is not True:
        return "in"
    q = v.get("qty") or 0
    if q <= 0:
        return "backorder" if v["policy"] == "CONTINUE" else "out"
    return "low" if q <= LOW_QTY else "in"


# ---------------------------------------------------------------- NYC Dealerline
WHEEL_SIZE = re.compile(r"\b(\d{2})\s*[xX]\s*(\d{1,2}(?:\.\d)?)\b")
PCD = re.compile(r"\b(\d)\s*[xX]\s*(\d{3}(?:\.\d{1,2})?)\b")
OFFSET = re.compile(r"(?:\b(?:ET|OFFSET)\s*([+-]?\d{1,3})\b)|(?:(?<![\w.])([+-]\d{1,3})\b)", re.I)


def _cell(row, cls):
    m = re.search(r'<td[^>]*class="[^"]*\b' + cls + r'\b[^"]*"[^>]*>(.*?)</td>', row, re.S | re.I)
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()


def parse_nyc_rows(html):
    """[{code, make, desc, qty}] from the Dealerline buying table (tr.BuyingTableRow)."""
    out = []
    for row in re.findall(r'<tr[^>]*class="[^"]*BuyingTableRow[^"]*"[^>]*>(.*?)</tr>', html, re.S | re.I):
        code = _cell(row, "BuyingTableProductCode")
        desc = _cell(row, "BuyingTableDesc")
        qm = re.search(r'class="[^"]*product-display-quantity[^"]*"[^>]*>\s*([\d,]+)', row)
        mm = re.search(r'class="[^"]*make-image[^"]*"[^>]*>.*?<div>\s*([^<]+?)\s*</div>', row, re.S)
        if not code:
            continue
        out.append({"code": code, "desc": desc, "make": (mm.group(1).strip() if mm else ""),
                    "qty": int(qm.group(1).replace(",", "")) if qm else None})
    return out


def form_fields(html, action):
    """Every named input of the form posting to `action`, whatever the attribute order."""
    m = re.search(r'<form[^>]*action="' + re.escape(action) + r'"[^>]*>(.*?)</form>', html, re.S | re.I)
    if not m:
        return None
    fields = {}
    for tag in re.findall(r"<(?:input|select)\b[^>]*>", m.group(1), re.I):
        n = re.search(r'name="([^"]+)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n:
            fields.setdefault(n.group(1), v.group(1) if v else "")
    tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html)
    if tok:
        fields.setdefault("__RequestVerificationToken", tok.group(1))
    return fields


NYC_DIAG = {}


def read_nyc():
    cfg = pl.PORTALS["nyc"]
    op, _, _ = pl.sign_in("nyc", os.environ["NYC_USER"].strip(), os.environ["NYC_PASS"].strip())
    _, page = pl._get(op, cfg["home"] + "/Search/Family/Wheels")
    rows = parse_nyc_rows(page)
    fields = form_fields(page, "/Search/SearchByProductFamily")
    NYC_DIAG["requests"] = [{"path": "/Search/Family/Wheels", "rows": len(rows)}]
    NYC_DIAG["fields"] = sorted(k for k in (fields or {}) if "Token" not in k)
    NYC_DIAG["family_value_shape"] = shape((fields or {}).get("ProductFamilyName", ""))
    NYC_DIAG["page_forms"] = re.findall(r'<form[^>]*action="([^"]*)"', page)[:10]
    if fields is not None:
        fields["ProductFamilyName"] = fields.get("ProductFamilyName") or "Wheels"
        req = urllib.request.Request(cfg["home"] + "/Search/SearchByProductFamily", data=urllib.parse.urlencode(fields).encode(),
                                     headers={"User-Agent": pl.UA, "Content-Type": "application/x-www-form-urlencoded"})
        with op.open(req, timeout=300) as r:
            body = r.read().decode("utf-8", "replace")
        more = parse_nyc_rows(body)
        NYC_DIAG["requests"].append({"path": "/Search/SearchByProductFamily", "rows": len(more), "bytes": len(body)})
        # The wheel search page may also offer brand links; record their paths (no values).
        NYC_DIAG["wheel_links"] = sorted(set(re.findall(r'href="(/(?:Search|WheelSearch)[^"?#]*)', page)))[:20]
        if len(more) > len(rows):
            rows = more
    return rows


def wheel_key(text):
    t = text.replace(" ", " ")
    size = WHEEL_SIZE.search(t)
    pcd = None
    for m in PCD.finditer(t):
        if not size or m.span() != size.span():
            pcd = m
            break
    off = OFFSET.search(t)
    if not (size and pcd and off):
        return None
    return (f"{int(size.group(1))}x{float(size.group(2)):g}", f"{pcd.group(1)}x{float(pcd.group(2)):g}", f"{int(off.group(1) or off.group(2)):+d}")


def our_wheel_key(v):
    o = v["opts"]
    size = WHEEL_SIZE.search(o.get("Rim Size", "") or "")
    pcd = PCD.search(o.get("PCD", "") or "")
    off = re.search(r"([+-]?\d{1,3})", o.get("Offset", "") or "")
    if not (size and pcd and off):
        return None
    return (f"{int(size.group(1))}x{float(size.group(2)):g}", f"{pcd.group(1)}x{float(pcd.group(2)):g}", f"{int(off.group(1)):+d}")


def match_nyc(rows, products):
    """Match each of our variants to one NYC row: same size, PCD and offset, and every word of our product title
    (after the brand) appears in the NYC description. Ambiguous matches are left alone."""
    index = {}
    for r in rows:
        k = wheel_key(r["desc"])
        if k:
            index.setdefault(k, []).append(r)
    matched, unmatched, ambiguous = [], 0, 0
    for p in products:
        brand_words = set(norm(p.get("vendor", "")).split())
        words = [w for w in norm(p["title"]).split() if w not in brand_words]
        for v in p["variants"]:
            k = our_wheel_key(v)
            cands = [r for r in index.get(k, []) if all(w in norm(r["desc"]).split() for w in words)] if k else []
            if len(cands) == 1:
                matched.append((p, v, cands[0]))
            elif len(cands) > 1:
                ambiguous += 1
            else:
                unmatched += 1
    return matched, unmatched, ambiguous, sum(1 for r in rows if wheel_key(r["desc"]))


# ---------------------------------------------------------------- Equipit (Unleashed storefront)
EQ_HOME = "https://equipit.store.unleashedsoftware.com/home"
EQ_API = "https://store-api.unleashedsoftware.com/api"


def read_equipit():
    """{code: (stockAvailable, soldOut)} for every product in the store, read through the signed-in browser."""
    from playwright.sync_api import sync_playwright
    headers = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page()

        def grab(req):
            if "store-api.unleashedsoftware.com/api/" in req.url and "authorization" in {k.lower() for k in req.headers}:
                headers.update({k: v for k, v in req.headers.items() if k.lower() in ("authorization", "storeid", "store-id", "x-store-id", "accept")})
        page.on("request", grab)
        page.goto(EQ_HOME, wait_until="networkidle", timeout=90000)
        if not page.locator("input[type=password]").first.is_visible():
            for role in ("button", "link"):
                c = page.get_by_role(role, name=re.compile(r"log ?in|sign ?in", re.I))
                hit = next((c.nth(i) for i in range(c.count()) if c.nth(i).is_visible() and c.nth(i).is_enabled()), None)
                if hit:
                    hit.click()
                    break
        page.wait_for_selector("input[type=password]", timeout=30000)
        pwd = page.locator("input[type=password]").first
        form = pwd.locator("xpath=ancestor::form[1]")
        form.locator("input[type=email], input[type=text], input:not([type])").first.fill(os.environ["EQUIPIT_USER"].strip())
        pwd.fill(os.environ["EQUIPIT_PASS"].strip())
        s = form.locator("button[type=submit], input[type=submit]")
        (s.first.click() if s.count() and s.first.is_enabled() else pwd.press("Enter"))
        page.wait_for_timeout(8000)
        page.goto("https://equipit.store.unleashedsoftware.com/products", wait_until="networkidle", timeout=90000)
        if "authorization" not in {k.lower() for k in headers}:
            b.close()
            raise RuntimeError("equipit: signed in but no API authorisation was captured")
        out, page_no, pages, size = {}, 1, 1, 100
        while page_no <= pages:
            r = page.request.get(f"{EQ_API}/products?pageNumber={page_no}&pageSize={size}", headers=headers, timeout=90000)
            if r.status != 200:
                if size != 20 and page_no == 1:
                    size = 20
                    continue
                raise RuntimeError(f"equipit: product list page {page_no} returned HTTP {r.status}")
            model = (r.json() or {}).get("model") or {}
            pages = int((model.get("pagination") or {}).get("numberOfPages") or 1)
            for it in model.get("products") or []:
                code = code_norm(it.get("productDisplayTitle"))
                if code:
                    out[code] = (float(it.get("stockAvailable") or 0), bool(it.get("soldOut")))
            page_no += 1
            if page_no > 2000:
                break
        b.close()
    return out


# ---------------------------------------------------------------- compare and apply
def compare(name, matched_states, rep):
    """matched_states: [(product, variant, (state, qty))]. Fills rep and returns stock targets and policy changes."""
    targets, policies = {}, []
    for p, v, (state, q) in matched_states:
        if OWN_STOCK_TAG in [t.lower() for t in p.get("tags", [])] or not v["item"]:
            continue
        now = our_state(v)
        changes = now != state or (state == "low" and (v.get("qty") or 0) != q)
        if changes:
            rep["would_change"] += 1
            if len(rep["examples"]) < EXAMPLES:
                label = {"in": "In stock", "low": f"Low stock ({q} left)", "backorder": "Back order", "out": "Out of stock"}[state]
                rep["examples"].append({"product": p["title"], "variant": " / ".join(v["opts"].values()), "now": now, "portal": label,
                                        "handle": p.get("handle", "")})
        targets[v["item"]] = q if state != "in" else STOCK_QTY
        want = "CONTINUE" if state == "backorder" else "DENY"
        if v["tracked"] is not True or v["policy"] != want:
            policies.append((p["id"], {"id": v["id"], "inventoryPolicy": want, "inventoryItem": {"tracked": True}}))
    return targets, policies


def main():
    import sync  # Shopify access, inventory writes and the stock model live in the main sync
    result = {"apply": APPLY, "portals": {}}
    all_targets, all_policies = {}, []

    def blank():
        return {"ok": False, "error": None, "rows": 0, "matched": 0, "unmatched": 0, "ambiguous": 0, "would_change": 0, "examples": []}

    # NYC Dealerline: stock only for the NYC wheel brands.
    rep = blank()
    result["portals"]["NYC Dealerline"] = rep
    try:
        if not os.environ.get("NYC_USER"):
            raise RuntimeError("NYC_USER / NYC_PASS secrets are not set")
        rows = read_nyc()
        rep["rows"] = len(rows)
        from collections import Counter
        rep["diag"] = {"makes": Counter(r["make"] for r in rows).most_common(15),
                       "desc_shapes": [re.sub(r"[A-Za-z]", "x", re.sub(r"\d", "9", r["desc"]))[:50] for r in rows[:8]],
                       **NYC_DIAG}
        prods = []
        for v in NYC_VENDORS:
            for p in sync.shopify_products(f'vendor:"{v}"'):
                p["vendor"] = v
                prods.append(p)
        matched, rep["unmatched"], rep["ambiguous"], rep["rows_with_size"] = match_nyc(rows, prods)
        rep["matched"] = len(matched)
        rep["our_variants"] = sum(len(p["variants"]) for p in prods)
        if rep["rows"] < 50 or rep["matched"] < 0.2 * max(1, rep["our_variants"]):
            rep["error"] = (f"only {rep['matched']} of {rep['our_variants']} variants matched from {rep['rows']} portal rows; "
                            "nothing applied until matching is confirmed")
        states = [(p, v, state_from_qty(r["qty"], sellable_when_out=(our_state(v) == "backorder"))) for p, v, r in matched if r["qty"] is not None]
        t, pol = compare("NYC Dealerline", states, rep)
        rep["ok"] = rep["error"] is None
        if rep["ok"]:
            all_targets.update(t)
            all_policies += pol
    except Exception as e:
        rep["error"] = f"{type(e).__name__}: {str(e)[:200]}"

    # Equipit: stock only for the configured vendors (matched on exact part number).
    rep = blank()
    result["portals"]["Equipit"] = rep
    try:
        if not os.environ.get("EQUIPIT_USER"):
            raise RuntimeError("EQUIPIT_USER / EQUIPIT_PASS secrets are not set")
        stock = read_equipit()
        rep["rows"] = len(stock)
        prods = [p for v in EQUIPIT_VENDORS for p in sync.shopify_products(f'vendor:"{v}"')]
        rep["our_variants"] = sum(len(p["variants"]) for p in prods)
        states, unmatched_skus, kits = [], [], 0
        for p in prods:
            for v in p["variants"]:
                hit = stock.get(code_norm(v["sku"])) if v["sku"] else None
                if hit is None and v["sku"]:
                    parts = [stock.get(code_norm(x)) for x in kit_codes(v["sku"])]
                    if parts and all(x is not None for x in parts):
                        hit = (min(x[0] for x in parts), any(x[1] for x in parts))
                        kits += 1
                if hit is None:
                    rep["unmatched"] += 1
                    unmatched_skus.append(v["sku"])
                    continue
                qty, sold_out = hit
                states.append((p, v, state_from_qty(qty, sellable_when_out=not sold_out)))
        rep["kits_matched"] = kits
        rep["diag"] = {"our_unmatched_sku_shapes": top_shapes(unmatched_skus), "portal_code_shapes": top_shapes(list(stock)),
                       "unmatched_blank_sku": sum(1 for x in unmatched_skus if not x)}
        rep["matched"] = len(states)
        if rep["rows"] < 1000 or rep["matched"] < 0.2 * max(1, rep["our_variants"]):
            rep["error"] = (f"only {rep['matched']} of {rep['our_variants']} variants matched from {rep['rows']} portal products; "
                            "nothing applied until matching is confirmed")
        t, pol = compare("Equipit", states, rep)
        rep["ok"] = rep["error"] is None
        if rep["ok"]:
            all_targets.update(t)
            all_policies += pol
    except Exception as e:
        rep["error"] = f"{type(e).__name__}: {str(e)[:200]}"

    if APPLY and (all_targets or all_policies):
        report = {"errors": []}
        by_product = {}
        for pid, inp in all_policies:
            by_product.setdefault(pid, []).append(inp)
        for pid, vs in by_product.items():
            try:
                errs = sync.gql(sync.BULK_M, {"pid": pid, "v": vs})["productVariantsBulkUpdate"]["userErrors"]
                if errs:
                    report["errors"].append(errs[:3])
            except Exception as e:
                report["errors"].append(str(e)[:200])
        sync.apply_stock(report, all_targets)
        result["applied"] = {"quantities_updated": report.get("stock_quantities_updated", 0),
                             "policies_updated": len(all_policies), "errors": report["errors"][:10]}

    json.dump(result, open("portals.json", "w"), indent=1)
    mode = "applied" if APPLY else "review mode, nothing changed"
    lines = [f"# Dealer portals ({mode})", ""]
    for name, r in result["portals"].items():
        if r["ok"] or r["matched"]:
            lines.append(f"- {name}: {r['rows']} portal products read, {r['matched']} of our variants matched, "
                         f"{r['would_change']} stock differences" + (f" (not applied: {r['error']})" if r["error"] else ""))
        else:
            lines.append(f"- {name}: could not be read ({r['error']})")
    open("portals.md", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
