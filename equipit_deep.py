"""Equipit second-stage probe: record the background request paths the storefront makes after sign-in.
Paths, methods and status codes only, plus JSON key names of product responses. No values."""
import os, re, sys, json
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

HOME = "https://equipit.store.unleashedsoftware.com/home"

def main():
    reqs, keys = {}, {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(); page = b.new_page()
        def on_resp(r):
            u = urlsplit(r.url)
            if "unleashed" not in u.netloc or re.search(r"\.(js|css|png|jpe?g|svg|woff2?|ico)$", u.path): return
            k = f"{r.request.method} {u.netloc}{re.sub(r'/[0-9a-f-]{16,}', '/{id}', u.path)}"
            reqs[k] = r.status
            if "json" in (r.headers.get("content-type") or "") and re.search(r"product|item|stock|catalog|search", u.path, re.I) and k not in keys:
                try:
                    d = r.json()
                    if isinstance(d, dict):
                        first = next((v for v in d.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
                        keys[k] = {"top": sorted(d.keys())[:20], "item": sorted(first[0].keys())[:40] if first else []}
                    elif isinstance(d, list) and d and isinstance(d[0], dict):
                        keys[k] = {"item": sorted(d[0].keys())[:40]}
                except Exception: pass
        page.on("response", on_resp)
        page.goto(HOME, wait_until="networkidle", timeout=60000)
        if not page.locator("input[type=password]").first.is_visible():
            for role in ("button", "link"):
                c = page.get_by_role(role, name=re.compile(r"log ?in|sign ?in", re.I))
                hit = next((c.nth(i) for i in range(c.count()) if c.nth(i).is_visible() and c.nth(i).is_enabled()), None)
                if hit: hit.click(); break
        page.wait_for_selector("input[type=password]", timeout=20000)
        pwd = page.locator("input[type=password]").first
        form = pwd.locator("xpath=ancestor::form[1]")
        form.locator("input[type=email], input[type=text], input:not([type])").first.fill(os.environ["EQUIPIT_USER"].strip())
        pwd.fill(os.environ["EQUIPIT_PASS"].strip())
        s = form.locator("button[type=submit], input[type=submit]")
        (s.first.click() if s.count() and s.first.is_enabled() else pwd.press("Enter"))
        page.wait_for_timeout(8000); page.wait_for_load_state("networkidle", timeout=60000)
        links = sorted({urlsplit(h).path for h in page.eval_on_selector_all("a[href]", "e=>e.map(a=>a.href)") if "equipit" in h})[:25]
        print(json.dumps({"menu_paths": links}, indent=1))
        for path in [p for p in links if re.search(r"product|catalog|shop|categor|brand", p, re.I)][:3] + ["/products?search=tough%20dog"]:
            try:
                page.goto("https://equipit.store.unleashedsoftware.com" + path, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(4000)
            except Exception as exc:
                print(f"equipit: {path} failed ({type(exc).__name__})")
        b.close()
    print(json.dumps({"requests": reqs, "json_keys": keys}, indent=1))

if __name__ == "__main__":
    main()
