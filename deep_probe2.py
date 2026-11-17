"""Third-stage probe. Structure only: content types, sizes, JSON key names, column headings, row counts.
NYC: the search post that fills the product table. CoolDrive: search through a real browser (plain requests get 403)."""
import json, os, re, sys, urllib.parse, urllib.request
import portal_login as pl

def shape(text):
    try:
        d = json.loads(text)
    except Exception:
        pr = pl.probe(text)
        return {"kind": "html", "tables": pr["tables"][:3], "tr": pr["tag_counts"].get("tr", 0)}
    def keys(x, depth=0):
        if isinstance(x, dict):
            return {k: keys(v, depth + 1) if depth < 2 else type(v).__name__ for k, v in list(x.items())[:30]}
        if isinstance(x, list):
            return [f"list[{len(x)}]", keys(x[0], depth + 1) if x else None]
        return type(x).__name__
    return {"kind": "json", "keys": keys(d)}

def nyc():
    cfg = pl.PORTALS["nyc"]
    op, _, _ = pl.sign_in("nyc", os.environ["NYC_USER"].strip(), os.environ["NYC_PASS"].strip())
    _, html = pl._get(op, cfg["home"] + "/Search/Family/Wheels")
    m = re.search(r'<form[^>]*action="/Search/SearchByProductFamily".*?</form>', html, re.S | re.I)
    fields = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', m.group(0))) if m else {}
    tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html)
    fields["ProductFamilyName"] = "Wheels"
    if tok: fields["__RequestVerificationToken"] = tok.group(1)
    print("nyc: SearchByProductFamily fields sent:", sorted(k for k in fields if "Token" not in k))
    for path, extra in [("/Search/SearchByProductFamily", {}), ("/WheelSearch/QuickSearch", {"SearchQuery": "JSR"})]:
        f = dict(fields); f.update(extra)
        try:
            req = urllib.request.Request(cfg["home"] + path, data=urllib.parse.urlencode(f).encode(),
                  headers={"User-Agent": pl.UA, "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest"})
            with op.open(req, timeout=120) as r:
                body = r.read().decode("utf-8", "replace")
                print(json.dumps({"path": path, "status": r.status, "type": r.headers.get("Content-Type"), "bytes": len(body), "shape": shape(body)}, indent=1))
        except Exception as e:
            print(f"nyc: {path} failed ({type(e).__name__}: {str(e)[:80]})")
    # Data endpoints the page script uses after load.
    for p in sorted(set(re.findall(r'["\'](/(?:Search|api|Stock|Product)[A-Za-z0-9_/\-]*)', html)))[:25]:
        print("nyc script path:", p)

def cooldrive():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page()
        page.goto("https://ishop.cooldrive.com.au/login", wait_until="networkidle")
        page.fill("#user_email_address", os.environ["COOLDRIVE_USER"].strip())
        page.fill("#user_password", os.environ["COOLDRIVE_PASS"].strip())
        page.locator("form[action='/login'] [type=submit], form[action='/login'] button").first.click()
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(3000)
        for q in ["redarc", "brembo"]:
            page.goto(f"https://ishop.cooldrive.com.au/search?q={q}", wait_until="networkidle", timeout=60000)
            html = page.content()
            prods = sorted({urllib.parse.urlsplit(h).path for h in page.eval_on_selector_all("a[href]", "e=>e.map(a=>a.href)")
                            if "cooldrive" in h and re.search(r"/(p|product|products|item|parts?)/", h)})[:3]
            labels = sorted(set(x.lower() for x in re.findall(r"(?i)\b(in stock|out of stock|available|on hand|qty|stock|ex gst|inc gst|rrp|your price|trade price|nett?|price)\b", html)))
            print(json.dumps({"q": q, "status_path": urllib.parse.urlsplit(page.url).path, "shape": shape(html),
                              "product_paths": prods, "labels": labels, "top_classes": pl.probe(html)["top_classes"][:12]}, indent=1))
            if prods:
                page.goto("https://ishop.cooldrive.com.au" + prods[0], wait_until="networkidle")
                ph = page.content()
                labels = sorted(set(x.lower() for x in re.findall(r"(?i)\b(in stock|out of stock|available|on hand|qty|stock|ex gst|inc gst|rrp|your price|trade price|nett?|price|branch|part number|sku)\b", ph)))
                print(json.dumps({"product_page": prods[0], "shape": shape(ph), "labels": labels, "top_classes": pl.probe(ph)["top_classes"][:12]}, indent=1))
        b.close()

if __name__ == "__main__":
    {"nyc": nyc, "cooldrive": cooldrive}[os.environ["PORTAL"]]()
