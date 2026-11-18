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


def code_candidates(sku):
    """Ways our SKU may appear at a supplier, most specific first: as is; without quantity marks such as (2);
    with vehicle suffixes removed one at a time (FC41232-PX3 -> FC41232, TDGS-386/1-PJPK -> TDGS-386/1)."""
    s0 = (sku or "").strip()
    if not s0:
        return []
    s1 = re.sub(r"\(\d+\)", "", s0)
    out = [s0, s1]
    parts = s1.split("-")
    for i in range(len(parts) - 1, 0, -1):
        out.append("-".join(parts[:i]))
    seen, res = set(), []
    for x in out:
        k = code_norm(x)
        if len(k) >= 4 and k not in seen:
            seen.add(k)
            res.append(x)
    return res


def lookup(stock, sku):
    """(value, how) from a {normalised code: value} map using code_candidates and kit expansion."""
    for c in code_candidates(sku):
        hit = stock.get(code_norm(c))
        if hit is not None:
            return hit, ("exact" if code_norm(c) == code_norm(sku) else "suffix")
        parts = kit_codes(c)
        if parts:
            vals = [stock.get(code_norm(x)) for x in parts]
            if all(v is not None for v in vals):
                return vals, "kit"
    return None, None


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
    # Wheel search by brand (JSR, GT Form and Black Rock are brands in the portal's wheel search).
    try:
        _, ws = pl._get(op, cfg["home"] + "/WheelSearch/")
        NYC_DIAG["wheelsearch_forms"] = [{"action": a, "fields": sorted(set(re.findall(r'name="([^"]+)"', body)))[:20]}
                                         for a, body in re.findall(r'<form[^>]*action="([^"]*)"[^>]*>(.*?)</form>', ws, re.S | re.I)][:8]
        NYC_DIAG["wheelsearch_selects"] = {n: len(re.findall(r"<option", body)) for n, body in
                                           re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', ws, re.S | re.I)}
        brands = {n.lower(): v for v, n in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>\s*([^<]+?)\s*</option>', ws)}
        NYC_DIAG["brands_listed"] = sorted(b for b in brands if any(w.lower() in b for w in NYC_VENDORS))
        ef = form_fields(ws, "/WheelSearch/SearchElements") or {}
        bsel = re.search(r'<select[^>]*name="Brand"[^>]*>(.*?)</select>', ws, re.S | re.I)
        bopts = {n.strip().lower(): v for v, n in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>\s*([^<]+?)\s*</option>', bsel.group(1))} if bsel else {}
        found = []
        for vend in NYC_VENDORS:
            f = {k: v for k, v in ef.items() if k not in ("Brand",)}
            f["Brand"] = bopts.get(vend.lower(), vend)
            req = urllib.request.Request(cfg["home"] + "/WheelSearch/SearchElements", data=urllib.parse.urlencode(f).encode(),
                                         headers={"User-Agent": pl.UA, "Content-Type": "application/x-www-form-urlencoded"})
            try:
                with op.open(req, timeout=300) as r:
                    body = r.read().decode("utf-8", "replace")
                    landed = urllib.parse.urlsplit(r.geturl()).path
            except Exception as e:
                NYC_DIAG.setdefault("brand_searches", []).append({"brand": vend, "error": f"{type(e).__name__}: {str(e)[:80]}"})
                continue
            got = parse_nyc_rows(body)
            NYC_DIAG.setdefault("brand_searches", []).append({"brand": vend, "landed": landed, "rows": len(got), "bytes": len(body),
                "desc_shapes": [re.sub(r"[A-Za-z]", "x", re.sub(r"\d", "9", x["desc"]))[:50] for x in got[:3]],
                "forms_on_result": re.findall(r'<form[^>]*action="([^"]*)"', body)[:6],
                "links_on_result": sorted(set(re.findall(r'href="(/(?:WheelSearch|Search|Product)[^"?#]*)', body)))[:15]})
            found += got
        if len(found) > len(rows):
            rows = found
    except Exception as e:
        NYC_DIAG["wheelsearch_error"] = f"{type(e).__name__}: {str(e)[:150]}"
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


EQ_DIAG = {}
EQ_PRICE_IDS = set()
EQ_IDS = {}
EQ_BUY = {}  # normalised code -> unit buy price (ex GST), for the margin floor only; never published
EQ_WANT_PRICES = []  # set by main before reading: our (sku, price) pairs, for a ratio check only


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
                    EQ_IDS[code] = it.get("productId")
            if page_no == 1:
                # Pricing diagnostics (key names and counts only): is a retail or default sell price available?
                try:
                    ids = [it.get("productId") for it in (model.get("products") or [])[:20] if it.get("productId")]
                    pr = page.request.post(f"{EQ_API}/products/prices", data={"productIds": ids}, headers=headers, timeout=90000)
                    pm = ((pr.json() or {}).get("model") or {}).get("products") or []
                    EQ_PRICE_IDS.update({x.get("productId") for x in pm})
                    EQ_DIAG["price_entries_per_product"] = sorted({len(x.get("prices") or []) for x in pm})
                    EQ_DIAG["price_entry_keys"] = sorted({k for x in pm for e in (x.get("prices") or []) for k in e})
                    EQ_DIAG["product_settings"] = {k: v for k, v in (model.get("productSettings") or {}).items() if isinstance(v, bool)}
                    if ids:
                        det = page.request.get(f"{EQ_API}/products/{ids[0]}", headers=headers, timeout=90000)
                        dj = (det.json() or {}) if det.status == 200 else {}
                        dm = dj.get("model") if isinstance(dj, dict) else None
                        EQ_DIAG["detail_status"] = det.status
                        EQ_DIAG["detail_keys"] = sorted(dm.keys())[:60] if isinstance(dm, dict) else type(dm).__name__
                except Exception as e:
                    EQ_DIAG["price_diag_error"] = f"{type(e).__name__}: {str(e)[:120]}"
            page_no += 1
            if page_no > 2000:
                break
        # Ratio check for up to 150 of our products: our price / Equipit unitPrice and / discountedUnitPrice.
        try:
            pairs = [(EQ_IDS.get(code_norm(c)), price) for sku, price in EQ_WANT_PRICES for c in code_candidates(sku)[:1]]
            pairs = [(i, p) for i, p in pairs if i and p][:600]
            ratios_u, ratios_d = [], []
            for k in range(0, len(pairs), 50):
                chunk = pairs[k:k + 50]
                pr = page.request.post(f"{EQ_API}/products/prices", data={"productIds": [i for i, _ in chunk]}, headers=headers, timeout=90000)
                pm = {x.get("productId"): (x.get("prices") or [{}])[0] for x in ((pr.json() or {}).get("model") or {}).get("products") or []}
                ids_to_code = {v: k for k, v in EQ_IDS.items()}
                for i, ours in chunk:
                    e = pm.get(i) or {}
                    if e.get("discountedUnitPrice") and i in ids_to_code:
                        EQ_BUY[ids_to_code[i]] = float(e["discountedUnitPrice"])
                    if e.get("unitPrice"): ratios_u.append(ours / float(e["unitPrice"]))
                    if e.get("discountedUnitPrice"): ratios_d.append(ours / float(e["discountedUnitPrice"]))
            med = lambda xs: round(sorted(xs)[len(xs) // 2], 3) if xs else None
            EQ_DIAG["price_ratio_ours_over_unit"] = {"n": len(ratios_u), "median": med(ratios_u)}
            EQ_DIAG["price_ratio_ours_over_discounted"] = {"n": len(ratios_d), "median": med(ratios_d)}
        except Exception as e:
            EQ_DIAG["ratio_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        b.close()
    return out


# ---------------------------------------------------------------- Tough Dog price guide
PRICE_GUIDE_STORE = os.environ.get("TD_PRICE_STORE", "https://ppdperformance.com.au")
PRICE_GUIDE_PATH = os.environ.get("TD_PRICE_PATH", "/collections/tough-dog-lift-kits/products.json")
PRICE_OFFSET = float(os.environ.get("PRICE_OFFSET", "1"))
MIN_PRICE = float(os.environ.get("MIN_PRICE", "10"))
MIN_MARGIN = float(os.environ.get("TD_MIN_MARGIN", "0.10"))  # never below buy price + GST + this margin
MAX_SWING = float(os.environ.get("TD_MAX_SWING", "0.35"))    # bigger moves are held for review (pair vs single etc.)


def read_price_guide():
    """{normalised code: lowest current price} for Tough Dog items in the guide store. Codes are indexed both as
    listed and without a leading TD prefix (the store lists TD-FC41130B for Tough Dog FC41130B)."""
    out, page = {}, 1
    while page < 60:
        req = urllib.request.Request(f"{PRICE_GUIDE_STORE}{PRICE_GUIDE_PATH}?limit=250&page={page}", headers={"User-Agent": pl.UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            ps = json.loads(r.read().decode("utf-8", "replace")).get("products", [])
        if not ps:
            break
        for p in ps:
            if "tough" not in (p.get("vendor") or "").lower():
                continue
            for v in p["variants"]:
                raw = (v.get("sku") or "").strip()
                if not raw or re.search(r"\bx\s*\d", raw, re.I):  # skip multi-pack variants such as "x2"
                    continue
                price = float(v.get("price") or 0)
                if price <= 0:
                    continue
                k = code_norm(raw)
                for key in {k, k[2:] if k.startswith("TD") and len(k) > 5 else k}:
                    out[key] = min(price, out.get(key, price))
        page += 1
    return out


def price_plan(prods, guide, buy, rep):
    """Review the guide price less PRICE_OFFSET for each matched Tough Dog variant. Moves bigger than MAX_SWING, and
    prices that would break the margin floor, are held rather than proposed."""
    plan = []
    for p in prods:
        for v in p["variants"]:
            if not v["sku"]:
                continue
            hit, how = lookup(guide, v["sku"])
            if hit is None or how == "kit":
                continue
            rep["price_matched"] += 1
            want = round(max(MIN_PRICE, hit - PRICE_OFFSET), 2)
            floor = None
            b, _ = lookup(buy, v["sku"])
            if b and not isinstance(b, list):
                floor = round(b * 1.1 * (1 + MIN_MARGIN), 2)
            cur = v["price"]
            name = f"{p['title']}"
            if abs(want - cur) < 0.01:
                continue
            if floor and want < floor:
                rep["price_held"].append({"product": name, "now": cur, "guide": hit, "why": "below the margin floor"})
                continue
            if cur > 0 and abs(want - cur) / cur > MAX_SWING:
                rep["price_held"].append({"product": name, "now": cur, "guide": hit, "why": f"move over {int(MAX_SWING * 100)}% (check pair or single)"})
                continue
            plan.append((p, v, want))
            if len(rep["price_examples"]) < EXAMPLES:
                rep["price_examples"].append({"product": name, "from": cur, "to": want, "handle": p.get("handle", "")})
    rep["price_would_change"] = len(plan)
    rep["price_held"] = rep["price_held"][:EXAMPLES]
    return plan


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
        prods = [p for v in EQUIPIT_VENDORS for p in sync.shopify_products(f'vendor:"{v}"')]
        EQ_WANT_PRICES[:] = [(v["sku"], v["price"]) for p in prods for v in p["variants"] if v["sku"] and v["price"]]
        stock = read_equipit()
        rep["rows"] = len(stock)
        rep["our_variants"] = sum(len(p["variants"]) for p in prods)
        states, unmatched_skus, kits = [], [], 0
        for p in prods:
            for v in p["variants"]:
                hit, how = lookup(stock, v["sku"]) if v["sku"] else (None, None)
                if how == "kit":
                    hit = (min(x[0] for x in hit), any(x[1] for x in hit))
                    kits += 1
                elif how == "suffix":
                    rep["suffix_matched"] = rep.get("suffix_matched", 0) + 1
                if hit is None:
                    rep["unmatched"] += 1
                    unmatched_skus.append(v["sku"])
                    continue
                qty, sold_out = hit
                states.append((p, v, state_from_qty(qty, sellable_when_out=not sold_out)))
        rep["kits_matched"] = kits
        rep["diag"] = {"our_unmatched_sku_shapes": top_shapes(unmatched_skus), "portal_code_shapes": top_shapes(list(stock)),
                       "unmatched_blank_sku": sum(1 for x in unmatched_skus if not x), **EQ_DIAG}
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

    # Tough Dog prices: competitor guide less $1, with a margin floor from the Equipit buy price.
    rep = result["portals"]["Equipit"]
    rep.update({"price_matched": 0, "price_would_change": 0, "price_examples": [], "price_held": []})
    price_updates = []
    try:
        guide = read_price_guide()
        rep["price_guide"] = {"store": PRICE_GUIDE_STORE, "codes": len(guide)}
        tprods = [p for v in EQUIPIT_VENDORS for p in sync.shopify_products(f'vendor:"{v}"')]
        price_updates = price_plan(tprods, guide, EQ_BUY, rep)
    except Exception as e:
        rep["price_error"] = f"{type(e).__name__}: {str(e)[:200]}"

    if APPLY and price_updates and os.environ.get("TD_PRICES_APPLY", "0") == "1":
        by_p = {}
        for p, v, want in price_updates:
            by_p.setdefault(p["id"], []).append({"id": v["id"], "price": f"{want:.2f}"})
        for pid, vs in by_p.items():
            try:
                sync.gql(sync.BULK_M, {"pid": pid, "v": vs})
            except Exception as e:
                rep.setdefault("price_apply_errors", []).append(str(e)[:150])
        rep["prices_applied"] = len(price_updates)

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
            if "price_matched" in r:
                lines.append(f"  - Tough Dog price guide: {r['price_matched']} matched, {r['price_would_change']} price changes proposed, "
                             f"{len(r['price_held'])} held for review" + (f" ({r['price_error']})" if r.get("price_error") else ""))
        else:
            lines.append(f"- {name}: could not be read ({r['error']})")
    open("portals.md", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
