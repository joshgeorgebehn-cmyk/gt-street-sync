#!/usr/bin/env python3
"""
GT Street CNC sync: prices and stock for NYC Wheels brands (GT Form, JSR, Black Rock).

Every run:
  1. Reads CNC Wheels' public product sitemaps to find every CNC page for these brands.
  2. Reads GT Street's products for these brands from the Shopify Admin API.
  3. Matches each GT Street product to a CNC range (brand, model, finish) and each variant
     to the CNC page for its rim size and PCD.
  4. Reads only the CNC pages that GT Street needs: current price and stock status.
  5. Price: CNC price minus PRICE_OFFSET (default $1). Stock: CNC "Out of stock" makes the
     variant Sold out on GT Street (inventory tracked, 0 available, no overselling); in stock
     or back order makes it buyable again (inventory not tracked).
  6. Writes a report (report.md, report.json) and stamps the shop metafield gt_sync.last_run.

Safety rules (change with environment variables):
  * A new price below MIN_PRICE (default 150) or more than MAX_DROP (default 0.30) under the
    current price is held, not applied, and listed in the report for a person to check.
  * Anything that cannot be matched to a CNC page is left exactly as it is and reported.
  * Offsets missing from CNC's offset list are reported only.
  * DRY_RUN=1 changes nothing in Shopify.

XForce source: xforce.com.au public catalogue. Products with vendor XForce are matched on the exact title (word
order ignored), priced at XForce's price minus XF_PRICE_OFFSET and Sold out when XForce is. On by default (XFORCE=1).

Optional source, off by default (JUST_JAP=1 to enable): Just Jap (justjap.com public catalogue). Products tagged supplier:justjap are matched on the exact
SKU, priced at Just Jap's price minus JJ_PRICE_OFFSET (default: PRICE_OFFSET) and Sold out when Just Jap is.
Set JUST_JAP=0 to switch it off.

Credentials (GitHub Actions secrets, never in code):
  SHOPIFY_STORE          e.g. srbc11-w2.myshopify.com
  SHOPIFY_ADMIN_TOKEN    Admin API access token (shpat_...), or instead:
  SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET  for apps that use the client credentials grant.
"""
import os, re, json, time, gzip, html, sys, datetime, concurrent.futures as cf
import urllib.request, urllib.error, urllib.parse
from norm import norm

API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-10")
STORE = re.sub(r"^https?://", "", os.environ.get("SHOPIFY_STORE", "srbc11-w2.myshopify.com").strip()).strip("/")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
PRICE_OFFSET = float(os.environ.get("PRICE_OFFSET", "1"))
MIN_PRICE = float(os.environ.get("MIN_PRICE", "150"))
MAX_DROP = float(os.environ.get("MAX_DROP", "0.30"))
CNC_DELAY = float(os.environ.get("CNC_DELAY", "0.8"))
CNC_WORKERS = int(os.environ.get("CNC_WORKERS", "2"))
VENDORS = ("GT Form", "JSR", "Black Rock")
BRAND_PREFIX = ("gt-form", "jsr", "black-rock")
UA = "GTStreetStockSync/1.0 (+https://gtstreet.au; sales@gtstreet.au)"

# GT Street title (normalised) -> CNC range name (normalised), where the two name the same wheel differently.
ALIAS = {
    "gt form gfs4 silver machined face": ["gt form gfs4 silver machined"],
    "gt form gfs5 silver machined face": ["gt form gfs5 silver machined"],
    "gt form gfs8 silver machined": ["gt form gfs8 silver machined face"],
    "jsr st21 silver machined lip": ["jsr st21 silver machined"],
    "black rock vortex satin gunmetal grey": ["black rock vortex gunmetal grey"],
    "black rock duty gloss black machined": ["black rock duty black machined face"],
    "black rock cargo gloss black machined": ["black rock cargo black machined face"],
    "black rock rambler gunmetal grey black ring": ["black rock rambler gunmetal grey black ring", "black rock rambler dark grey black ring"],
    "black rock rambler silver machined": ["black rock rambler silver machined", "black rock rambler silver machined face"],
    "jsr st23 gloss black": ["jsr st23 gloss black", "jsr st23 satin black"],
}

SIZE_RE = re.compile(r"^(\d{2})x(\d{1,2})$")


