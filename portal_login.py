"""Dealer portal sign-in for NYC Wheels (Dealerline) and CoolDrive (iShop).

Logins come from GitHub secrets only. Nothing here prints a page body, a password or a
token. Probe mode logs structure only (tag and class names, table headers and counts).
"""
import http.cookiejar
import os
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = "GTStreetStockSync/1.0 (+https://gtstreet.au; sales@gtstreet.au)"

PORTALS = {
    "nyc": {
        "login": "https://dealerline.nycwheels.com.au/Logon/Login",
        "home": "https://dealerline.nycwheels.com.au",
        "form_action": "",  # the page posts back to itself
        "user_field": "Username",
        "pass_field": "Password",
        "user_env": "NYC_USER",
        "pass_env": "NYC_PASS",
    },
    "cooldrive": {
        "login": "https://ishop.cooldrive.com.au/login",
        "home": "https://ishop.cooldrive.com.au",
        "form_action": "/login",
        "user_field": "user[email_address]",
        "pass_field": "user[password]",
        "user_env": "COOLDRIVE_USER",
        "pass_env": "COOLDRIVE_PASS",
    },
}


class _Forms(HTMLParser):
    """Collects the inputs of each form, keyed by the form's action."""

    def __init__(self):
        super().__init__()
        self.forms = []  # list of (action, [(name, value, type)])
        self._cur = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._cur = (a.get("action") or "", [])
            self.forms.append(self._cur)
        elif tag == "input" and self._cur is not None and a.get("name"):
            self._cur[1].append((a["name"], a.get("value") or "", (a.get("type") or "text").lower()))

    def handle_endtag(self, tag):
        if tag == "form":
            self._cur = None


def _opener():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def _get(opener, url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with opener.open(req, timeout=60) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


def _post(opener, url, fields):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA,
                                 "Content-Type": "application/x-www-form-urlencoded"})
    with opener.open(req, timeout=60) as r:
        return r.geturl(), r.read().decode("utf-8", "replace")


def sign_in(portal, user, password):
    """Return (opener, landing_url, html) after a sign-in attempt. Raises on failure."""
    cfg = PORTALS[portal]
    opener, _ = _opener()
    page_url, html = _get(opener, cfg["login"])
    p = _Forms()
    p.feed(html)
    # Pick the form that holds the password field, so the right hidden tokens are sent.
    form = next((f for f in p.forms if any(n == cfg["pass_field"] for n, _, _ in f[1])), None)
    if form is None:
        raise RuntimeError(f"{portal}: no sign-in form with the expected password field")
    action, inputs = form
    fields = {}
    for name, value, kind in inputs:
        if kind in ("checkbox",):
            continue
        fields[name] = value
    fields[cfg["user_field"]] = user
    fields[cfg["pass_field"]] = password
    if portal == "nyc":
        fields["RememberMe"] = "true"
    target = urllib.parse.urljoin(page_url, action or page_url)
    landed, body = _post(opener, target, fields)
    if "password" in body.lower() and ("invalid" in body.lower() or "incorrect" in body.lower()):
        raise RuntimeError(f"{portal}: sign-in rejected (check the secret values)")
    if landed.rstrip("/") == page_url.rstrip("/") and cfg["pass_field"].lower() in body.lower():
        raise RuntimeError(f"{portal}: still on the sign-in page after posting")
    return opener, landed, body


def probe(html):
    """Structure only: tag and class counts, table headers and row counts. No text values."""
    class _Probe(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags, self.classes, self.tables = {}, {}, []
            self._in_th = False
            self._cur = None
            self._heads = []

        def handle_starttag(self, tag, attrs):
            self.tags[tag] = self.tags.get(tag, 0) + 1
            for k, v in attrs:
                if k == "class" and v:
                    for c in v.split()[:3]:
                        self.classes[c] = self.classes.get(c, 0) + 1
            if tag == "table":
                self._cur = {"headers": [], "rows": 0}
                self.tables.append(self._cur)
            if tag == "tr" and self._cur is not None:
                self._cur["rows"] += 1
            if tag == "th":
                self._in_th = True

        def handle_endtag(self, tag):
            if tag == "th":
                self._in_th = False

        def handle_data(self, data):
            if self._in_th and self._cur is not None:
                t = data.strip()
                if t and len(self._cur["headers"]) < 20:
                    self._cur["headers"].append(t[:40])

    p = _Probe()
    p.feed(html)
    top_classes = sorted(p.classes.items(), key=lambda kv: -kv[1])[:15]
    return {
        "tag_counts": {k: v for k, v in p.tags.items() if k in ("table", "tr", "form", "input", "select", "a")},
        "top_classes": top_classes,
        "tables": [{"headers": t["headers"], "rows": t["rows"]} for t in p.tables[:10]],
    }


if __name__ == "__main__":
    import json
    portal = os.environ.get("PORTAL", "nyc")
    cfg = PORTALS[portal]
    user = os.environ.get(cfg["user_env"], "")
    password = os.environ.get(cfg["pass_env"], "")
    if not user or not password:
        raise SystemExit(f"{portal}: secrets {cfg['user_env']} / {cfg['pass_env']} are not set")
    _, landed, body = sign_in(portal, user, password)
    print(f"{portal}: signed in, landed on {urllib.parse.urlsplit(landed).path or '/'}")
    print(json.dumps(probe(body), indent=2))
