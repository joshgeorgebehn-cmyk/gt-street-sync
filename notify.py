"""Daily change digest. After every live run, opens a GitHub issue assigned to the repo owner, so GitHub emails
(and pushes to the GitHub mobile app) the day's price changes, stock changes, held items and errors.
Also writes price_changes.csv, attached to the run as a downloadable file. Dry runs only write the CSV."""
import csv, json, os, subprocess, datetime, re

def read(p):
    try: return open(p).read()
    except OSError: return ""

try: r = json.load(open("report.json"))
except Exception: r = None
status = os.environ.get("JOB_STATUS", "unknown")
dry = os.environ.get("DRY_RUN") == "1"
owner = os.environ.get("REPO_OWNER", "")
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=11))).strftime("%a %d %b %Y %H:%M")
verify = read("verify.md")
vfail = "## Mismatches" in verify

if r is None:
    title, body = f"Sync FAILED to produce a report ({now})", f"@{owner} The sync did not produce a report. Open the run: {os.environ.get('RUN_URL','')}"
    labels = "sync-failed"
else:
    ch = r.get("price_changes", [])
    with open("price_changes.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["variant", "old_price", "new_price", "change", "change_pct"])
        for c in ch:
            old, new = float(c["from"]), float(c["to"])
            w.writerow([c["variant"], f"{old:.2f}", f"{new:.2f}", f"{new-old:+.2f}", f"{(new-old)/old*100:+.1f}" if old else ""])
    ups = sum(1 for c in ch if float(c["to"]) > float(c["from"])); downs = len(ch) - ups
    ok = status == "success" and not vfail and not r.get("errors")
    title = f"{'OK' if ok else 'ATTENTION'}: sync {now}: {len(ch)} price changes, {len(r.get('sold_out', []))} sold out, {len(r.get('back_in_stock', []))} back in stock"
    L = [f"@{owner}", "", f"**Result: {'all checks passed' if ok else 'needs attention (see below)'}**. Workflow status: {status}. {os.environ.get('RUN_URL','')}", "",
         f"- Price changes: {len(ch)} ({ups} up, {downs} down)",
         f"- Sold out at supplier: {len(r.get('sold_out', []))}; back in stock: {len(r.get('back_in_stock', []))}",
         f"- Held for review: {len(r.get('held', []))}; errors: {len(r.get('errors', []))}",
         f"- CNC pages checked: {r.get('cnc_pages_read', 0)}, skipped: {r.get('cnc_pages_failed', 0)}"]
    for n, s in r.get("suppliers", {}).items():
        L.append(f"- {n}: {s['variants_matched']} variants matched, {len(s['unmatched'])} not found" + (f", ERROR {s['error']}" if s.get("error") else ""))
    if verify:
        v = [x for x in verify.splitlines() if x.strip()][:12]
        L += ["", "**Independent verification**"] + [("- " + x.lstrip("-# ")) for x in v]
    if r.get("held"):
        L += ["", "**Held for review (price not changed)**"] + [f"- {h['variant']}: now ${h['current']:.2f}, supplier ${h['cnc']:.2f}" for h in r["held"][:40]]
    if r.get("errors"):
        L += ["", "**Errors**"] + [f"- {json.dumps(e)[:250]}" for e in r["errors"][:15]]
    big = sorted(ch, key=lambda c: abs(float(c["to"]) - float(c["from"])), reverse=True)[:60]
    if big:
        L += ["", f"**Largest price changes (top {len(big)} of {len(ch)}; full list in price_changes.csv on the run)**", "", "| Variant | Old | New | Change |", "|---|---|---|---|"]
        L += [f"| {c['variant']} | ${float(c['from']):.2f} | ${float(c['to']):.2f} | {float(c['to'])-float(c['from']):+.2f} |" for c in big]
    body = "\n".join(L)[:60000]; labels = "sync-report" if ok else "sync-attention"

if dry or not os.environ.get("GH_TOKEN"):
    print("Dry run or no token: digest not posted.\n", title); raise SystemExit(0)
cmd = ["gh", "issue", "create", "--title", title, "--body", body, "--label", labels]
if owner: cmd += ["--assignee", owner]
p = subprocess.run(cmd, capture_output=True, text=True)
if p.returncode:  # labels may not exist yet
    for lb in labels.split(","): subprocess.run(["gh", "label", "create", lb, "--force"], capture_output=True)
    p = subprocess.run(cmd, capture_output=True, text=True)
print(p.stdout, p.stderr)
# Close yesterday's OK digests so the issue list stays clean (attention ones stay open until you close them).
if r is not None and labels == "sync-report":
    out = subprocess.run(["gh", "issue", "list", "--label", "sync-report", "--state", "open", "--json", "number", "--limit", "50"], capture_output=True, text=True).stdout
    new = re.search(r"/issues/(\d+)", p.stdout)
    for i in json.loads(out or "[]"):
        if not new or str(i["number"]) != new.group(1): subprocess.run(["gh", "issue", "close", str(i["number"])], capture_output=True)