# ---------------------------------------------------------------- HTTP helpers
def http(url, data=None, headers=None, timeout=60, retries=3):
    h = {"User-Agent": UA, "Accept-Encoding": "gzip"}
    h.update(headers or {})
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                return r.status, body.decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1)); continue
            try:
                eb = e.read()
                if e.headers.get("Content-Encoding") == "gzip":
                    eb = gzip.decompress(eb)
                return e.code, eb.decode("utf-8", "ignore")
            except Exception:
                return e.code, ""
        except Exception:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1)); continue
            return 0, ""
    return 0, ""


# ---------------------------------------------------------------- Shopify
_TOKEN = None
def token():
    global _TOKEN
    if _TOKEN: return _TOKEN
    t = os.environ.get("SHOPIFY_ADMIN_TOKEN")
    if t:
        _TOKEN = t; return t
    cid, sec = os.environ.get("SHOPIFY_CLIENT_ID"), os.environ.get("SHOPIFY_CLIENT_SECRET")
    if not (cid and sec):
        sys.exit("No Shopify credentials: set SHOPIFY_ADMIN_TOKEN, or SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET")
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid.strip(), "client_secret": sec.strip()}).encode()
    st, txt = http(f"https://{STORE}/admin/oauth/access_token", data=body,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
    if st != 200:
        # Shopify's error body names the cause (invalid_client, app not installed, etc). It never contains the secret.
        plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", txt))
        m = re.search(r"Oauth error \S+", plain) or re.search(r'"error[^}]*', txt)
        sys.exit(f"Could not get a Shopify access token from {STORE} (HTTP {st}): {m.group(0).strip() if m else txt[:200]}")
    _TOKEN = json.loads(txt)["access_token"]; return _TOKEN

def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    for attempt in range(6):
        st, txt = http(f"https://{STORE}/admin/api/{API_VERSION}/graphql.json", data=body,
                       headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token()})
        if st != 200:
            raise RuntimeError(f"Shopify HTTP {st}")
        d = json.loads(txt)
        errs = d.get("errors")
        if errs and any("THROTTLED" in json.dumps(e) for e in errs):
            time.sleep(4 * (attempt + 1)); continue
        if errs:
            raise RuntimeError(json.dumps(errs)[:500])
        cost = d.get("extensions", {}).get("cost", {}).get("throttleStatus", {})
        if cost and cost.get("currentlyAvailable", 1000) < 200:
            time.sleep(2)
        return d["data"]
    raise RuntimeError("Shopify throttled too long")

PRODUCTS_Q = """
query($cursor:String,$q:String!){ products(first:50, after:$cursor, query:$q){
  pageInfo{hasNextPage endCursor}
  nodes{ id title handle vendor status
    variants(first:250){ pageInfo{hasNextPage endCursor} nodes{ id sku price inventoryPolicy selectedOptions{name value}
      inventoryItem{ id tracked } } } } } }"""
MORE_VARIANTS_Q = """
query($id:ID!,$cursor:String){ product(id:$id){ variants(first:250, after:$cursor){ pageInfo{hasNextPage endCursor}
  nodes{ id sku price inventoryPolicy selectedOptions{name value} inventoryItem{ id tracked } } } } }"""

def shopify_products(search=None):
    q = search or "(" + " OR ".join(f'vendor:"{v}"' for v in VENDORS) + ")"
    out, cursor = [], None
    while True:
        d = gql(PRODUCTS_Q, {"cursor": cursor, "q": f"{q} AND (status:active OR status:draft)"})["products"]
        for p in d["nodes"]:
            vs = p["variants"]["nodes"]; vinfo = p["variants"]["pageInfo"]
            while vinfo["hasNextPage"]:
                more = gql(MORE_VARIANTS_Q, {"id": p["id"], "cursor": vinfo["endCursor"]})["product"]["variants"]
                vs += more["nodes"]; vinfo = more["pageInfo"]
            out.append({"id": p["id"], "title": p["title"], "handle": p["handle"], "status": p["status"], "variants": [
                {"id": v["id"], "sku": (v.get("sku") or "").strip(), "price": float(v["price"]), "policy": v["inventoryPolicy"],
                 "tracked": v["inventoryItem"]["tracked"], "item": v["inventoryItem"]["id"],
                 "opts": {o["name"]: o["value"] for o in v["selectedOptions"]}} for v in vs]})
        if not d["pageInfo"]["hasNextPage"]: break
        cursor = d["pageInfo"]["endCursor"]
    return out

