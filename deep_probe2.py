"""Third-stage probe. Structure only: content types, sizes, JSON key names, column headings, row counts.
NYC: the search post that fills the product table. CoolDrive: search through a real browser (plain requests get 403)."""
import json, os, re, sys, urllib.parse, urllib.request
import portal_login as pl
from skeleton import skeleton

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
    try:
        f = dict(fields)
        req = urllib.request.Request(cfg["home"] + "/Search/SearchByProductFamily", data=urllib.parse.urlencode(f).encode(),
              headers={"User-Agent": pl.UA, "Content-Type": "application/x-www-form-urlencoded"})
        with op.open(req, timeout=120) as r:
            body = r.read().decode("utf-8", "replace")
        print("nyc: count of 'Product Code' labels:", len(re.findall(r"Product Code", body)))
        idx = body.find("Product Code")
        print("nyc: masked skeleton after the results table header:")
        print(skeleton(body[idx:] if idx >= 0 else body, None, 160))
        # Repeated row-ish classes and how many times they repeat (likely one per product).
        cls = re.findall(r'class="([^"]+)"', body)
        from collections import Counter
        print("nyc: repeated classes:", [c for c in Counter(cls).most_common(40) if 20 <= c[1] <= 5000][:20])
    except Exception as e:
        print(f"nyc: skeleton failed ({type(e).__name__}: {str(e)[:80]})")
    for ext in re.findall(r'(/api/externaldata/[0-9a-f-]{36})', html)[:1]:
        for meth in ("GET", "POST"):
            try:
                req = urllib.request.Request(cfg["home"] + ext, data=(b"" if meth == "POST" else None), method=meth,
                      headers={"User-Agent": pl.UA, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/html, */*"})
                with op.open(req, timeout=180) as r:
                    body = r.read().decode("utf-8", "replace")
                    print(json.dumps({"path": "/api/externaldata/{id}", "method": meth, "status": r.status, "type": r.headers.get("Content-Type"), "bytes": len(body), "shape": shape(body)}, indent=1))
                    break
            except Exception as e:
                print(f"nyc: externaldata {meth} failed ({type(e).__name__}: {str(e)[:80]})")
    # How the page script calls it (method and parameter names around the call, no values).
    i = html.find("/api/externaldata/")
    if i >= 0:
        ctx = html[max(0, i - 600): i + 400]
        print("nyc: call context keywords:", sorted(set(re.findall(r"\b(ajax|fetch|post|get|type|method|data|dataSrc|columns|serverSide|url|draw|start|length)\b", ctx, re.I))))
        print("nyc: column data names:", re.findall(r'data\s*:\s*["\']([A-Za-z0-9_.]+)["\']', html)[:30])
    # Data endpoints the page script uses after load.
    for p in sorted(set(re.findall(r'["\'](/(?:Search|api|Stock|Product)[A-Za-z0-9_/\-]*)', html)))[:25]:
        print("nyc script path:", p)

def cooldrive():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page()
        page.goto("https://ishop.cooldrive.com.au/login", wait_until="domcontentloaded"); page.wait_for_timeout(3000)
        page.fill("#user_email_address", os.environ["COOLDRIVE_USER"].strip())
        page.fill("#user_password", os.environ["COOLDRIVE_PASS"].strip())
        page.locator("form[action='/login'] [type=submit], form[action='/login'] button").first.click()
        page.wait_for_timeout(6000)
        for q in ["redarc", "brembo"]:
            page.goto(f"https://ishop.cooldrive.com.au/search?q={q}", wait_until="domcontentloaded", timeout=60000); page.wait_for_timeout(6000)
            html = page.content()
            prods = sorted({urllib.parse.urlsplit(h).path for h in page.eval_on_selector_all("a[href]", "e=>e.map(a=>a.href)")
                            if "cooldrive" in h and re.search(r"/(p|product|products|item|parts?)/", h)})[:3]
            labels = sorted(set(x.lower() for x in re.findall(r"(?i)\b(in stock|out of stock|available|on hand|qty|stock|ex gst|inc gst|rrp|your price|trade price|nett?|price)\b", html)))
            title = page.title()
            print(json.dumps({"q": q, "title_masked": re.sub(r"\d", "9", title)[:60],
                              "challenge_markers": [w for w in ("just a moment", "verify you are human", "cf-challenge", "captcha", "access denied", "no results", "0 results")
                                                    if w in html.lower()]}))
            print("cooldrive: masked skeleton of search results:")
            print(skeleton(html, r'class="main-content', 140))
            print(json.dumps({"q": q, "status_path": urllib.parse.urlsplit(page.url).path, "shape": shape(html),
                              "product_paths": prods, "labels": labels, "top_classes": pl.probe(html)["top_classes"][:12]}, indent=1))
            if prods:
                page.goto("https://ishop.cooldrive.com.au" + prods[0], wait_until="domcontentloaded"); page.wait_for_timeout(5000)
                ph = page.content()
                labels = sorted(set(x.lower() for x in re.findall(r"(?i)\b(in stock|out of stock|available|on hand|qty|stock|ex gst|inc gst|rrp|your price|trade price|nett?|price|branch|part number|sku)\b", ph)))
                print(json.dumps({"product_page": prods[0], "shape": shape(ph), "labels": labels, "top_classes": pl.probe(ph)["top_classes"][:12]}, indent=1))
        b.close()

if __name__ == "__main__":
    {"nyc": nyc, "cooldrive": cooldrive}[os.environ["PORTAL"]]()
