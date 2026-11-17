"""Daily email to sales@gtstreet.au after every run: price changes, stock changes, held items, errors and the
independent verification result, with a small photo of each product.

Sending, in order of preference:
  1. Microsoft 365 (Graph API), secrets MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, MS_SENDER (default sales@gtstreet.au)
  2. Any SMTP server, secrets SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS
A GitHub issue is also opened as a backup (GitHub emails the repo owner). price_changes.csv is attached to the run.
"""
import csv, datetime, html, json, os, re, smtplib, subprocess, urllib.parse, urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

TO = os.environ.get("EMAIL_TO") or "sales@gtstreet.au"
SITE = "https://gtstreet.au/products/"

def read(p):
    try: return open(p).read()
    except OSError: return ""

def thumb(url, w=64):
    if not url: return ""
    return url + ("&" if "?" in url else "?") + f"width={w}"

def money(x): return f"${float(x):,.2f}"

try: r = json.load(open("report.json"))
except Exception: r = None
status = os.environ.get("JOB_STATUS", "unknown")
run_url = os.environ.get("RUN_URL", "")
owner = os.environ.get("REPO_OWNER", "")
now = datetime.datetime.now(datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=11))).strftime("%a %d %b %Y")
verify = read("verify.md")
vfail = "## Mismatches" in verify
vchecked = re.search(r"re-checked against the supplier: (\d+)", verify)
vbad = re.search(r"Mismatches: (\d+)", verify)

def row(item, right):
    img = thumb(item.get("image"))
    link = SITE + item["handle"] if item.get("handle") else ""
    pic = f'<img src="{html.escape(img)}" width="48" height="48" style="object-fit:contain;border:1px solid #eee;border-radius:4px" alt="">' if img else ""
    name = html.escape(item["variant"])
    name = f'<a href="{link}" style="color:#111;text-decoration:none">{name}</a>' if link else name
    return f'<tr><td style="padding:4px 8px 4px 0">{pic}</td><td style="padding:4px 8px;font-size:13px">{name}</td><td style="padding:4px 0;font-size:13px;white-space:nowrap;text-align:right">{right}</td></tr>'

def table(title, rows, note=""):
    if not rows: return ""
    return (f'<h3 style="font-size:15px;margin:22px 0 6px">{title}</h3>' + (f'<p style="font-size:12px;color:#666;margin:0 0 6px">{note}</p>' if note else "") +
            '<table style="border-collapse:collapse;width:100%">' + "".join(rows) + "</table>")