def storefront_products(all_vendors=False):
    """Testing only: public products.json, no inventory data, ids are numeric."""
    out, page = [], 1
    while True:
        st, txt = http(f"https://gtstreet.au/products.json?limit=250&page={page}")
        ps = json.loads(txt)["products"] if st == 200 else []
        if not ps: break
        for p in ps:
            if not all_vendors and p["vendor"] not in VENDORS: continue
            names = [o["name"] for o in p["options"]]
            out.append({"id": p["id"], "title": p["title"], "handle": p["handle"], "status": "ACTIVE", "tags": p.get("tags", []), "variants": [
                {"id": v["id"], "sku": (v.get("sku") or "").strip(), "price": float(v["price"]), "policy": None, "tracked": None, "item": None,
                 "opts": {names[i]: v[f"option{i+1}"] for i in range(len(names))}} for v in p["variants"]]})
        page += 1
    return out


# ---------------------------------------------------------------- CNC
def cnc_index():
    """All CNC wheel pages for the three brands: {(range, size, pcd): url}."""
    idx = {}
    for i in ["", "2", "3", "4", "5", "6", "7", "8", "9"]:
        st, txt = http(f"https://cncwheels.com.au/product-sitemap{i}.xml")
        if st != 200 or "<urlset" not in txt: continue
        for loc in re.findall(r"<loc>(https://cncwheels\.com\.au/shop/[^<]+)</loc>", txt):
            parts = loc.rstrip("/").split("/")
            slug = parts[-1]
            if parts[-2] == "wheel-tyre-packages" or "package" in slug or not slug.startswith(BRAND_PREFIX): continue
            toks = slug.split("-")
            k = next((j for j, t in enumerate(toks) if SIZE_RE.match(t)), None)
            if k is None: continue
            d, w = SIZE_RE.match(toks[k]).groups()
            rest = toks[k + 1:]
            if len(rest) > 1 and rest[0].isdigit() and len(rest[0]) <= 2 and re.match(r"^\d+x\d+$", rest[1]):  # 8-5 -> 8.5, 8-25 -> 8.25
                w += "." + rest[0]; rest = rest[1:]
            if not rest or not re.match(r"^\d+x\d+$", rest[0]): continue
            pcd = rest[0]
            if len(rest) > 1 and rest[1].isdigit() and len(rest[1]) == 1: pcd += "." + rest[1]
            idx[(norm(" ".join(toks[:k])), f"{d}x{w}", pcd)] = loc
    return idx

def cnc_page(url):
    st, t = http(url)
    time.sleep(CNC_DELAY)
    if st != 200 or not t: return {"ok": False, "status": st}
    m = re.search(r'"offers":\s*\{[^}]*"price":\s*"?([\d.]+)', t)
    price = float(m.group(1)) if m else None
    attrs = {}
    tab = re.search(r"woocommerce-product-attributes(.*?)</table>", t, re.S)
    if tab:
        for a, b in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", tab.group(1), re.S):
            attrs[re.sub("<[^>]+>", "", a).strip().lower()] = html.unescape(re.sub("<[^>]+>", "", b)).strip()
    stock = re.findall(r'class="stock[^"]*">([^<]*)', t)
    stock = stock[0].strip() if stock else ""
    offsets = [o.strip().replace(" ", "") for o in attrs.get("offset", "").split(",") if o.strip()]
    offsets = [("+" + o) if re.match(r"^\d", o) else o for o in offsets]
    return {"ok": price is not None, "price": price, "out": stock.lower() == "out of stock",
            "backorder": "back-order" in stock.lower() or "backorder" in stock.lower(), "offsets": offsets}


# ---------------------------------------------------------------- Just Jap
JJ_STORE = os.environ.get("JJ_STORE", "https://justjap.com")
JJ_TAG = os.environ.get("JJ_TAG", "supplier:justjap")
JJ_PRICE_OFFSET = float(os.environ.get("JJ_PRICE_OFFSET", os.environ.get("PRICE_OFFSET", "1")))
JJ_MIN_PRICE = float(os.environ.get("JJ_MIN_PRICE", "5"))

