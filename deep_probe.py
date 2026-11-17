"""Second-stage probe: find where each portal's product rows come from.
Logs paths, field names, column headings and counts only. Never values, prices, stock or credentials."""
import json, os, re, sys, urllib.parse
import portal_login as pl

def js_paths(html):
    found = set()
    for m in re.finditer(r'["\'](/[A-Za-z][A-Za-z0-9_/\-]{2,80})(?:\?[^"\']*)?["\']', html):
        path = m.group(1)
        if re.search(r"\.(css|js|png|jpe?g|svg|ico|woff2?|gif)$", path, re.I):
            continue
        if re.search(r"search|product|stock|item|data|get|list|api|family|wheel|price|inventory|query|load", path, re.I):
            found.add(path)
    return sorted(found)[:40]

def forms(html):
    out = []
    for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", html, re.I | re.S):
        action = re.search(r'action="([^"]*)"', m.group(1))
        method = re.search(r'method="([^"]*)"', m.group(1))
        names = re.findall(r'<(?:input|select|textarea)[^>]*name="([^"]+)"', m.group(2), re.I)
        out.append({"action": action.group(1) if action else "", "method": (method.group(1) if method else "get").lower(),
                    "fields": [n for n in names if "token" not in n.lower()][:15]})
    return out[:6]

def report(portal, label, html):
    pr = pl.probe(html)
    print(json.dumps({"portal": portal, "page": label, "tables": pr["tables"][:4], "forms": forms(html),
                      "script_paths": js_paths(html), "top_classes": pr["top_classes"][:10]}, indent=1))

def main():
    portal = os.environ["PORTAL"]
    cfg = pl.PORTALS[portal]
    opener, _, _ = pl.sign_in(portal, os.environ[cfg["user_env"]].strip(), os.environ[cfg["pass_env"]].strip())
    if portal == "nyc":
        pages = ["/Search/Family/Wheels", "/Search/Family/Tyres", "/WheelSearch/"]
    else:
        pages = ["/search?q=redarc", "/search?q=brembo", "/iShop"]
    for path in pages:
        try:
            url, html = pl._get(opener, urllib.parse.urljoin(cfg["home"], path))
            report(portal, f"{path} -> {urllib.parse.urlsplit(url).path}", html)
            if portal == "cooldrive":
                prod = sorted({urllib.parse.urlsplit(h).path for h in re.findall(r'<a\b[^>]*href="([^"#]+)"', html)
                               if re.search(r"/(p|product|products|item)s?/", h, re.I)})[:3]
                print(json.dumps({"portal": portal, "product_link_paths_sample": prod}))
                for pp in prod[:1]:
                    _, ph = pl._get(opener, urllib.parse.urljoin(cfg["home"], pp))
                    report(portal, f"product page {pp}", ph)
                    labels = sorted(set(re.findall(r"(?i)\b(in stock|out of stock|available|on hand|qty|quantity|stock|price|ex gst|inc gst|rrp|your price|trade)\b", ph)))
                    print(json.dumps({"portal": portal, "labels_on_product_page": labels}))
        except Exception as exc:
            print(f"{portal}: {path} failed ({type(exc).__name__}: {str(exc)[:80]})")

if __name__ == "__main__":
    main()
