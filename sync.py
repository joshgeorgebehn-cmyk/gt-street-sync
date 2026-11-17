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
  * A new CNC price below MIN_PRICE (default $10, a guard against misread pages) is held and reported.
    MAX_DROP (default 0.20) holds any drop of more than 20% overnight for a person to check.
  * Anything that cannot be matched to a CNC page is left exactly as it is and reported.
  * Offsets missing from CNC's offset list are reported only.
  * DRY_RUN=1 changes nothing in Shopify.

Other suppliers: every supplier website listed in suppliers.json (Shopify-platform sites with a public
/products.json catalogue, e.g. XForce, BC Racing, Alu-Cab). GT Street products found by that entry's "query" are
matched on the exact SKU (and, where "match_title" is true, on the exact title), priced at the supplier's price minus
"price_offset" and marked Sold out when the supplier is. Turn one off with "enabled": false or a repo variable named
after its id (e.g. BCRACING=0). ONLY=xforce runs just that supplier (and skips CNC) for a quick test.

Credentials (GitHub Actions secrets, never in code):
  SHOPIFY_STORE          e.g. srbc11-w2.myshopify.com
  SHOPIFY_ADMIN_TOKEN    Admin API access token (shpat_...), or instead:
  SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET  for apps that use the client credentials grant.
"""
import uuid, os, re, json, time, gzip, html, sys, datetime, concurrent.futures as cf
import urllib.request, urllib.error, urllib.parse
from norm import norm

API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-10")
STORE = re.sub(r"^https?://", "", os.environ.get("SHOPIFY_STORE", "srbc11-w2.myshopify.com").strip()).strip("/")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
PRICE_OFFSET = float(os.environ.get("PRICE_OFFSET", "1"))
MIN_PRICE = float(os.environ.get("MIN_PRICE", "10"))
CNC_SET_SKU = os.environ.get("CNC_SET_SKU", "1") == "1"
# Stock model: every synced variant is tracked in Shopify. Out of stock at the supplier = 0 available (Sold out).
# In stock at the supplier = STOCK_QTY available. Suppliers publish availability, not counts, so STOCK_QTY is a
# stand-in meaning "available from the supplier". Products tagged OWN_STOCK_TAG hold GT Street's own counted stock
# and are never given supplier quantities.
STOCK_QTY = int(os.environ.get("STOCK_QTY", "20"))
OWN_STOCK_TAG = os.environ.get("OWN_STOCK_TAG", "own-stock").lower()
MAX_DROP = float(os.environ.get("MAX_DROP", "0.20"))  # a drop of more than 20% overnight is held for a person to check
CNC_DELAY = float(os.environ.get("CNC_DELAY", "0.5"))
CNC_WORKERS = int(os.environ.get("CNC_WORKERS", "4"))
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
def fresh(url):
    """Add a unique query value so no website cache, CDN or proxy can hand back an old copy of the page."""
    return url + ("&" if "?" in url else "?") + f"_gt={time.time_ns()}"

def http(url, data=None, headers=None, timeout=60, retries=3):
    h = {"User-Agent": UA, "Accept-Encoding": "gzip", "Cache-Control": "no-cache, no-store, max-age=0", "Pragma": "no-cache"}
    h.update(headers or {})
    for attempt in range(retries):
        try:
            req = urllib.request.Request(fresh(url) if data is None else url, data=data, headers=h)
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
  nodes{ id title handle vendor status tags featuredMedia{ preview{ image{ url } } }
    variants(first:250){ pageInfo{hasNextPage endCursor} nodes{ id sku price inventoryQuantity inventoryPolicy selectedOptions{name value}
      inventoryItem{ id tracked } } } } } }"""