if r is None:
    ok = False
    subject = f"GT Street sync FAILED {now}: no report produced"
    body = f'<p>The daily sync did not finish and produced no report. Nothing was confirmed today. Open the run: <a href="{run_url}">{run_url}</a></p>'
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
    ups = sum(1 for c in ch if float(c["to"]) > float(c["from"]))
    ok = status == "success" and not vfail and not errs
    subject = (f"{'GT Street sync OK' if ok else 'GT Street sync NEEDS ATTENTION'} {now}: "
               f"{len(ch)} price changes, {sum(1 for s in sc if s['now'] == 'Out of stock')} sold out, {sum(1 for s in sc if s['now'] == 'In stock')} back in stock")
    sup = "".join(f"<li>{html.escape(n)}: {s['variants_matched']} variants checked, {len(s['unmatched'])} not found on their site"
                  + (f' <b style="color:#b00">ERROR: {html.escape(str(s["error"]))}</b>' if s.get("error") else "") + "</li>"
                  for n, s in r.get("suppliers", {}).items())
    banner_col = "#1a7f37" if ok else "#b42318"
    body = [f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:720px;color:#111">',
            f'<div style="background:{banner_col};color:#fff;padding:12px 16px;border-radius:6px;font-size:16px"><b>{"All checks passed" if ok else "Needs attention, see below"}</b></div>',
            '<ul style="font-size:13px;line-height:1.6">',
            f"<li>CNC Wheels: {r.get('cnc_pages_read', 0)} pages read live, {r.get('cnc_pages_failed', 0)} skipped (left unchanged)</li>", sup,
            f"<li>Price changes applied: {len(ch)} ({ups} up, {len(ch) - ups} down)</li>",
            f"<li>Stock changes applied: {len(sc)}</li>",
            f"<li>Held for review (not changed): {len(held)}</li>",
            f"<li>Errors: {len(errs)}</li>",
            (f"<li>Independent re-check of {vchecked.group(1)} random products against the suppliers' websites: "
             + (f'<b style="color:#b00">{vbad.group(1)} mismatches</b>' if vfail else "all matched") + "</li>") if vchecked else
            '<li><b style="color:#b00">Independent re-check did not run</b></li>',
            "</ul>"]
    if vfail:
        body.append('<h3 style="font-size:15px;color:#b00">Re-check mismatches</h3><ul style="font-size:13px">' +
                    "".join(f"<li>{html.escape(x[2:])}</li>" for x in verify.splitlines() if x.startswith("- ") and ("PRICE" in x or "STOCK" in x)) + "</ul>")
    if errs:
        body.append('<h3 style="font-size:15px;color:#b00">Errors</h3><ul style="font-size:12px">' + "".join(f"<li>{html.escape(json.dumps(e)[:300])}</li>" for e in errs[:20]) + "</ul>")
    body.append(table("Held for review (price NOT changed)", [row(h, f"now {money(h['current'])}<br>supplier {money(h['cnc'])}") for h in held[:50]],
                      "Price would drop more than 20% or fall below the minimum. Check the supplier's page and change by hand if correct."))
    big = sorted(ch, key=lambda c: abs(float(c["to"]) - float(c["from"])), reverse=True)
    body.append(table(f"Price changes ({len(ch)})", [row(c, f"{money(c['from'])} → <b>{money(c['to'])}</b><br><span style='color:{'#1a7f37' if float(c['to']) > float(c['from']) else '#b42318'}'>{float(c['to'])-float(c['from']):+,.2f}</span>") for c in big[:150]],
                      "Largest first." + (f" Showing 150 of {len(ch)}; the full list is in price_changes.csv on the run." if len(ch) > 150 else "")))
    body.append(table(f"Stock changes ({len(sc)})", [row(s, f"<b style='color:{'#b42318' if s['now'] == 'Out of stock' else '#1a7f37'}'>{s['now']}</b>") for s in sc[:150]]))
    skipped = r.get("cnc_skipped_pages", [])
    if skipped:
        body.append(f'<h3 style="font-size:15px;margin:22px 0 6px">CNC pages skipped ({len(skipped)}), left unchanged</h3><ul style="font-size:12px">' +
                    "".join(f'<li><a href="{html.escape(x["url"])}">{html.escape(x["url"].rstrip("/").split("/")[-1])}</a>: {html.escape(x["why"])}</li>' for x in skipped[:40]) + "</ul>")
    body.append(f'<p style="font-size:12px;color:#666;margin-top:24px">Full report: <a href="{run_url}">{run_url}</a></p></div>')
    body = "".join(body)
    text_summary = subject

sent = False
try:
    if os.environ.get("MS_CLIENT_ID") and os.environ.get("MS_CLIENT_SECRET") and os.environ.get("MS_TENANT_ID"):
        tok = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://login.microsoftonline.com/{os.environ['MS_TENANT_ID']}/oauth2/v2.0/token",
            data=urllib.parse.urlencode({"client_id": os.environ["MS_CLIENT_ID"], "client_secret": os.environ["MS_CLIENT_SECRET"],
                                         "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}).encode()), timeout=30).read())["access_token"]
        sender = os.environ.get("MS_SENDER") or TO
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
    print("EMAIL FAILED:", e)

# Backup: GitHub issue (GitHub emails and notifies the repo owner).
if os.environ.get("GH_TOKEN"):
    note = "" if sent else "\n\n**The email to sales@gtstreet.au was NOT sent. Check the email secrets.**"
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

open("email.html", "w").write(f"<h2>{html.escape(subject)}</h2>" + body)
