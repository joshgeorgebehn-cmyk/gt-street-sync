"""Daily email to joshua.b@gtstreet.au and will.k@gtstreet.au after every run: price changes, stock changes, held items, errors and the
independent verification result, with a small photo of each product.

Sending, in order of preference:
  1. Microsoft 365 (Graph API), secrets MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, MS_SENDER (default joshua.b@gtstreet.au)
  2. Any SMTP server, secrets SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS
A GitHub issue is also opened as a backup (GitHub emails the repo owner). price_changes.csv is attached to the run.
"""
import csv, datetime, html, json, os, re, smtplib, subprocess, urllib.parse, urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

TO = os.environ.get("EMAIL_TO") or "joshua.b@gtstreet.au,will.k@gtstreet.au"
SITE = "https://gtstreet.au/products/"
LOGO = "https://gtstreet.au/cdn/shop/files/gt-street-logo.png?v=1792649268&width=360"

# GT Street styling (from the live theme): near black, brand red, silver greys, Barlow.
INK, PANEL, RED, GREEN, AMBER = "#0A0A0B", "#121214", "#D0102B", "#1E8E3E", "#B26A00"
GREY, LIGHT, LINE, BG = "#6B6B75", "#F4F4F6", "#E4E4E8", "#ECECEF"
FONT = "Barlow,'Helvetica Neue',Helvetica,Arial,sans-serif"

def read(p):
    try: return open(p).read()
    except OSError: return ""

def thumb(url, w=96):
    if not url: return ""
    return url + ("&" if "?" in url else "?") + f"width={w}"

def money(x): return f"${float(x):,.2f}"
def esc(x): return html.escape(str(x))

try: r = json.load(open("report.json"))
except Exception: r = None
status = os.environ.get("JOB_STATUS", "unknown")
run_url = os.environ.get("RUN_URL", "")
owner = os.environ.get("REPO_OWNER", "")
local = datetime.datetime.now(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=11)))
now = local.strftime("%a %d %b %Y")
verify = read("verify.md")
vfail = "## Mismatches" in verify
vchecked = re.search(r"re-checked against the supplier: (\d+)", verify)
vbad = re.search(r"Mismatches: (\d+)", verify)

def detail(item):
    """The size or part detail, without the product name in front."""
    v, p = item.get("variant", ""), item.get("product", "")
    d = v[len(p):].strip() if p and v.startswith(p) else v
    return d.strip("() ")

def group(items, key):
    """Collapse rows that are the same product with the same change, e.g. 8 PCDs all going $344 to $358."""
    out = {}
    for it in items:
        k = (it.get("product") or it["variant"],) + key(it)
        g = out.setdefault(k, dict(it, details=[]))
        d = detail(it)
        if d and d not in g["details"]: g["details"].append(d)
    return list(out.values())

def pic(item):
    img = thumb(item.get("image"))
    if not img: return f'<div style="width:48px;height:48px;background:{LIGHT};border-radius:6px"></div>'
    return f'<img src="{esc(img)}" width="48" height="48" alt="" style="display:block;width:48px;height:48px;object-fit:contain;background:#fff;border:1px solid {LINE};border-radius:6px">'

def name_cell(item):
    title = item.get("product") or item["variant"]
    link = SITE + item["handle"] if item.get("handle") else ""
    t = f'<a href="{link}" style="color:{INK};text-decoration:none;font-weight:600">{esc(title)}</a>' if link else f'<b>{esc(title)}</b>'
    ds = item.get("details") or []
    sub = ""
    if len(ds) == 1:
        sub = f'<div style="color:{GREY};font-size:12px;margin-top:2px">{esc(ds[0])}</div>'
    elif ds:
        # Show what all the sizes share once (e.g. 18x7.5 +40), then only what differs (e.g. the PCDs).
        toks = [d.split() for d in ds]
        common = [t for t in toks[0] if all(t in x for x in toks[1:])]
        diff = [" ".join(t for t in x if t not in common) or " ".join(x) for x in toks]
        shown = ", ".join(diff[:8]) + (f" and {len(diff) - 8} more" if len(diff) > 8 else "")
        lead = (" ".join(common) + " in ") if common else ""
        sub = f'<div style="color:{GREY};font-size:12px;margin-top:2px">{esc(lead)}{len(ds)} sizes: {esc(shown)}</div>'
    return t + sub