MORE_VARIANTS_Q = """
query($id:ID!,$cursor:String){ product(id:$id){ variants(first:250, after:$cursor){ pageInfo{hasNextPage endCursor}
  nodes{ id sku price inventoryQuantity inventoryPolicy selectedOptions{name value} inventoryItem{ id tracked } } } } }"""

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
            img = ((((p.get("featuredMedia") or {}).get("preview") or {}).get("image")) or {}).get("url", "")
            out.append({"id": p["id"], "title": p["title"], "handle": p["handle"], "status": p["status"], "tags": p.get("tags", []), "image": img, "variants": [
                {"id": v["id"], "sku": (v.get("sku") or "").strip(), "price": float(v["price"]), "qty": v.get("inventoryQuantity"), "policy": v["inventoryPolicy"],
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
                {"id": v["id"], "sku": (v.get("sku") or "").strip(), "price": float(v["price"]), "qty": v.get("inventoryQuantity"), "policy": None, "tracked": None, "item": None,
                 "opts": {names[i]: v[f"option{i+1}"] for i in range(len(names))}} for v in p["variants"]]})
        page += 1
    return out


# ---------------------------------------------------------------- CNC
CNC_LASTMOD = {}  # url -> lastmod from CNC's sitemap, filled by cnc_index()

def cnc_index():
    """All CNC wheel pages for the three brands: {(range, size, pcd): url}."""
    idx = {}
    for i in ["", "2", "3", "4", "5", "6", "7", "8", "9"]:
        st, txt = http(f"https://cncwheels.com.au/product-sitemap{i}.xml")
        if st != 200 or "<urlset" not in txt: continue
        for m in re.finditer(r"<loc>(https://cncwheels\.com\.au/shop/[^<]+)</loc>(?:\s*<lastmod>([^<]+)</lastmod>)?", txt):
            loc = m.group(1)
            if m.group(2): CNC_LASTMOD[loc] = m.group(2)
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

CNC_PARSER_VERSION = 4

def _money(x):
    try:
        return float(x.replace(",", ""))
    except Exception:
        return None

def cnc_price(t):
    """The price CNC is actually selling this wheel for, per wheel, or (None, reason).

    Two independent readings must agree, otherwise the page is skipped and the variant is left unchanged:
      1. the price shown in the product summary under the title (the <ins> sale price if the wheel is on sale,
         otherwise the single price), and
      2. WooCommerce's own structured data: the offer's PriceSpecification price (not the ListPrice).
    CNC pages also carry a second, stale "Schema Pro" block with an out of date price (e.g. $425 when the wheel
    sells for $460). Version 1 of this parser read that block, which priced wheels too low. It is never used.
    """
    shown = None
    m = re.search(r'class="product_title entry-title".*?<p class="price">(.*?)</p>', t, re.S)
    if m:
        block = m.group(1)
        ins = re.search(r"<ins[^>]*>(.*?)</ins>", block, re.S)
        part = ins.group(1) if ins else block
        amts = re.findall(r"woocommerce-Price-amount amount\"><bdi>.*?</span>\s*([\d,]+(?:\.\d+)?)", part, re.S)
        if len(amts) == 1 or (ins and amts):
            shown = _money(amts[0])
    if shown is None:
        m = re.search(r"Current price is:\s*\$([\d,]+(?:\.\d+)?)", t)
        if m: shown = _money(m.group(1))
    struct = None
    m = re.search(r'"@type":"PriceSpecification","price":"([\d.]+)"', t)
    if m: struct = _money(m.group(1))
    if shown is None and struct is None: return None, "no price found", ""
    if shown is None or struct is None: return None, f"only one price reading (shown {shown}, structured {struct})", ""
    if abs(shown - struct) > 0.5:
        # Readings differ. If the price customers see is the higher one, use it (it is what CNC really sells at, so
        # we stay within market and do not undercut). If the structured figure is the higher one, we would be pricing
        # above what CNC shows, so the variant is left unchanged and reported.
        if shown > struct: return shown, "", f"readings differed (shown {shown}, structured {struct}); used the higher, shown price"
        return None, f"price readings disagree (shown {shown}, structured {struct}, shown is lower)", ""
    return shown, "", ""

def cnc_page(url):
    st, t = http(url)
    time.sleep(CNC_DELAY)
    if st != 200 or not t: return {"ok": False, "status": st}
    price, why, note = cnc_price(t)
    attrs = {}
    tab = re.search(r"woocommerce-product-attributes(.*?)</table>", t, re.S)
    if tab:
        for a, b in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", tab.group(1), re.S):
            attrs[re.sub("<[^>]+>", "", a).strip().lower()] = html.unescape(re.sub("<[^>]+>", "", b)).strip()
    stock = re.findall(r'class="stock[^"]*">([^<]*)', t)
    stock = stock[0].strip() if stock else ""
    # Stock is read twice as well: the stock line shown on the page and WooCommerce's structured availability.
    avail = re.search(r'"availability":"https:\\?/\\?/schema\.org\\?/(\w+)"', t)
    avail = avail.group(1) if avail else ""
    shown_out = stock.lower() == "out of stock"
    struct_out = avail == "OutOfStock" if avail else None
    if price is not None and struct_out is not None and shown_out != struct_out:
        price, why, note = None, f"stock readings disagree (page says '{stock or 'in stock'}', structured data says {avail})"
    offsets = [o.strip().replace(" ", "") for o in attrs.get("offset", "").split(",") if o.strip()]
    offsets = [("+" + o) if re.match(r"^\d", o) else o for o in offsets]
    msku = re.search(r'class="sku">\s*([^<\s][^<]*?)\s*<', t)
    return {"ok": price is not None, "price": price, "why": why, "note": note, "v": CNC_PARSER_VERSION,
            "out": shown_out, "sku": msku.group(1).strip() if msku else "",
            "backorder": "back-order" in stock.lower() or "backorder" in stock.lower(), "offsets": offsets}


# ---------------------------------------------------------------- Shopify-platform suppliers
# Many supplier and distributor websites run on Shopify and publish their catalogue at /products.json.
# Each one is a single entry in suppliers.json, so adding a supplier is a config change, not new code.
TITLE_STOP = {"system", "exhaust", "with", "and", "the", "for", "inch", "in", "to", "of"}

def title_key(title, brand=""):
    """Order-free word set of a title, ignoring the brand name and filler words."""
    s = title.lower().replace('"', " inch ").replace("&", " ")
    s = re.sub(r"(\d)\.(\d)", r"\1p\2", s)
    stop = TITLE_STOP | set(re.findall(r"[a-z0-9]+", brand.lower()))
    return frozenset(t for t in re.findall(r"[a-z0-9]+", s) if t not in stop)

def clean_sku(x):
    return (x or "").strip().strip("[]").strip()

def sku_norm(x, prefix=""):
    """Compare part numbers ignoring case, spaces and punctuation, and an optional prefix used only here (e.g. HR-)."""
    x = (x or "").strip().upper()
    if prefix and x.startswith(prefix.upper()): x = x[len(prefix):]
    return re.sub(r"[^A-Z0-9]", "", x)

def shopify_supplier_catalogue(cfg):
    """{SKU: rec} and {title key: rec} where rec = (price, available, sku, handle, variant id). Ambiguous titles are dropped."""
    store, brand = cfg["store"].rstrip("/"), cfg.get("brand", "")
    by_sku, by_title, seen, page = {}, {}, {}, 1
    while page < 200:
        st, txt = http(f"{store}{cfg.get('catalogue_path', '/products.json')}?limit=250&page={page}")
        if st != 200:
            if page == 1: return None, None
            break
        ps = json.loads(txt).get("products", [])
        if not ps: break
        for p in ps:
            for v in p["variants"]:
                rec = (float(v["price"]), bool(v.get("available", True)), clean_sku(v.get("sku")), p["handle"], v["id"])
                if rec[2]: by_sku[sku_norm(rec[2])] = rec
            if len(p["variants"]) == 1:
                k = title_key(p["title"], brand)
                seen[k] = seen.get(k, 0) + 1
                by_title[k] = (float(p["variants"][0]["price"]), bool(p["variants"][0].get("available", True)),
                               clean_sku(p["variants"][0].get("sku")), p["handle"], p["variants"][0]["id"])
        page += 1; time.sleep(float(cfg.get("delay", 1.0)))
    return by_sku, {k: v for k, v in by_title.items() if seen[k] == 1}

LOW_QTY = int(os.environ.get("LOW_QTY", "5"))  # at or below this the supplier's item is shown as Low stock (Alu-Cab uses 5)

def supplier_page_stock(store, handle):
    """{variant id: (quantity, policy)} as published on the supplier's own product page (their theme embeds it)."""
    st, t = http(f"{store}/products/{handle}")
    if st != 200 or not t: return {}
    t = t.replace('\\"', '"')
    out = {}
    for m in re.finditer(r'"id"\s*:\s*(\d{10,})((?:(?!"id"\s*:\s*\d{10,}).){0,4000}?)"inventory_quantity"\s*:\s*(-?\d+)', t, re.S):
        vid, mid, qty = int(m.group(1)), m.group(2), int(m.group(3))
        pol = re.search(r'"inventory_policy"\s*:\s*"(\w+)"', mid + t[m.end():m.end() + 200])
        out.setdefault(vid, (qty, pol.group(1) if pol else ""))
    return out

def stock_state(available, qty, policy):
    """The supplier's own state: ("in"|"low"|"backorder"|"out", qty), or None if the two readings disagree."""
    if qty is None: return None
    if not available:
        return ("out", 0) if qty <= 0 and policy != "continue" else None
    if qty > LOW_QTY: return ("in", qty)
    if qty > 0: return ("low", qty)
    if policy == "continue": return ("backorder", 0)
    return ("in", qty)  # sellable with no tracked quantity: the supplier shows it as available

def our_state(v):
    if v["tracked"] is not True: return "in"
    q = v.get("qty") or 0
    if q <= 0: return "backorder" if v["policy"] == "CONTINUE" else "out"
    return "low" if q <= LOW_QTY else "in"

STATE_LABEL = {"in": "In stock", "low": "Low stock", "backorder": "Back order", "out": "Out of stock"}

def load_suppliers():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "suppliers.json")
    with open(path) as f:
        cfg = json.load(f)
    only = {x.strip().lower() for x in os.environ.get("ONLY", "").split(",") if x.strip()}
    out = []
    for c in cfg:
        on = os.environ.get(c["id"].upper().replace("-", "_"), "1" if c.get("enabled", True) else "0") == "1"
        if only and only != {"suppliers"}: on = c["id"].lower() in only  # "suppliers" = every enabled supplier, no CNC
        if on: out.append(c)
    return out

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
    run_cnc = os.environ.get("CNC", "1") == "1" and (not os.environ.get("ONLY") or "cnc" in os.environ["ONLY"].lower())
    if not run_cnc: products = []
    idx = cnc_index() if run_cnc else {}
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
    # Every CNC page is read live from cncwheels.com.au on every run. Nothing is reused from earlier runs, and
    # nothing is taken from Google or any other copy of CNC's data.
    pages = {}
    with cf.ThreadPoolExecutor(CNC_WORKERS) as ex:
        for u, res in zip(urls, ex.map(cnc_page, urls)):
            pages[u] = res
    failed = [u for u, r in pages.items() if not r["ok"]]
    report["cnc_pages_read"] = len(urls); report["cnc_pages_failed"] = len(failed)
    report["cnc_skipped_pages"] = [{"url": u, "why": pages[u].get("why") or f"HTTP {pages[u].get('status')}"} for u in failed][:300]
    report["cnc_reading_notes"] = [{"url": u, "note": r["note"]} for u, r in pages.items() if r.get("note")][:300]
    # If CNC is down or blocking, change nothing.
    if urls and len(failed) / len(urls) > 0.2:
        report["errors"].append(f"{len(failed)} of {len(urls)} CNC pages could not be read; CNC products left unchanged.")
        plan = []

    sku_set = {}  # variant id -> supplier part number to write
    decisions = []  # (product, variant, display name, source price, source out of stock, offset)
    for p, v, url in plan:
        pg = pages[url]
        if not pg["ok"]: continue
        name = f"{p['title']} {v['opts'].get('Rim Size')} {v['opts'].get('PCD')} {v['opts'].get('Offset')}"
        if pg["offsets"] and v["opts"].get("Offset") not in pg["offsets"]:
            report["missing_offsets"].append(name)
        # CNC's stock code for this size and PCD. One CNC page covers several offsets, so the offset is appended
        # to keep every variant's SKU unique (e.g. 1674-P35). Only blank or placeholder (NYC-...) SKUs are replaced.
        if CNC_SET_SKU and pg.get("sku") and (not v["sku"] or v["sku"].upper().startswith("NYC-")):
            off = (v["opts"].get("Offset") or "").replace("+", "P").replace("-", "M").replace(" ", "")
            new_sku = f"{pg['sku']}-{off}" if off else pg["sku"]
            if new_sku != v["sku"]:
                sku_set[v["id"]] = new_sku
                report.setdefault("sku_changes", []).append({"product": name, "from": v["sku"], "to": new_sku})
        cnc_state = ("out", 0) if pg["out"] else (("backorder", 0) if pg.get("backorder") else ("in", STOCK_QTY))
        decisions.append((p, v, name, pg["price"], cnc_state, PRICE_OFFSET, MIN_PRICE))
        report.setdefault("cnc_applied", []).append({"variant": v["id"], "url": url, "name": name})

    # Other suppliers with a public Shopify catalogue (suppliers.json).
    report["suppliers"] = {}
    for cfg in load_suppliers():
        name_ = cfg["name"]
        by_sku, by_title = shopify_supplier_catalogue(cfg)
        rep = {"products": 0, "variants_matched": 0, "unmatched": [], "error": None}
        report["suppliers"][name_] = rep
        if by_sku is None or len(by_sku) < int(cfg.get("min_catalogue", 50)):
            rep["error"] = f"{name_} catalogue could not be read; {name_} products left unchanged."
            report["errors"].append(rep["error"]); continue
        prods = shopify_products(cfg["query"]) if source == "admin" else []
        rep["products"] = len(prods)
        offset, floor = float(cfg.get("price_offset", PRICE_OFFSET)), float(cfg.get("min_price", 5))
        matched = []
        for p in prods:
            for v in p["variants"]:
                hit = by_sku.get(sku_norm(v["sku"], cfg.get("sku_prefix", ""))) if v["sku"] else None
                if not hit and cfg.get("match_title") and len(p["variants"]) == 1:
                    hit = by_title.get(title_key(p["title"], cfg.get("brand", "")))
                if not hit:
                    rep["unmatched"].append(f"{p['title']} ({v['sku'] or 'no SKU'})"); continue
                rep["variants_matched"] += 1
                matched.append((p, v, hit))
        # Stock: read every matched product's page on the supplier's site for the exact quantity and back order setting.
        pages = {}
        if cfg.get("use_stock", True):
            handles = sorted({h[3] for _, _, h in matched})
            with cf.ThreadPoolExecutor(int(cfg.get("page_workers", 4))) as ex:
                for hd, res in zip(handles, ex.map(lambda x: supplier_page_stock(cfg["store"].rstrip("/"), x), handles)):
                    pages[hd] = res
        rep["stock_unverified"] = 0
        for p, v, hit in matched:
            if cfg.get("set_sku") and hit[2] and v["sku"] != hit[2]:
                sku_set[v["id"]] = hit[2]
                report.setdefault("sku_changes", []).append({"product": p["title"], "from": v["sku"], "to": hit[2]})
            src = None  # None = stock left unchanged
            if cfg.get("use_stock", True):
                q = pages.get(hit[3], {}).get(hit[4])
                src = stock_state(hit[1], q[0], q[1]) if q else None
                if src is None:
                    rep["stock_unverified"] += 1
                    report.setdefault("stock_unverified", []).append(f"{p['title']} ({name_} {hit[2] or hit[3]})")
            decisions.append((p, v, f"{p['title']} ({name_} {hit[2] or hit[3]})", hit[0], src, offset, floor))
            report.setdefault("supplier_applied", []).append({"variant": v["id"], "store": cfg["store"].rstrip("/"),
                "handle": hit[3], "sku": hit[2], "offset": offset, "stock": src is not None, "state": src[0] if src else None, "name": p["title"]})

    updates = {}  # product id -> list of variant inputs
    stock_targets = {}  # inventory item id -> available quantity to hold at the store location
    for p, v, name, src_price, src, offset, floor in decisions:
        inp = {"id": v["id"]}
        new_price = round(src_price - offset, 2)
        if abs(new_price - v["price"]) >= 0.01:
            if new_price < floor or (v["price"] > 0 and new_price < v["price"] * (1 - MAX_DROP)):
                report["held"].append({"variant": name, "product": p["title"], "id": v["id"], "current": v["price"], "cnc": src_price, "image": p.get("image", ""), "handle": p.get("handle", "")})
            else:
                inp["price"] = f"{new_price:.2f}"
                report["price_changes"].append({"variant": name, "product": p["title"], "from": v["price"], "to": new_price, "image": p.get("image", ""), "handle": p.get("handle", "")})
        own = OWN_STOCK_TAG in [t.lower() for t in p.get("tags", [])]
        if src is not None and not own and v["item"]:
            state, sq = src
            want_policy = "CONTINUE" if state == "backorder" else "DENY"
            if v["tracked"] is not True or v["policy"] != want_policy:
                inp["inventoryPolicy"] = want_policy; inp["inventoryItem"] = {"tracked": True}
            # In stock: 20. Low stock: the supplier's exact quantity. Back order and out of stock: 0 (back order stays orderable).
            stock_targets[v["item"]] = STOCK_QTY if state == "in" else (sq if state == "low" else 0)
            if our_state(v) != state:  # an actual change today
                report.setdefault("stock_changes", []).append({"variant": name, "product": p["title"],
                    "now": STATE_LABEL[state] + (f" ({sq} left)" if state == "low" else ""), "state": state,
                    "image": p.get("image", ""), "handle": p.get("handle", "")})
            (report["sold_out"] if state == "out" else report["back_in_stock"]).append(name)
        if v["id"] in sku_set:
            inp.setdefault("inventoryItem", {})["sku"] = sku_set[v["id"]]
        if len(inp) > 1:
            updates.setdefault(p["id"], []).append(inp)

    finish(report, started, stock_targets, updates)