def jj_catalogue():
    """Just Jap's public Shopify catalogue: {SKU: (price, available)}. One request per 250 products."""
    out, page = {}, 1
    while page < 400:
        st, txt = http(f"{JJ_STORE}/products.json?limit=250&page={page}")
        if st != 200: return None if page == 1 else out
        ps = json.loads(txt).get("products", [])
        if not ps: break
        for p in ps:
            for v in p["variants"]:
                if v.get("sku"): out[v["sku"].strip().upper()] = (float(v["price"]), bool(v["available"]))
        page += 1; time.sleep(1.2)
    return out

# ---------------------------------------------------------------- XForce
XF_STORE = os.environ.get("XF_STORE", "https://www.xforce.com.au")
XF_VENDOR = os.environ.get("XF_VENDOR", "XForce")
XF_PRICE_OFFSET = float(os.environ.get("XF_PRICE_OFFSET", os.environ.get("PRICE_OFFSET", "1")))
XF_MIN_PRICE = float(os.environ.get("XF_MIN_PRICE", "5"))
XF_STOP = {"xforce", "system", "exhaust", "with", "and", "the", "for", "inch", "in", "to", "of"}

def xf_key(title):
    """Order-free word set of a title, so 'VW GOLF R MK7 Varex...' on XForce matches 'XForce VW Golf R MK7 Varex...' here."""
    s = title.lower().replace('"', " inch ").replace("&", " ")
    s = re.sub(r"(\d)\.(\d)", r"\1p\2", s)
    return frozenset(t for t in re.findall(r"[a-z0-9]+", s) if t not in XF_STOP)

def xf_catalogue():
    """XForce's public Shopify catalogue: {title word set: (price, available, sku, handle)} for single-variant products.
    Titles that appear more than once on XForce are dropped, so an ambiguous match is never used."""
    seen, out, page = {}, {}, 1
    while page < 40:
        st, txt = http(f"{XF_STORE}/products.json?limit=250&page={page}")
        if st != 200: return None if page == 1 else out
        ps = json.loads(txt).get("products", [])
        if not ps: break
        for p in ps:
            if len(p["variants"]) != 1: continue
            k, v = xf_key(p["title"]), p["variants"][0]
            seen[k] = seen.get(k, 0) + 1
            out[k] = (float(v["price"]), bool(v.get("available", True)), v.get("sku") or "", p["handle"])
        page += 1; time.sleep(1.2)
    return {k: v for k, v in out.items() if seen[k] == 1}