def item_row(item, right):
    return (f'<tr><td style="padding:10px 12px 10px 0;border-bottom:1px solid {LINE};width:48px;vertical-align:top">{pic(item)}</td>'
            f'<td style="padding:10px 8px;border-bottom:1px solid {LINE};font-size:14px;line-height:1.35;vertical-align:top">{name_cell(item)}</td>'
            f'<td style="padding:10px 0 10px 8px;border-bottom:1px solid {LINE};font-size:14px;text-align:right;white-space:nowrap;vertical-align:top">{right}</td></tr>')

def pill(text, col):
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{col}1A;color:{col};font-size:12px;font-weight:700">{text}</span>'

def section(title, count_note, rows, note=""):
    if not rows: return ""
    return (f'<tr><td style="padding:28px 28px 0"><div style="font-size:13px;letter-spacing:1.5px;text-transform:uppercase;color:{RED};font-weight:700">{esc(title)}</div>'
            f'<div style="font-size:13px;color:{GREY};margin:4px 0 8px">{count_note}</div>'
            + (f'<div style="font-size:13px;color:{INK};background:{LIGHT};padding:10px 12px;border-radius:6px;margin-bottom:6px">{note}</div>' if note else "")
            + f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse">{"".join(rows)}</table></td></tr>')

def tile(value, label, col=INK):
    return (f'<td width="25%" style="padding:6px"><div style="background:{LIGHT};border-radius:8px;padding:14px 8px;text-align:center">'
            f'<div style="font-size:26px;font-weight:700;color:{col};line-height:1">{value}</div>'
            f'<div style="font-size:11px;color:{GREY};text-transform:uppercase;letter-spacing:1px;margin-top:6px">{label}</div></div></td>')

def shell(inner, ok, headline):
    col = GREEN if ok else RED
    return (f'<!doctype html><html><body style="margin:0;padding:0;background:{BG}">'
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{BG}"><tr><td align="center" style="padding:24px 12px">'
            f'<table role="presentation" width="640" cellspacing="0" cellpadding="0" style="width:100%;max-width:640px;background:#fff;border-radius:10px;overflow:hidden;font-family:{FONT};color:{INK}">'
            f'<tr><td style="background:{INK};padding:22px 28px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td><img src="{LOGO}" width="150" alt="GT Street" style="display:block;width:150px;height:auto"></td>'
            f'<td align="right" style="color:#A3A3AD;font-size:12px;letter-spacing:1px;text-transform:uppercase">Supplier sync<br><span style="color:#ECECEF;font-size:14px;letter-spacing:0">{now}</span></td>'
            f'</tr></table></td></tr>'
            f'<tr><td style="background:{col};height:4px;line-height:4px;font-size:0">&nbsp;</td></tr>'
            f'<tr><td style="padding:24px 28px 4px"><div style="font-size:22px;font-weight:700">{"&#10003; " if ok else "&#9888; "}{headline}</div></td></tr>'
            + inner +
            f'<tr><td style="padding:28px;font-size:12px;color:{GREY};line-height:1.6;border-top:1px solid {LINE}">'
            f'Prices are set at each supplier\'s live website price less $1. Stock shows 20 when the supplier has it, 0 when they do not. '
            f'Drops over 20% are never applied automatically. <a href="{run_url}" style="color:{RED}">Full report and downloads</a></td></tr>'
            f'</table></td></tr></table></body></html>')

if r is None:
    ok = False
    subject = f"GT Street sync FAILED {now}: no report produced"
    body = shell(f'<tr><td style="padding:8px 28px 24px;font-size:15px;line-height:1.5">The sync did not finish, so nothing was confirmed today. '
                 f'Prices and stock may not have been updated. <a href="{run_url}" style="color:{RED}">Open the run</a> to see why.</td></tr>', False, "Sync did not finish")
    text_summary = subject
else:
    ch = r.get("price_changes", [])
    with open("price_changes.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["variant", "old_price", "new_price", "change", "change_pct", "link"])
        for c in ch:
            old, new = float(c["from"]), float(c["to"])
            w.writerow([c["variant"], f"{old:.2f}", f"{new:.2f}", f"{new-old:+.2f}", f"{(new-old)/old*100:+.1f}" if old else "", SITE + c.get("handle", "")])
    sc = r.get("stock_changes", [])
    held, errs = r.get("held", []), r.get("errors", [])
    skipped = r.get("cnc_skipped_pages", [])
    ups = sum(1 for c in ch if float(c["to"]) > float(c["from"]))
    sold = sum(1 for s in sc if s["now"] == "Out of stock")
    ok = status == "success" and not vfail and not errs
    attention = bool(held or errs or vfail)
    subject = (f"GT Street sync {now}: " + ("all good" if ok and not held else "needs attention") +
               f", {len(ch)} price changes, {sold} sold out, {len(sc) - sold} back in stock")
    headline = "All checks passed" if ok and not held else ("Applied, a few items need you" if ok else "Needs attention")
    inner = []
    lead = (f"{len(ch)} price change{'s' if len(ch) != 1 else ''} and {len(sc)} stock change{'s' if len(sc) != 1 else ''} were applied to the site."
            if (ch or sc) else "Every supplier was checked and nothing needed changing.")
    if vchecked:
        lead += (f" An independent recheck of {vchecked.group(1)} random products against the supplier websites "
                 + (f'found <b style="color:{RED}">{vbad.group(1)} mismatch{"es" if vbad and vbad.group(1) != "1" else ""}</b>.' if vfail else "matched every one."))
    else:
        lead += f' <b style="color:{RED}">The independent recheck did not run.</b>'
    inner.append(f'<tr><td style="padding:6px 28px 14px;font-size:15px;line-height:1.5;color:#333">{lead}</td></tr>')
    inner.append(f'<tr><td style="padding:0 22px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
                 + tile(len(ch), "Price changes") + tile(sold, "Sold out", RED if sold else INK) + tile(len(sc) - sold, "Restocked", GREEN if len(sc) - sold else INK)
                 + tile(len(held) + len(errs), "Need you", RED if (held or errs) else INK) + '</tr></table></td></tr>')

    # 1. Things that need a person, first.
    if vfail:
        rows = [f'<tr><td style="padding:8px 0;border-bottom:1px solid {LINE};font-size:13px">{esc(x[2:])}</td></tr>'
                for x in verify.splitlines() if x.startswith("- ") and ("PRICE" in x or "STOCK" in x)]
        inner.append(section("Recheck mismatches", "The site does not match the supplier for these. Check them first.", rows))
    if errs:
        rows = [f'<tr><td style="padding:8px 0;border-bottom:1px solid {LINE};font-size:13px;color:#333">{esc(e if isinstance(e, str) else json.dumps(e))[:300]}</td></tr>' for e in errs[:20]]
        inner.append(section("Errors", f"{len(errs)} error{'s' if len(errs) != 1 else ''} during the run.", rows))
    if held:
        g = group(held, lambda h: (round(float(h["current"]), 2), round(float(h["cnc"]), 2)))
        rows = [item_row(h, f'<span style="color:{GREY}">site</span> {money(h["current"])}<br><span style="color:{GREY}">supplier</span> <b>{money(h["cnc"])}</b>') for h in g[:50]]
        inner.append(section("Held for your review", f"{len(held)} price{'s' if len(held) != 1 else ''} not changed.", rows,
                             "The supplier price would cut more than 20%, or fall under the minimum. If the supplier's page is right, update these by hand."))

    # 2. What changed.
    if ch:
        g = group(ch, lambda c: (round(float(c["from"]), 2), round(float(c["to"]), 2)))
        g.sort(key=lambda c: abs(float(c["to"]) - float(c["from"])), reverse=True)
        def pc(c):
            d = float(c["to"]) - float(c["from"]); col = GREEN if d > 0 else RED
            return (f'<span style="color:{GREY};text-decoration:line-through;font-size:13px">{money(c["from"])}</span> '
                    f'<b>{money(c["to"])}</b><br>{pill(("&#9650; " if d > 0 else "&#9660; ") + f"${abs(d):,.2f}", col)}')
        more = f" Showing the largest 80 groups; every change is in price_changes.csv on the run." if len(g) > 80 else ""
        inner.append(section("Price changes", f"{len(ch)} applied: {ups} up, {len(ch) - ups} down. Largest first.{more}", [item_row(c, pc(c)) for c in g[:80]]))
    if sc:
        g = group(sc, lambda s: (s["now"],))
        g.sort(key=lambda s: s["now"] != "Out of stock")
        rows = [item_row(s, pill(("Sold out" if s["now"] == "Out of stock" else "Back in stock"), RED if s["now"] == "Out of stock" else GREEN)) for s in g[:80]]
        inner.append(section("Stock changes", f"{sold} sold out, {len(sc) - sold} back in stock.", rows))

    # 3. Supplier check summary.
    srows = [f'<tr><td style="padding:9px 0;border-bottom:1px solid {LINE};font-size:14px;font-weight:600">CNC Wheels</td>'
             f'<td style="padding:9px 0;border-bottom:1px solid {LINE};font-size:13px;color:{GREY};text-align:right">{r.get("cnc_pages_read", 0):,} pages read live'
             + (f', {len(skipped)} skipped' if skipped else "") + "</td></tr>"]
    for n, s_ in r.get("suppliers", {}).items():
        state = f'<span style="color:{RED};font-weight:700">could not be read</span>' if s_.get("error") else f'{s_["variants_matched"]:,} checked' + (f', {len(s_["unmatched"])} not on their site' if s_["unmatched"] else "")
        srows.append(f'<tr><td style="padding:9px 0;border-bottom:1px solid {LINE};font-size:14px;font-weight:600">{esc(n)}</td>'
                     f'<td style="padding:9px 0;border-bottom:1px solid {LINE};font-size:13px;color:{GREY};text-align:right">{state}</td></tr>')
    inner.append(section("Suppliers checked", "Every price and stock level read directly from each supplier's own website.", srows))
    if skipped:
        rows = [f'<tr><td style="padding:7px 0;border-bottom:1px solid {LINE};font-size:12px"><a href="{esc(x["url"])}" style="color:{INK}">{esc(x["url"].rstrip("/").split("/")[-1].replace("-", " "))}</a>'
                f'<div style="color:{GREY}">{esc(x["why"])}</div></td></tr>' for x in skipped[:25]]
        inner.append(section("CNC pages skipped", f"{len(skipped)} left unchanged today; they are read again tomorrow.", rows))
    body = shell("".join(inner), ok and not held, headline)
    text_summary = subject

if os.environ.get("TEST_EMAIL"):
    subject = "TEST EMAIL (sample data, nothing changed): " + subject
sent = False
try:
    if os.environ.get("MS_CLIENT_ID") and os.environ.get("MS_CLIENT_SECRET") and os.environ.get("MS_TENANT_ID"):
        tok = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://login.microsoftonline.com/{os.environ['MS_TENANT_ID']}/oauth2/v2.0/token",
            data=urllib.parse.urlencode({"client_id": os.environ["MS_CLIENT_ID"], "client_secret": os.environ["MS_CLIENT_SECRET"],
                                         "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}).encode()), timeout=30).read())["access_token"]
        sender = os.environ.get("MS_SENDER") or "joshua.b@gtstreet.au"
        msg = {"message": {"subject": subject, "body": {"contentType": "HTML", "content": body},
                           "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in TO.split(",")]}, "saveToSentItems": False}
        urllib.request.urlopen(urllib.request.Request(f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail", data=json.dumps(msg).encode(),
                               headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}), timeout=30)
        sent = True; print("Email sent via Microsoft 365 to", TO)
    elif os.environ.get("SMTP_HOST"):
        m = MIMEMultipart("alternative"); m["Subject"] = subject; m["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]; m["To"] = TO
        m.attach(MIMEText(text_summary, "plain")); m.attach(MIMEText(body, "html"))
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587), timeout=30) as s:
            s.starttls(); s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.sendmail(m["From"], [a.strip() for a in TO.split(",")], m.as_string())
        sent = True; print("Email sent via SMTP to", TO)
    else:
        print("No email secrets set (MS_* or SMTP_*); email not sent.")
except Exception as e:
    detail = ""
    try: detail = e.read().decode()[:500]
    except Exception: pass
    print("EMAIL FAILED:", e, detail)

if os.environ.get("TEST_EMAIL"):
    raise SystemExit(0 if sent else "Test email was NOT sent; see the error above.")
# Backup only if the email failed: a GitHub issue (GitHub emails and notifies the repo owner). The repo is public,
# so issues are not opened on normal days.
if os.environ.get("GH_TOKEN") and not sent:
    note = "" if sent else "\n\n**The email was NOT sent. Check the email secrets.**"
    md = re.sub(r"<br>", " ", body); md = re.sub(r"<[^>]+>", " ", md); md = re.sub(r"[ \t]+", " ", md)
    issue = f"@{owner} {html.unescape(md)[:60000]}{note}\n\n{run_url}"
    label = "sync-report" if (ok and sent) else "sync-attention"
    cmd = ["gh", "issue", "create", "--title", subject, "--body", issue, "--label", label] + (["--assignee", owner] if owner else [])
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        subprocess.run(["gh", "label", "create", label, "--force"], capture_output=True)
        p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout, p.stderr)
    if label == "sync-report":  # close older OK reports; attention ones stay open until you close them
        new = re.search(r"/issues/(\d+)", p.stdout)
        out = subprocess.run(["gh", "issue", "list", "--label", "sync-report", "--state", "open", "--json", "number", "--limit", "50"], capture_output=True, text=True).stdout
        for i in json.loads(out or "[]"):
            if not new or str(i["number"]) != new.group(1): subprocess.run(["gh", "issue", "close", str(i["number"])], capture_output=True)

open("email.html", "w").write(body)