BULK_M = """mutation($pid:ID!,$v:[ProductVariantsBulkInput!]!){ productVariantsBulkUpdate(productId:$pid, variants:$v){
  userErrors{field message} } }"""
# Shopify API 2026-04+ requires an idempotency key on inventory mutations.
QTY_M = """mutation($input:InventorySetQuantitiesInput!,$key:String!){ inventorySetQuantities(input:$input) @idempotent(key:$key){
  userErrors{field message} } }"""
LOC_Q = "{ locations(first:1, query:\"active:true\"){ nodes{ id } } }"
META_M = """mutation($m:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$m){ userErrors{field message} } }"""
SHOP_Q = "{ shop { id } }"

LEVELS_Q = """query($ids:[ID!]!,$loc:ID!){ nodes(ids:$ids){ ... on InventoryItem { id
  inventoryLevel(locationId:$loc){ quantities(names:["available"]){ quantity } } } } }"""

def apply_stock(report, targets):
    """Bring every synced variant's available quantity to its target, stocking it at the location first if needed
    (this is what clears "Inventory not stocked" in the admin)."""
    loc = gql(LOC_Q)["locations"]["nodes"][0]["id"]
    items = list(targets)
    to_set, to_activate = [], []
    for i in range(0, len(items), 100):
        for n in gql(LEVELS_Q, {"ids": items[i:i + 100], "loc": loc})["nodes"]:
            if not n: continue
            lvl = n.get("inventoryLevel")
            if lvl is None:
                to_activate.append(n["id"])
            else:
                have = lvl["quantities"][0]["quantity"] if lvl["quantities"] else None
                if have != targets[n["id"]]: to_set.append(n["id"])
    for i in range(0, len(to_activate), 25):
        chunk = to_activate[i:i + 25]
        body = " ".join(f'a{k}: inventoryActivate(inventoryItemId: "{it}", locationId: "{loc}", available: {targets[it]}) @idempotent(key: "{uuid.uuid4()}") {{ userErrors {{ message }} }}'
                        for k, it in enumerate(chunk))
        try:
            r = gql("mutation { " + body + " }")
            errs = [e for k in range(len(chunk)) for e in (r.get(f"a{k}") or {}).get("userErrors", [])]
            if errs: report["errors"].append({"activate": errs[:5]})
        except Exception as e:
            report["errors"].append({"activate": str(e)[:300]})
    for i in range(0, len(to_set), 100):
        q = [{"inventoryItemId": it, "locationId": loc, "quantity": targets[it], "changeFromQuantity": None} for it in to_set[i:i + 100]]
        try:
            errs = gql(QTY_M, {"input": {"name": "available", "reason": "correction", "quantities": q}, "key": str(uuid.uuid4())})["inventorySetQuantities"]["userErrors"]
            if errs: report["errors"].append({"inventory": errs[:5]})
        except Exception as e:
            report["errors"].append({"inventory": str(e)[:300]})
    report["stock_stocked_at_location"] = len(to_activate)
    report["stock_quantities_updated"] = len(to_set)