# ---------------------------------------------------------------- main
def main():
    started = datetime.datetime.now(datetime.timezone.utc)
    source = os.environ.get("SOURCE", "admin")
    if source == "admin":
        granted = {x["handle"] for x in gql("{ currentAppInstallation { accessScopes { handle } } }")["currentAppInstallation"]["accessScopes"]}
        need = {"write_products", "write_inventory", "read_locations"}
        if not need <= granted:
            sys.exit("The Shopify app is missing permissions: " + ", ".join(sorted(need - granted)) +
                     ". Release an app version with these scopes in the Dev Dashboard, then approve the update on the store. Granted now: " +
                     (", ".join(sorted(granted)) or "none"))
    products = shopify_products() if source == "admin" else storefront_products()
    if os.environ.get("LIMIT_PRODUCTS"):
        sk = int(os.environ.get("SKIP_PRODUCTS", "0")); products = products[sk:sk + int(os.environ["LIMIT_PRODUCTS"])]
    idx = cnc_index()
    ranges = {k[0] for k in idx}
    report = {"started": started.isoformat(), "dry_run": DRY_RUN, "products": len(products), "cnc_pages_known": len(idx),
              "unmatched_products": [], "unmatched_variants": [], "missing_offsets": [], "held": [],
              "price_changes": [], "sold_out": [], "back_in_stock": [], "errors": []}

    plan = []  # (product, variant, url)
    for p in products:
        k = norm(p["title"])
        keys = ALIAS.get(k, [k])
        if not any(x in ranges for x in keys):
            report["unmatched_products"].append(p["title"]); continue
        for v in p["variants"]:
            size, pcd, off = v["opts"].get("Rim Size"), v["opts"].get("PCD"), v["opts"].get("Offset")
            url = next((idx[(x, size, pcd)] for x in keys if (x, size, pcd) in idx), None)
            if not url:
                report["unmatched_variants"].append(f"{p['title']} {size} {pcd} {off}"); continue
            plan.append((p, v, url))

    urls = sorted({u for _, _, u in plan})
    print(f"{len(products)} products, {len(plan)} variants matched, reading {len(urls)} CNC pages", flush=True)
    pages = {}
    with cf.ThreadPoolExecutor(CNC_WORKERS) as ex:
        for u, res in zip(urls, ex.map(cnc_page, urls)):
            pages[u] = res
    failed = [u for u, r in pages.items() if not r["ok"]]
    report["cnc_pages_read"] = len(urls); report["cnc_pages_failed"] = len(failed)
    # If CNC is down or blocking, change nothing.
    if urls and len(failed) / len(urls) > 0.2:
        report["errors"].append(f"{len(failed)} of {len(urls)} CNC pages could not be read; CNC products left unchanged.")
        plan = []

    decisions = []  # (product, variant, display name, source price, source out of stock, offset)
    for p, v, url in plan:
        pg = pages[url]
        if not pg["ok"]: continue
        name = f"{p['title']} {v['opts'].get('Rim Size')} {v['opts'].get('PCD')} {v['opts'].get('Offset')}"
        if pg["offsets"] and v["opts"].get("Offset") not in pg["offsets"]:
            report["missing_offsets"].append(name)
        decisions.append((p, v, name, pg["price"], pg["out"], PRICE_OFFSET, MIN_PRICE))

    # Just Jap: products tagged supplier:justjap, matched on the exact SKU.
    if os.environ.get("JUST_JAP", "0") == "1":
        jj = jj_catalogue()
        if jj is None or len(jj) < 1000:
            report["errors"].append("Just Jap catalogue could not be read; Just Jap products left unchanged.")
        else:
            jjp = shopify_products(f'tag:"{JJ_TAG}"') if source == "admin" else [x for x in storefront_products(all_vendors=True) if JJ_TAG in x.get("tags", [])]
            report["jj_products"] = len(jjp); report["jj_unmatched"] = []
            for p in jjp:
                for v in p["variants"]:
                    hit = jj.get(v.get("sku", "").upper())
                    if not hit:
                        report["jj_unmatched"].append(f"{p['title']} ({v.get('sku') or 'no SKU'})"); continue
                    decisions.append((p, v, f"{p['title']} ({v['sku']})", hit[0], not hit[1], JJ_PRICE_OFFSET, JJ_MIN_PRICE))

    # XForce: products with vendor XForce, matched on the exact product title (word order ignored).
    if os.environ.get("XFORCE", "1") == "1":
        xf = xf_catalogue()
        if xf is None or len(xf) < 300:
            report["errors"].append("XForce catalogue could not be read; XForce products left unchanged.")
        else:
            xfp = shopify_products(f'vendor:"{XF_VENDOR}"') if source == "admin" else [x for x in storefront_products(all_vendors=True) if x.get("vendor", "").lower() == XF_VENDOR.lower()]
            report["xf_products"] = len(xfp); report["xf_unmatched"] = []
            for p in xfp:
                hit = xf.get(xf_key(p["title"]))
                if not hit or len(p["variants"]) != 1:
                    report["xf_unmatched"].append(p["title"]); continue
                v = p["variants"][0]
                decisions.append((p, v, f"{p['title']} (XForce {hit[2] or hit[3]})", hit[0], not hit[1], XF_PRICE_OFFSET, XF_MIN_PRICE))

    updates = {}  # product id -> list of variant inputs
    to_zero = []  # inventory item ids that become sold out
    for p, v, name, src_price, src_out, offset, floor in decisions:
        inp = {"id": v["id"]}
        new_price = round(src_price - offset, 2)
        if abs(new_price - v["price"]) >= 0.01:
            if new_price < floor or (v["price"] > 0 and new_price < v["price"] * (1 - MAX_DROP)):
                report["held"].append({"variant": name, "current": v["price"], "cnc": src_price})
            else:
                inp["price"] = f"{new_price:.2f}"
                report["price_changes"].append({"variant": name, "from": v["price"], "to": new_price})
        if src_out:
            if v["tracked"] is not True or v["policy"] != "DENY":
                inp["inventoryPolicy"] = "DENY"; inp["inventoryItem"] = {"tracked": True}
                report["sold_out"].append(name)
            if v["item"]: to_zero.append(v["item"])
        else:
            if v["tracked"] is True:
                inp["inventoryItem"] = {"tracked": False}
                report["back_in_stock"].append(name)
        if len(inp) > 1:
            updates.setdefault(p["id"], []).append(inp)

    finish(report, started, to_zero, updates)

