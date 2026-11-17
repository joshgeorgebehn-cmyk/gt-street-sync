"""Masked page skeletons: tags and classes kept, every letter in text replaced with x and every digit with 9.
Shows the layout of a product row without revealing any product, price or stock value."""
import re
from html.parser import HTMLParser

class _Sk(HTMLParser):
    def __init__(self, limit):
        super().__init__(); self.out = []; self.limit = limit; self.depth = 0; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "svg"): self.skip += 1; return
        if self.skip or len(self.out) >= self.limit: return
        a = dict(attrs)
        cls = (a.get("class") or "").split()[:3]
        extra = "".join(f" {k}" for k in ("data-label", "data-title", "title") if k in a)
        self.out.append("  " * min(self.depth, 12) + f"<{tag}{'.' + '.'.join(cls) if cls else ''}{extra}>")
        if tag not in ("br", "img", "input", "meta", "link", "hr"): self.depth += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "svg"): self.skip = max(0, self.skip - 1); return
        if tag not in ("br", "img", "input", "meta", "link", "hr"): self.depth = max(0, self.depth - 1)
    def handle_data(self, d):
        if self.skip or len(self.out) >= self.limit: return
        t = d.strip()
        if t:
            masked = re.sub(r"[A-Za-z]", "x", re.sub(r"\d", "9", t))[:40]
            self.out.append("  " * min(self.depth, 12) + "  " + masked)

def skeleton(html, start_pattern=None, limit=120):
    if start_pattern:
        m = re.search(start_pattern, html, re.I)
        if m: html = html[m.start():]
    p = _Sk(limit); p.feed(html)
    return "\n".join(p.out)