def finish(report, started, stock_targets, updates):
    if not DRY_RUN and os.environ.get("SOURCE", "admin") == "admin":
        for pid, vs in updates.items():
            for i in range(0, len(vs), 100):
                try:
                    errs = gql(BULK_M, {"pid": pid, "v": vs[i:i + 100]})["productVariantsBulkUpdate"]["userErrors"]
                    if errs: report["errors"].append({"product": pid, "errors": errs})
                except Exception as e:
                    report["errors"].append({"product": pid, "error": str(e)})
        if stock_targets:
            apply_stock(report, stock_targets)
        shop = gql(SHOP_Q)["shop"]["id"]
        gql(META_M, {"m": [{"ownerId": shop, "namespace": "gt_sync", "key": "last_run", "type": "date_time",
                            "value": started.replace(microsecond=0).isoformat()}]})
    report["finished"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    json.dump(report, open("report.json", "w"), indent=1)
    md = [f"# Supplier price and stock sync {'(dry run, nothing changed)' if report['dry_run'] else '(live)'}",
          f"Started {report['started']}",
          f"- GT Street products checked: {report['products']}",
          f"- CNC pages checked: {report.get('cnc_pages_read', 0)} (all read live; skipped: {report.get('cnc_pages_failed', 0)})",
          f"- Price changes: {len(report['price_changes'])}",
          f"- Held for review (under ${MIN_PRICE:.0f} or over the drop limit): {len(report['held'])}",
          f"- Sold out at the supplier: {len(report['sold_out'])}",
          f"- In stock at the supplier: {len(report['back_in_stock'])}",
          f"- Stock quantities changed: {report.get('stock_quantities_updated', 0)}; newly stocked at the location: {report.get('stock_stocked_at_location', 0)}",
          f"- Products not on CNC (left alone): {len(report['unmatched_products'])}",
          f"- Variants with no CNC page for that size and PCD (left alone): {len(report['unmatched_variants'])}",
          f"- Offsets CNC does not list (reported only): {len(report['missing_offsets'])}",
          f"- Part numbers replaced with the supplier's real part number: {len(report.get('sku_changes', []))}",
          *[f"- {n}: {r['products']} products, {r['variants_matched']} variants matched, {len(r['unmatched'])} not found on their site" + (f" (ERROR: {r['error']})" if r['error'] else "")
            for n, r in report.get("suppliers", {}).items()],
          f"- Errors: {len(report['errors'])}", ""]
    if report["held"]:
        md.append("## Held for review"); md += [f"- {h['variant']}: now ${h['current']:.2f}, CNC ${h['cnc']:.2f}" for h in report["held"][:200]]
    if report.get("cnc_skipped_pages"):
        md.append("## CNC pages skipped (left unchanged)"); md += [f"- {x['url']}: {x['why']}" for x in report["cnc_skipped_pages"][:100]]
    if report.get("cnc_reading_notes"):
        md.append("## CNC pages where the two price readings differed (higher shown price used)"); md += [f"- {x['url']}: {x['note']}" for x in report["cnc_reading_notes"][:100]]
    if report["errors"]:
        md.append("## Errors"); md += [f"- {json.dumps(e)[:300]}" for e in report["errors"][:50]]
    for n, r in report.get("suppliers", {}).items():
        if r["unmatched"]:
            md.append(f"## {n}: not found on their site (left unchanged)"); md += [f"- {t}" for t in r["unmatched"][:300]]
    if report["unmatched_products"]:
        md.append("## Products not on CNC"); md += [f"- {t}" for t in report["unmatched_products"]]
    open("report.md", "w").write("\n".join(md) + "\n")
    print("\n".join(md[:20]))

if __name__ == "__main__":
    main()