BULK_M = """mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkUpdate(productId:$pid, variants:$v){
  userErrors{field message} } }"""
QTY_M = """mutation($input:InventorySetQuantitiesInput!){ inventorySetQuantities(input:$input){ userErrors{field message} } }"""
LOC_Q = "{ locations(first:1, query:\"active:true\"){ nodes{ id } } }"
META_M = """mutation($m:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$m){ userErrors{field message} } }"""
SHOP_Q = "{ shop { id } }"

def finish(report, started, to_zero, updates):
    if not DRY_RUN and os.environ.get("SOURCE", "admin") == "admin":
        for pid, vs in updates.items():
            for i in range(0, len(vs), 100):
                try:
                    errs = gql(BULK_M, {"pid": pid, "v": vs[i:i + 100]})["productVariantsBulkUpdate"]["userErrors"]
                    if errs: report["errors"].append({"product": pid, "errors": errs})
                except Exception as e:
                    report["errors"].append({"product": pid, "error": str(e)})
        if to_zero:
            loc = gql(LOC_Q)["locations"]["nodes"][0]["id"]
            for i in range(0, len(to_zero), 100):
                q = [{"inventoryItemId": it, "locationId": loc, "quantity": 0} for it in to_zero[i:i + 100]]
                try:
                    errs = gql(QTY_M, {"input": {"name": "available", "reason": "correction", "ignoreCompareQuantity": True,
                                                  "quantities": q}})["inventorySetQuantities"]["userErrors"]
                    if errs: report["errors"].append({"inventory": errs[:5]})
                except Exception as e:
                    report["errors"].append({"inventory": str(e)})
        shop = gql(SHOP_Q)["shop"]["id"]
        gql(META_M, {"m": [{"ownerId": shop, "namespace": "gt_sync", "key": "last_run", "type": "date_time",
                            "value": started.replace(microsecond=0).isoformat()}]})
    report["finished"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    json.dump(report, open("report.json", "w"), indent=1)
    md = [f"# CNC sync {'(dry run, nothing changed)' if report['dry_run'] else ''}",
          f"Started {report['started']}",
          f"- GT Street products checked: {report['products']}",
          f"- CNC pages read: {report.get('cnc_pages_read', 0)} (failed: {report.get('cnc_pages_failed', 0)})",
          f"- Price changes: {len(report['price_changes'])}",
          f"- Held for review (big drop or under ${MIN_PRICE:.0f}): {len(report['held'])}",
          f"- Newly sold out: {len(report['sold_out'])}",
          f"- Back in stock: {len(report['back_in_stock'])}",
          f"- Products not on CNC (left alone): {len(report['unmatched_products'])}",
          f"- Variants with no CNC page for that size and PCD (left alone): {len(report['unmatched_variants'])}",
          f"- Offsets CNC does not list (reported only): {len(report['missing_offsets'])}",
          f"- Just Jap products checked: {report.get('jj_products', 0)} (SKUs not on Just Jap: {len(report.get('jj_unmatched', []))})",
          f"- XForce products checked: {report.get('xf_products', 0)} (not matched to xforce.com.au: {len(report.get('xf_unmatched', []))})",
          f"- Errors: {len(report['errors'])}", ""]
    if report["held"]:
        md.append("## Held for review"); md += [f"- {h['variant']}: now ${h['current']:.2f}, CNC ${h['cnc']:.2f}" for h in report["held"][:200]]
    if report["errors"]:
        md.append("## Errors"); md += [f"- {json.dumps(e)[:300]}" for e in report["errors"][:50]]
    if report.get("xf_unmatched"):
        md.append("## XForce products not matched to xforce.com.au (left unchanged)"); md += [f"- {t}" for t in report["xf_unmatched"]]
    if report["unmatched_products"]:
        md.append("## Products not on CNC"); md += [f"- {t}" for t in report["unmatched_products"]]
    open("report.md", "w").write("\n".join(md) + "\n")
    print("\n".join(md[:14]))

if __name__ == "__main__":
    main()
