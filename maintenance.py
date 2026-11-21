"""
Description upkeep that has to follow prices and variants, run after every sync.

1. BC Racing: descriptions used to carry a price table ("V1-VM ... $1,549") and say kits are "sold at BC Racing
   Australia's recommended retail price". The sync now prices each kit from BC Racing's site every day, so a
   price written into the text goes stale. The Price column and that phrase are removed; the live price is the
   one shown on the page.
2. Wheels (GT Form, JSR, Black Rock): the "Available rim sizes / PCDs / offsets" lines are rewritten from the
   product's actual variants, so the text never lists a size that can't be bought.

3. Wheels: CNC has no product description field, only a short SEO snippet. Its first sentence, when clean and not about one
   size, is kept as the opening paragraph (class "cnc-intro") of the wheel description and refreshed every run. Written only when
   CNC_DESCRIPTIONS_APPLY=1; a dry run (or the switch off) reports what would be written.

Idempotent: a description is only written when it would change. DRY_RUN=1 reports only.
"""
import re, json, html, os, time
import concurrent.futures as cf
from sync import gql, DRY_RUN, cnc_index, cnc_intro, http, norm, ALIAS, CNC_DELAY

Q = """query($c:String,$q:String){ products(first:50, after:$c, query:$q){ pageInfo{hasNextPage endCursor}
  nodes{ id title descriptionHtml options{ name values } } } }"""
M = """mutation($p:ProductUpdateInput!){ productUpdate(product:$p){ userErrors{ field message } } }"""


def products(query):
    c = None
    while True:
        d = gql(Q, {"c": c, "q": query})["products"]
        yield from d["nodes"]
        if not d["pageInfo"]["hasNextPage"]:
            return
        c = d["pageInfo"]["endCursor"]


def fix_bc(body):
    b = body
    # drop the Price header cell and any cell that is only a dollar amount
    b = re.sub(r"\s*<th>\s*Price\s*</th>", "", b, flags=re.I)
    b = re.sub(r"\s*<td>\s*\$\s?[\d,]+(?:\.\d{1,2})?\s*</td>", "", b)
    b = re.sub(r"sold at BC Racing Australia(?:'|&#39;|’|&rsquo;)s recommended retail price,\s*", "", b)
    return b


def fix_wheel(body, options):
    vals = {o["name"].lower(): o["values"] for o in options}
    b = body
    for label, key in (("Available rim sizes", "rim size"), ("Available PCDs", "pcd"), ("Available offsets", "offset")):
        if vals.get(key):
            b = re.sub(rf"(<strong>{label}:</strong>)\s*[^<]*", lambda m: f"{m.group(1)} {', '.join(vals[key])}", b)
    return b


INTRO_APPLY = os.environ.get("CNC_DESCRIPTIONS_APPLY", "0") == "1"
INTRO_RE = re.compile(r'<p class="cnc-intro">.*?</p>\s*', re.S)


def intro_pass():
    """Returns (lines, errors) for the CNC opening sentence on every wheel description."""
    idx = cnc_index()
    first = {}
    for k, u in sorted(idx.items()):
        first.setdefault(k[0], u)
    prods = list(products('vendor:"GT Form" OR vendor:"JSR" OR vendor:"Black Rock"'))
    by_range = {}
    for p in prods:
        k = norm(p["title"])
        r = next((x for x in ALIAS.get(k, [k]) if x in first), None)
        if r:
            by_range.setdefault(r, []).append(p)

    def get(r):
        st, t = http(first[r]); time.sleep(CNC_DELAY)
        return r, (cnc_intro(t) if st == 200 and t else None)  # None = page unreadable, leave descriptions alone

    intros = {}
    with cf.ThreadPoolExecutor(4) as ex:
        for r, i in ex.map(get, list(by_range)):
            intros[r] = i
    todo, errors, samples, with_intro = 0, [], [], 0
    for r, ps in by_range.items():
        intro = intros[r]
        if intro is None:
            continue
        with_intro += bool(intro)
        for p in ps:
            cur = p["descriptionHtml"] or ""
            base = INTRO_RE.sub("", cur)
            new = (f'<p class="cnc-intro">{html.escape(intro)}</p>' + base) if intro else base
            if new == cur:
                continue
            todo += 1
            if len(samples) < 12:
                samples.append(f"- {p['title']}: {intro or '(no clean CNC sentence, intro removed)'}")
            if INTRO_APPLY and not DRY_RUN:
                errs = gql(M, {"p": {"id": p["id"], "descriptionHtml": new}})["productUpdate"]["userErrors"]
                if errs:
                    errors.append({"product": p["title"], "errors": errs})
    unreadable = sum(1 for i in intros.values() if i is None)
    lines = ["", "## CNC opening sentence", "",
             f"- Wheel products matched to a CNC range: {sum(len(v) for v in by_range.values())} ({len(by_range)} ranges)",
             f"- Ranges with a clean CNC sentence: {with_intro}; without one: {len(by_range) - with_intro - unreadable}; CNC page unreadable: {unreadable}",
             f"- Descriptions {'written' if INTRO_APPLY and not DRY_RUN else 'that would be written'}: {todo}"
             + ("" if INTRO_APPLY or DRY_RUN else " (switch CNC_DESCRIPTIONS_APPLY is off, nothing written)")] + (["", "Examples:"] + samples if samples else [])
    return lines, errors


def main():
    lines = ["# Description upkeep", "", f"- Mode: {'DRY RUN (nothing changed)' if DRY_RUN else 'LIVE'}"]
    changed, errors = {"BC Racing": 0, "Wheels": 0}, []
    jobs = [("BC Racing", 'vendor:"BC Racing"', lambda p: fix_bc(p["descriptionHtml"] or "")),
            ("Wheels", 'vendor:"GT Form" OR vendor:"JSR" OR vendor:"Black Rock"',
             lambda p: fix_wheel(p["descriptionHtml"] or "", p["options"]))]
    for name, q, fn in jobs:
        for p in products(q):
            new = fn(p)
            if new == (p["descriptionHtml"] or ""):
                continue
            changed[name] += 1
            if DRY_RUN:
                continue
            errs = gql(M, {"p": {"id": p["id"], "descriptionHtml": new}})["productUpdate"]["userErrors"]
            if errs:
                errors.append({"product": p["title"], "errors": errs})
    lines += [f"- {k} descriptions updated: {v}" for k, v in changed.items()]
    try:
        l2, e2 = intro_pass()
        lines += l2; errors += e2
    except Exception as ex:
        errors.append({"cnc_intro": str(ex)[:300]})
    if errors:
        lines += ["", "## Errors"] + [f"- {json.dumps(e)[:300]}" for e in errors[:30]]
    out = "\n".join(lines) + "\n"
    print(out)
    with open("maintenance.md", "w") as f:
        f.write(out)


if __name__ == "__main__":
    main()
