"""GT Street store policies, kept in code.

The policies/*.md files are the source of truth for the Shopify policy pages (Contact, Shipping, Terms of service,
Privacy). The refund policy is lawyer approved and edited in Shopify, so it is checked here but never pushed.

  python3 policies.py render            print the HTML for each managed policy (no network)
  python3 policies.py push              update Shopify from policies/*.md (needs write_legal_policies scope)
  python3 policies.py check             compare Shopify with the files, check required clauses and dates,
                                        and list legal changes coming up; opens or updates a GitHub issue if
                                        anything needs attention (when run in GitHub Actions)

Every policy must end with "Version x.y. Last updated DD/MM/YYYY." and be reviewed at least every 12 months.
"""
import datetime as dt
import html
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
POLICY_DIR = HERE / "policies"
MAX_AGE_DAYS = 365

# Phrases each live policy must contain (lower case). Missing ones are flagged by check.
REQUIRED = {
    "TERMS_OF_SERVICE": ["guarantees that cannot be excluded under the australian consumer law",
                         "major failure", "gtstreet.au/apps/rewards/terms", "newsletter is never a condition",
                         "abn 82 692 485 868"],
    "PRIVACY_POLICY": ["australian privacy principles", "overseas", "automated decisions", "oaic",
                       "unsubscribe", "gt street rewards", "we do not sell personal information"],
    "SHIPPING_POLICY": ["full delivery cost is shown before you pay", "australian consumer law"],
    "REFUND_POLICY": ["australian consumer law", "major failure"],
    "CONTACT_INFORMATION": ["abn 82 692 485 868", "support@gtstreet.au", "0414 854 221"],
}

# Phrases that must never appear (old details, risky wording).
FORBIDDEN = ["sales@gtstreet.au", "continued use of the services constitutes acceptance",
             "you agree to indemnify", "have exclusive jurisdiction"]

# Dated legal changes to keep an eye on. Shown by check from 90 days before until 30 days after.
LEGAL_CALENDAR = [
    ("2026-12-10", "Privacy Act: automated decision disclosure (APP 1.7 to 1.9) starts. Privacy policy section "
                   "'Automated decisions' covers GT Street Rewards and fraud screening; confirm nothing new is automated."),
    ("2027-01-01", "GT Street Rewards opens. Promotion terms are served by the rewards app at /apps/rewards/terms."),
    ("2027-06-30", "GT Street Rewards closes. Turn off the theme rewards setting and the order email block after the reveal deadline."),
    ("2027-07-01", "Drip pricing ban starts (Treasury Laws Amendment (Unfair Trading Practices) Act). Every advertised "
                   "price must include all unavoidable fees; check product pages, ads and checkout surcharges."),
]


def inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def render(md: str) -> str:
    """Small Markdown renderer for the subset the policies use: #, ##, paragraphs, '- ' lists, bold and links."""
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S).strip()
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + "<br>".join(inline(x) for x in para) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            items.clear()

    for line in md.split("\n"):
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("# "):
            flush()  # the page title comes from Shopify, so the H1 is not repeated in the body
        elif s.startswith("## "):
            flush()
            out.append(f"<h2>{inline(s[3:])}</h2>")
        elif s.startswith("- "):
            if para:
                flush()
            items.append(s[2:])
        else:
            if items:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


def sources() -> dict:
    found = {}
    for f in sorted(POLICY_DIR.glob("*.md")):
        text = f.read_text()
        m = re.search(r"type:\s*(\w+)\s*\|\s*version:\s*([\d.]+)\s*\|\s*updated:\s*([\d/]+)", text)
        if not m:
            sys.exit(f"{f.name}: missing the header comment (type | version | updated)")
        found[m.group(1)] = {"file": f.name, "version": m.group(2), "updated": m.group(3), "html": render(text)}
    return found


def plain(h: str) -> str:
    t = re.sub(r"<[^>]+>", " ", h or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip().lower()


def gql(query, variables=None):
    sys.path.insert(0, str(HERE))
    import sync  # reuses the store, API version and credentials of the supplier sync
    return sync.gql(query, variables)


def live() -> dict:
    d = gql("{ shop { shopPolicies { type title url updatedAt body } } }")
    return {p["type"]: p for p in d["shop"]["shopPolicies"]}


def push():
    m = """mutation($p: ShopPolicyInput!) { shopPolicyUpdate(shopPolicy: $p) {
             shopPolicy { type updatedAt } userErrors { field message } } }"""
    for typ, s in sources().items():
        r = gql(m, {"p": {"type": typ, "body": s["html"]}})["shopPolicyUpdate"]
        if r["userErrors"]:
            sys.exit(f"{typ}: {r['userErrors']}")
        print(f"updated {typ} to version {s['version']} ({s['updated']})")


def check(today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    src, shop = sources(), live()
    problems = []
    for typ, p in shop.items():
        text = plain(p["body"])
        if typ in src and text != plain(src[typ]["html"]):
            problems.append(f"{typ}: the Shopify page differs from policies/{src[typ]['file']}. "
                            "Edit the file and run push, or copy the Shopify change into the file.")
        for phrase in REQUIRED.get(typ, []):
            if phrase not in text:
                problems.append(f"{typ}: missing required wording: '{phrase}'")
        for phrase in FORBIDDEN:
            if phrase in text:
                problems.append(f"{typ}: contains wording that must be removed: '{phrase}'")
        m = re.search(r"last updated (\d{2})/(\d{2})/(\d{4})", text)
        if not m:
            problems.append(f"{typ}: no 'Last updated DD/MM/YYYY' line")
        else:
            age = (today - dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))).days
            if age > MAX_AGE_DAYS:
                problems.append(f"{typ}: last updated {age} days ago; review it (limit {MAX_AGE_DAYS} days)")
    for typ in REQUIRED:
        if typ not in shop:
            problems.append(f"{typ}: no policy page published in Shopify")
    for day, note in LEGAL_CALENDAR:
        delta = (dt.date.fromisoformat(day) - today).days
        if -30 <= delta <= 90:
            when = dt.date.fromisoformat(day).strftime("%d/%m/%Y")
            problems.append(f"Coming up {when} ({delta} days): {note}" if delta >= 0 else f"Since {when}: {note}")
    return problems


def report(problems: list[str]):
    body = "Weekly policy check found:\n\n" + "\n".join(f"- {p}" for p in problems) + \
           "\n\nSource files: policies/*.md. Run `python3 policies.py push` after editing."
    print(body)
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    title = "Store policies need attention"
    found = subprocess.run(["gh", "issue", "list", "--state", "open", "--search", f"in:title {title}",
                            "--json", "number"], capture_output=True, text=True, check=True).stdout
    nums = [i["number"] for i in json.loads(found or "[]")]
    if nums:
        subprocess.run(["gh", "issue", "comment", str(nums[0]), "--body", body], check=True)
    else:
        subprocess.run(["gh", "issue", "create", "--title", title, "--body", body], check=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "render":
        for typ, s in sources().items():
            print(f"===== {typ} v{s['version']} ({s['updated']})\n{s['html']}\n")
    elif cmd == "push":
        push()
    elif cmd == "check":
        found = check()
        if found:
            report(found)
        else:
            print("All policies match their files, carry the required wording and are within 12 months.")
    else:
        sys.exit(__doc__)
