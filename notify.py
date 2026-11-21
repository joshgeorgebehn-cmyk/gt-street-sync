"""GT Street Daily Digest: one email a day summarising the supplier checks since the last digest.

Interface (the only one): DIGEST_MANIFEST points to manifest.json written by digest.py collect. Each check in it names
the GitHub run, its state as GitHub reported it, and the folder its sync-report artifact was downloaded to. Nothing
else is read; there is no fallback to a stray report.json.

Modes (DIGEST_MODE):
  live     build, validate, send to EMAIL_TO (default)
  preview  build only, write email.html and price_changes.csv, send nothing
  test     sample data (tests/fixtures), subject starts "TEST EMAIL", sent to EMAIL_TO. Never touches Shopify.

Delivery: Microsoft 365 Graph first (MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, MS_SENDER), SMTP second
(SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM). price_changes.csv is attached to either. If neither sends,
a GitHub issue is opened (or the open one for the same day is commented on) and the script exits 2 so the workflow
goes red. delivery.json records exactly what happened.
"""
from __future__ import annotations

import base64, csv, datetime, html, io, json, os, re, smtplib, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from zoneinfo import ZoneInfo

SYD = ZoneInfo("Australia/Sydney")
DEFAULT_TO = "joshua.b@gtstreet.au,will.k@gtstreet.au,billy@breadenmedia.com.au"
SITE = "https://gtstreet.au/products/"
LOGO = "https://gtstreet.au/cdn/shop/files/gt-street-logo.png?v=1792649268&width=360"
SAFE_HOSTS = ("gtstreet.au", "cdn.shopify.com", "github.com", "cncwheels.com.au", "xforce.com.au", "bcracing.com.au",
              "alu-cab.com.au", "revzone.com.au")
REQUIRED = {"price_changes": list, "stock_changes": (list, type(None)), "errors": list, "suppliers": dict}

# GT Street styling (from the live theme): near black, brand red, silver greys, Barlow.
INK, RED, GREEN, AMBER = "#0A0A0B", "#D0102B", "#1E8E3E", "#B26A00"
GREY, LIGHT, LINE, BG = "#6B6B75", "#F4F4F6", "#E4E4E8", "#ECECEF"
FONT = "Barlow,'Helvetica Neue',Helvetica,Arial,sans-serif"


# ---------------------------------------------------------------- small helpers
def esc(x: Any) -> str:
    return html.escape(str(x), quote=True)


def num(x: Any) -> float | None:
    try:
        f = float(x)
        return f if f == f and abs(f) < 1e9 else None
    except (TypeError, ValueError):
        return None


def money(x: Any) -> str:
    f = num(x)
    return f"${f:,.2f}" if f is not None else "n/a"


def safe_url(u: Any) -> str:
    """https links to known hosts only; anything else is dropped rather than rendered."""
    if not isinstance(u, str) or not u:
        return ""
    p = urllib.parse.urlparse(u)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not any(host == h or host.endswith("." + h) for h in SAFE_HOSTS):
        return ""
    return u


def product_url(item: dict) -> str:
    h = item.get("handle")
    return SITE + urllib.parse.quote(h, safe="-_") if isinstance(h, str) and h else ""


def thumb(url: Any, w: int = 96) -> str:
    u = safe_url(url)
    return (u + ("&" if "?" in u else "?") + f"width={w}") if u else ""


def vkey(c: dict) -> str:
    return str(c.get("id") or c.get("variant") or "")


# ---------------------------------------------------------------- loading and validating
def validate_report(rep: Any, run_id: str) -> list[str]:
    """Problems with a report; an empty list means it can be trusted."""
    if not isinstance(rep, dict):
        return ["report.json is not a JSON object"]
    probs = []
    for k, t in REQUIRED.items():
        if k not in rep and k != "stock_changes":
            probs.append(f"report.json has no '{k}'")
        elif k in rep and not isinstance(rep[k], t):
            probs.append(f"report.json '{k}' has the wrong type")
    if "complete" in rep and rep["complete"] is not True:
        probs.append("report.json says the run did not complete")
    if "finished" not in rep and "complete" not in rep:
        probs.append("report.json has no finish time, so the run did not complete")
    if rep.get("run_id") and run_id and str(rep["run_id"]) != str(run_id):
        probs.append(f"report.json belongs to run {rep['run_id']}, not {run_id}")
    if rep.get("dry_run"):
        probs.append("report.json is from a dry run (nothing was applied)")
    for c in rep.get("price_changes", []) if isinstance(rep.get("price_changes"), list) else []:
        if not isinstance(c, dict) or num(c.get("from")) is None or num(c.get("to")) is None:
            probs.append("a price change has a missing or malformed price"); break
    return probs


def load_verify(d: str) -> dict:
    """Structured result from verify.json; older runs only have verify.md, which is parsed as a fallback."""
    p = os.path.join(d, "verify.json")
    if os.path.exists(p):
        try:
            v = json.load(open(p))
            if isinstance(v, dict) and isinstance(v.get("rechecked"), int) and isinstance(v.get("mismatches"), list):
                return v
            return {"ran": False, "problem": "verify.json is malformed"}
        except (OSError, ValueError):
            return {"ran": False, "problem": "verify.json could not be read"}
    try:
        md = open(os.path.join(d, "verify.md")).read()
    except OSError:
        return {"ran": False, "problem": "the independent recheck did not run (no verify result)"}
    m = re.search(r"re-checked against the supplier: (\d+)", md)
    if not m:
        return {"ran": False, "problem": "verify.md did not contain a result"}
    a = re.search(r"(?:Of which amended this run|Amended this run): (\d+)", md)
    return {"ran": True, "rechecked": int(m.group(1)), "amended": int(a.group(1)) if a else None, "sampled": None,
            "mismatches": [l[2:] for l in md.splitlines() if l.startswith("- ") and (l[2:].startswith(("PRICE", "STOCK")))]}


def active_notices(today: str) -> list[dict]:
    """Dated notices (notices.json) that apply to today's digest. Shown at the top of the email while they run."""
    try:
        items = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "notices.json")))
    except (OSError, ValueError):
        return []
    return [n for n in items if isinstance(n, dict) and str(n.get("from", "")) <= today <= str(n.get("to", "9999"))]


def load_checks(manifest: dict) -> list[dict]:
    out = []
    for c in manifest.get("checks", []):
        chk = dict(c, report=None, verify={"ran": False, "problem": "no report"}, problems=[])
        if c.get("state") in ("missing", "running", "timed_out", "cancelled"):
            chk["problems"].append({"missing": "this check never ran", "running": "this check was still running when the digest was sent",
                                    "timed_out": "this check ran past its time limit", "cancelled": "this check was cancelled"}[c["state"]])
            out.append(chk); continue
        d = c.get("dir") or ""
        try:
            rep = json.load(open(os.path.join(d, "report.json")))
        except FileNotFoundError:
            rep = None; chk["problems"].append("its report was not available")
        except (OSError, ValueError):
            rep = None; chk["problems"].append("its report was corrupted")
        if rep is not None:
            probs = validate_report(rep, str(c.get("run_id", "")))
            chk["problems"] += probs
            if not probs:
                rep.setdefault("stock_changes", [])
                chk["report"] = rep
        try:
            chk["portals"] = json.load(open(os.path.join(d, "portals.json")))
        except (OSError, ValueError):
            chk["portals"] = None
        if chk["report"] is not None:
            chk["verify"] = load_verify(d)
            if not chk["verify"].get("ran"):
                chk["problems"].append(chk["verify"].get("problem", "the independent recheck did not run"))
        if c.get("conclusion") not in (None, "", "success") and c.get("state") != "missing":
            chk["problems"].append(f"GitHub reported the run as {c['conclusion']}")
        out.append(chk)
    return out


# ---------------------------------------------------------------- consolidation
def consolidate(checks: list[dict]) -> dict:
    """Combine every readable check, oldest first. A variant changed twice shows once: first 'from', last 'to'."""
    good = [c for c in checks if c["report"]]
    pc: dict[str, dict] = {}; sc: dict[str, dict] = {}; held: dict[str, dict] = {}; nv: dict[str, dict] = {}; dc: dict[str, dict] = {}
    errors, mism, suppliers, skipped = [], [], [], []
    for c in good:
        r, lab = c["report"], c["label"]
        for x in r.get("price_changes", []):
            k = vkey(x)
            pc[k] = dict(x, **{"from": pc[k]["from"]}) if k in pc else dict(x)
        for x in r.get("stock_changes", []):
            k = vkey(x)
            sc[k] = dict(x, prev=sc[k].get("prev")) if k in sc else dict(x)
        for x in r.get("new_variants", []):
            nv[vkey(x)] = dict(x)
        for x in r.get("discontinued", []):
            dc[vkey(x)] = dict(x)
        errors += [f"{lab}: " + (e if isinstance(e, str) else json.dumps(e)) for e in r.get("errors", [])]
        mism += [f"{lab}: {m}" for m in c["verify"].get("mismatches", [])]
        skipped += r.get("cnc_skipped_pages", []) or []
        suppliers.append((lab, r))
    # Held is a current state, so it comes from the latest check only (an earlier hold may since have been applied).
    if good:
        held = {vkey(x): dict(x) for x in good[-1]["report"].get("held", [])}
    prices = [x for x in pc.values() if abs(num(x["to"]) - num(x["from"])) >= 0.01]
    stock = [x for x in sc.values() if not x.get("prev") or x.get("prev") != x.get("state")]
    big = [x for x in prices if num(x["from"]) > 0 and num(x["to"]) < num(x["from"]) * 0.8]
    deferred = good[-1]["report"].get("new_variants_deferred") if good else None
    return {"prices": prices, "stock": stock, "big": big, "held": list(held.values()), "new": list(nv.values()),
            "disc": list(dc.values()), "errors": errors, "mismatches": mism, "suppliers": suppliers,
            "skipped": skipped, "deferred": deferred, "good": good}


def write_csv(prices: list[dict], path: str = "price_changes.csv") -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["product", "variant", "old_price", "new_price", "change", "change_pct", "link"])
    for c in sorted(prices, key=lambda c: abs(num(c["to"]) - num(c["from"])), reverse=True):
        old, new = num(c["from"]), num(c["to"])
        w.writerow([c.get("product", ""), c.get("variant", ""), f"{old:.2f}", f"{new:.2f}", f"{new - old:+.2f}",
                    f"{(new - old) / old * 100:+.1f}" if old else "", product_url(c)])
    data = buf.getvalue().encode()
    open(path, "wb").write(data)
    return data


# ---------------------------------------------------------------- HTML (table based, inline styles, works in Outlook)
def detail(item: dict) -> str:
    v, p = str(item.get("variant", "")), str(item.get("product", ""))
    d = v[len(p):].strip() if p and v.startswith(p) else v
    return d.strip("() ")


def group(items: list[dict], key) -> list[dict]:
    """Collapse rows that are the same product with the same change, e.g. 8 PCDs all going $344 to $358."""
    out: dict[tuple, dict] = {}
    for it in items:
        k = (it.get("product") or it.get("variant"),) + key(it)
        g = out.setdefault(k, dict(it, details=[], n=0))
        g["n"] += 1
        d = detail(it)
        if d and d not in g["details"]:
            g["details"].append(d)
    return list(out.values())


def pic(item: dict) -> str:
    img = thumb(item.get("image"))
    if not img:
        return f'<div style="width:48px;height:48px;background:{LIGHT};border-radius:6px"></div>'
    return (f'<img src="{esc(img)}" width="48" height="48" alt="" style="display:block;width:48px;height:48px;'
            f'object-fit:contain;background:#fff;border:1px solid {LINE};border-radius:6px">')


def name_cell(item: dict) -> str:
    title = item.get("product") or item.get("variant") or "Unnamed product"
    link = product_url(item)
    t = (f'<a href="{esc(link)}" style="color:{INK};text-decoration:none;font-weight:600">{esc(title)}</a>' if link
         else f'<b>{esc(title)}</b>')
    ds = item.get("details") or []
    if len(ds) == 1:
        return t + f'<div style="color:{GREY};font-size:12px;margin-top:2px">{esc(ds[0])}</div>'
    if ds:
        toks = [d.split() for d in ds]
        common = [x for x in toks[0] if all(x in y for y in toks[1:])]
        diff = [" ".join(x for x in y if x not in common) or " ".join(y) for y in toks]
        shown = ", ".join(diff[:8]) + (f" and {len(diff) - 8} more" if len(diff) > 8 else "")
        lead = (" ".join(common) + " in ") if common else ""
        return t + f'<div style="color:{GREY};font-size:12px;margin-top:2px">{esc(lead)}{len(ds)} sizes: {esc(shown)}</div>'
    return t


def item_row(item: dict, right: str) -> str:
    return (f'<tr><td style="padding:10px 12px 10px 0;border-bottom:1px solid {LINE};width:48px;vertical-align:top">{pic(item)}</td>'
            f'<td style="padding:10px 8px;border-bottom:1px solid {LINE};font-size:14px;line-height:1.35;vertical-align:top">{name_cell(item)}</td>'
            f'<td style="padding:10px 0 10px 8px;border-bottom:1px solid {LINE};font-size:14px;text-align:right;white-space:nowrap;vertical-align:top">{right}</td></tr>')


def text_row(txt: str, col: str = "#333") -> str:
    return f'<tr><td style="padding:8px 0;border-bottom:1px solid {LINE};font-size:13px;color:{col}">{esc(txt)[:400]}</td></tr>'


def pair_row(left: str, right: str) -> str:
    return (f'<tr><td style="padding:9px 0;border-bottom:1px solid {LINE};font-size:14px;font-weight:600">{left}</td>'
            f'<td style="padding:9px 0 9px 12px;border-bottom:1px solid {LINE};font-size:13px;color:{GREY};text-align:right">{right}</td></tr>')


def pill(text: str, col: str) -> str:
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{col}1A;color:{col};font-size:12px;font-weight:700">{text}</span>'


def section(title: str, note: str, rows: list[str], callout: str = "") -> str:
    if not rows:
        return ""
    return (f'<tr><td style="padding:28px 28px 0"><div style="font-size:13px;letter-spacing:1.5px;text-transform:uppercase;color:{RED};font-weight:700">{esc(title)}</div>'
            f'<div style="font-size:13px;color:{GREY};margin:4px 0 8px">{note}</div>'
            + (f'<div style="font-size:13px;color:{INK};background:{LIGHT};padding:10px 12px;border-radius:6px;margin-bottom:6px">{callout}</div>' if callout else "")
            + f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse">{"".join(rows)}</table></td></tr>')


def tile(value: Any, label: str, col: str = INK) -> str:
    return (f'<td width="25%" style="padding:6px"><div style="background:{LIGHT};border-radius:8px;padding:14px 8px;text-align:center">'
            f'<div style="font-size:26px;font-weight:700;color:{col};line-height:1">{esc(value)}</div>'
            f'<div style="font-size:11px;color:{GREY};text-transform:uppercase;letter-spacing:1px;margin-top:6px">{esc(label)}</div></div></td>')


def shell(inner: str, ok: bool, headline: str, date_txt: str, runs_url: str) -> str:
    col = GREEN if ok else RED
    return (f'<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            f'<body style="margin:0;padding:0;background:{BG}">'
            f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{BG}"><tr><td align="center" style="padding:24px 12px">'
            f'<table role="presentation" width="640" cellspacing="0" cellpadding="0" style="width:100%;max-width:640px;background:#fff;border-radius:10px;overflow:hidden;font-family:{FONT};color:{INK}">'
            f'<tr><td style="background:{INK};padding:22px 28px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td><img src="{LOGO}" width="150" alt="GT Street" style="display:block;width:150px;height:auto"></td>'
            f'<td align="right" style="color:#A3A3AD;font-size:12px;letter-spacing:1px;text-transform:uppercase">Daily digest<br><span style="color:#ECECEF;font-size:14px;letter-spacing:0">{esc(date_txt)}</span></td>'
            f'</tr></table></td></tr>'
            f'<tr><td style="background:{col};height:4px;line-height:4px;font-size:0">&nbsp;</td></tr>'
            f'<tr><td style="padding:24px 28px 4px"><div style="font-size:22px;font-weight:700">{"&#10003; " if ok else "&#9888; "}{esc(headline)}</div></td></tr>'
            + inner +
            f'<tr><td style="padding:28px;font-size:12px;color:{GREY};line-height:1.6;border-top:1px solid {LINE}">'
            f"Prices are whatever each supplier's website shows, less $1. Stock mirrors the supplier: In stock 20, Low stock their exact quantity, "
            f'Back order 0 and still orderable, Out of stock 0. Every price change is in the attached price_changes.csv. '
            + (f'<a href="{esc(runs_url)}" style="color:{RED}">All runs, full reports and downloads</a>' if runs_url else "")
            + '</td></tr></table></td></tr></table></body></html>')


def check_result(c: dict) -> str:
    if c["report"] is None:
        return f'<b style="color:{RED}">{esc("; ".join(c["problems"]) or "no report")}</b>'
    r, v = c["report"], c["verify"]
    base = f'{len(r.get("price_changes", []))} price, {len(r.get("stock_changes") or [])} stock changes'
    if not v.get("ran"):
        return base + f'; <b style="color:{RED}">recheck did not run</b>'
    if v["mismatches"]:
        return base + f'; <b style="color:{RED}">{len(v["mismatches"])} mismatches</b> in {v["rechecked"]:,} rechecked'
    extra = [p for p in c["problems"]]
    tail = f'; <b style="color:{RED}">{esc("; ".join(extra))}</b>' if extra else ""
    return f'<span style="color:{GREEN}">&#10003;</span> {base}; {v["rechecked"]:,} rechecked, all matched{tail}'


def build(manifest: dict) -> tuple[str, str, dict, bytes]:
    """Returns subject, html, summary (for logs and the issue) and the CSV bytes."""
    checks = load_checks(manifest)
    m = consolidate(checks)
    date_txt = manifest.get("digest_label") or datetime.datetime.now(SYD).strftime("%a %d %b %Y")
    runs_url = safe_url(manifest.get("runs_url", ""))
    csv_bytes = write_csv(m["prices"])
    expected = [c for c in checks if c.get("expected")]
    bad_checks = [c for c in checks if c["problems"]]
    n = len(m["prices"]) + len(m["stock"]) + len(m["new"]) + len(m["disc"])
    attention = bool(bad_checks or m["errors"] or m["mismatches"] or m["held"])
    nothing_ran = not m["good"]
    if nothing_ran:
        subject = "Daily Digest: Stock and Pricing - no check completed, attention needed"
        headline = "No supplier check completed"
    else:
        subject = f"Daily Digest: Stock and Pricing - {n} change{'s' if n != 1 else ''}" + (" (attention needed)" if attention else "")
        headline = "Needs attention" if attention else "All checks passed"
    ok = not attention and not nothing_ran

    inner = []
    if nothing_ran:
        lead = "None of the expected supplier checks produced a usable report, so prices and stock were not confirmed. The reasons are listed below."
    else:
        ups = sum(1 for c in m["prices"] if num(c["to"]) > num(c["from"]))
        lead = (f"{len(m['good'])} of {len(checks)} check{'s' if len(checks) != 1 else ''} completed. "
                + (f"{len(m['prices'])} price change{'s' if len(m['prices']) != 1 else ''} ({ups} up, {len(m['prices']) - ups} down) and "
                   f"{len(m['stock'])} stock change{'s' if len(m['stock']) != 1 else ''} were applied."
                   if (m["prices"] or m["stock"]) else "Every supplier was checked and nothing needed changing."))
        rech = sum(c["verify"].get("rechecked", 0) for c in m["good"] if c["verify"].get("ran"))
        if all(c["verify"].get("ran") for c in m["good"]):
            lead += (f" An independent recheck of {rech:,} products against the supplier websites "
                     + (f'found <b style="color:{RED}">{len(m["mismatches"])} mismatch{"es" if len(m["mismatches"]) != 1 else ""}</b>.' if m["mismatches"] else "matched every one."))
        else:
            lead += f' <b style="color:{RED}">The independent recheck did not run for every check.</b>'
    for n in active_notices(manifest.get("digest_date", "")):
        inner.append(f'<tr><td style="padding:0 28px 14px"><div style="background:{LIGHT};border-left:4px solid {RED};padding:12px 14px;border-radius:6px;font-size:14px;line-height:1.5">'
                     f'<b>{esc(n.get("title", "Notice"))}</b><br>{esc(n.get("text", ""))}</div></td></tr>')
    inner.append(f'<tr><td style="padding:6px 28px 14px;font-size:15px;line-height:1.5;color:#333">{lead}</td></tr>')

    sold = sum(1 for s in m["stock"] if s.get("state") == "out" or s.get("now") == "Out of stock")
    restocked = sum(1 for s in m["stock"] if s.get("state") == "in")
    need = len(bad_checks) + len(m["errors"]) + len(m["mismatches"]) + len(m["held"])
    inner.append(f'<tr><td style="padding:0 22px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
                 + tile(len(m["prices"]), "Price changes") + tile(len(m["stock"]), "Stock changes")
                 + tile(sold, "Sold out", RED if sold else INK) + tile(need, "Need you", RED if need else INK) + '</tr></table></td></tr>')

    # The checks themselves, with links to each exact run.
    rows = []
    for c in checks:
        if not c.get("expected"):
            continue
        url = safe_url(c.get("url", ""))
        name = esc(c.get("label", "check"))
        left = f'<a href="{esc(url)}" style="color:{INK};text-decoration:none">{name}</a>' if url else name
        rows.append(pair_row(left, check_result(c)))
    manual = [c for c in checks if not c.get("expected")]
    if manual:
        ok_m = sum(1 for c in manual if c["report"] is not None)
        rows.append(pair_row("Manual runs", f'{len(manual)} run{"s" if len(manual) != 1 else ""} in this period ({ok_m} with a report), included in the totals above'))
    inner.append(section("Checks", "The four scheduled checks since yesterday 10am.", rows))

    # Dealer portals (NYC Dealerline, Equipit): the latest check that read them.
    pc = next((c for c in reversed(checks) if c.get("portals")), None)
    if pc:
        pdat = pc["portals"]
        applied = bool(pdat.get("apply"))
        prow = []
        for pname, r in (pdat.get("portals") or {}).items():
            if r.get("matched") or r.get("ok"):
                txt = (f'{r.get("rows", 0):,} portal products read, {r.get("matched", 0):,} of our variants matched, '
                       f'{r.get("would_change", 0):,} stock difference{"s" if r.get("would_change", 0) != 1 else ""}')
                if r.get("error"):
                    txt += f". Not applied: {r['error']}"
                prow.append(pair_row(esc(pname), esc(txt)))
            else:
                prow.append(pair_row(esc(pname), f'<span style="color:{RED}">Could not be read: {esc(r.get("error") or "unknown")}</span>'))
            if "price_matched" in r:
                g = r.get("price_guide") or {}
                prow.append(pair_row("Tough Dog prices", esc(
                    f'{r.get("price_matched", 0):,} matched to the price guide ({g.get("store", "competitor")}), '
                    f'{r.get("price_would_change", 0):,} changes proposed, {len(r.get("price_held") or []):,} held for review'
                    + (f'. {r["price_error"]}' if r.get("price_error") else ""))))
        note = ("Dealer portal stock is applied." if applied else "Review mode: nothing was changed.")
        inner.append(section("Dealer portals", note, prow))

    # 1. Things that need a person.
    if m["mismatches"]:
        inner.append(section("Recheck mismatches", f"The site does not match the supplier for these. Check them first.{' Showing 10 of ' + str(len(m['mismatches'])) + '.' if len(m['mismatches']) > 10 else ''}", [text_row(x) for x in m["mismatches"][:10]]))
    if bad_checks:
        inner.append(section("Check problems", "These checks are missing or incomplete, so their results are not counted above.",
                             [text_row(f'{c.get("label")}: {"; ".join(c["problems"])}', RED) for c in bad_checks]))
    if m["errors"]:
        inner.append(section("Errors", f"{len(m['errors'])} error{'s' if len(m['errors']) != 1 else ''} during the checks.", [text_row(e) for e in m["errors"][:6]]))
    if m["held"]:
        g = group(m["held"], lambda h: (round(num(h.get("current")) or 0, 2), round(num(h.get("cnc")) or 0, 2)))
        rows = [item_row(h, f'<span style="color:{GREY}">site</span> {money(h.get("current"))}<br><span style="color:{GREY}">supplier</span> <b>{money(h.get("cnc"))}</b>'
                            + (f'<br><span style="color:{GREY};font-size:12px">{esc(h["why"])}</span>' if h.get("why") else "")) for h in g[:10]]
        inner.append(section("Held for your review", f"{len(m['held'])} price{'s' if len(m['held']) != 1 else ''} not changed, largest first. Showing up to 10.", rows))

    if m["big"]:
        g = group(m["big"], lambda c: (round(num(c["from"]), 2), round(num(c["to"]), 2)))
        rows = [item_row(c, f'<span style="color:{GREY};text-decoration:line-through;font-size:13px">{money(c["from"])}</span> <b>{money(c["to"])}</b><br>'
                            + pill("&#9660; " + str(round((1 - num(c["to"]) / num(c["from"])) * 100)) + "%", RED)) for c in g[:10]]
        inner.append(section("Big price drops applied", f"{len(m['big'])} price{'s' if len(m['big']) != 1 else ''} fell more than 20%. Applied as the supplier shows; worth a glance.", rows))

    # 2. What changed.
    def price_cell(c: dict) -> str:
        f, t = num(c["from"]), num(c["to"]); d = t - f; col = GREEN if d > 0 else RED
        pct = f" {abs(d) / f * 100:.0f}%" if f else ""
        return (f'<span style="color:{GREY};text-decoration:line-through;font-size:13px">{money(f)}</span> <b>{money(t)}</b><br>'
                + pill(("&#9650; " if d > 0 else "&#9660; ") + f"${abs(d):,.2f}{pct}", col))
    for title, items in (("Price increases", [c for c in m["prices"] if num(c["to"]) > num(c["from"])]),
                         ("Price decreases", [c for c in m["prices"] if num(c["to"]) < num(c["from"])])):
        if not items:
            continue
        g = group(items, lambda c: (round(num(c["from"]), 2), round(num(c["to"]), 2)))
        g.sort(key=lambda c: abs(num(c["to"]) - num(c["from"])), reverse=True)
        more = " Showing the largest 10; every change is in the attached CSV." if len(g) > 10 else ""
        inner.append(section(title, f"{len(items)} applied, largest first.{more}", [item_row(c, price_cell(c)) for c in g[:10]]))

    if m["stock"]:
        order = {"out": 0, "backorder": 1, "low": 2, "in": 3}
        col = {"out": RED, "backorder": AMBER, "low": AMBER, "in": GREEN}
        label = {"out": "Sold out", "backorder": "Back order", "in": "In stock"}
        g = group(m["stock"], lambda s: (s.get("now"),))
        g.sort(key=lambda s: order.get(s.get("state"), 0))
        def st(s: dict) -> str:
            k = s.get("state") or ("out" if s.get("now") == "Out of stock" else "in")
            was = f'<br><span style="color:{GREY};font-size:12px">was {esc(label.get(s.get("prev"), s.get("prev")))}</span>' if s.get("prev") else ""
            return pill(esc(label.get(k, s.get("now", k))), col.get(k, GREY)) + was
        n_bo = sum(1 for s in m["stock"] if s.get("state") == "backorder"); n_low = sum(1 for s in m["stock"] if s.get("state") == "low")
        inner.append(section("Stock changes", f"{sold} sold out, {restocked} back in stock, {n_low} low stock, {n_bo} on back order (still orderable). Copied from each supplier's own page. Showing the first 12 groups.",
                             [item_row(s, st(s)) for s in g[:12]]))

    if m["new"]:
        g = group(m["new"], lambda c: ())
        def low_price(c: dict) -> str:
            ps = [num(x.get("price")) for x in m["new"] if x.get("product") == c.get("product") and num(x.get("price")) is not None]
            return f"from <b>{money(min(ps))}</b>" if ps else ""
        more = f" {m['deferred']} more are queued for the next check." if m["deferred"] else ""
        inner.append(section("New sizes added", f"{len(m['new'])} size{'s' if len(m['new']) != 1 else ''} CNC sells that the site did not list, added at CNC's price less $1 with their stock.{more}",
                             [item_row(c, low_price(c)) for c in g[:10]]))
    if m["disc"]:
        g = group(m["disc"], lambda c: ())
        inner.append(section("Discontinued at CNC", f"{len(m['disc'])} size{'s' if len(m['disc']) != 1 else ''} no longer sold by CNC (confirmed against CNC's own shop data).",
                             [item_row(c, pill("Discontinued", RED)) for c in g[:10]],
                             "Marked Discontinued on the site and set to out of stock. Nothing was deleted; remove them manually if you no longer want them listed."))

    # 3. Suppliers, per check (derived from the reports, nothing hard coded).
    srows = []
    if m["suppliers"]:
        lab, r = m["suppliers"][-1]
        cnc = f'CNC {r.get("cnc_pages_read", 0):,} pages' + (f', <span style="color:{RED}">{r.get("cnc_pages_failed")} unreadable</span>' if r.get("cnc_pages_failed") else "")
        srows.append(pair_row("CNC Wheels", cnc))
        for name, sv in (r.get("suppliers") or {}).items():
            if not isinstance(sv, dict):
                continue
            if sv.get("error"):
                srows.append(pair_row(f'<span style="color:{RED}">{esc(name)} could not be read</span>', ""))
            else:
                srows.append(pair_row(esc(name), f'{sv.get("variants_matched", 0):,} variants read'))
        inner.append(section("Suppliers", f"From the latest check ({esc(lab)}), each read live from the supplier's own website.", srows))

    body = shell("".join(inner), ok, headline, date_txt, runs_url)
    summary = {"subject": subject, "checks": [{"label": c.get("label"), "run_id": c.get("run_id"), "url": c.get("url"),
               "usable": c["report"] is not None, "problems": c["problems"]} for c in checks],
               "price_changes": len(m["prices"]), "stock_changes": len(m["stock"]), "new_sizes": len(m["new"]),
               "discontinued": len(m["disc"]), "errors": len(m["errors"]), "mismatches": len(m["mismatches"]), "attention": attention}
    return subject, body, summary, csv_bytes


# ---------------------------------------------------------------- delivery
class Permanent(Exception):
    """An error retrying will not fix (bad credentials, missing permission, unknown mailbox)."""


def http_json(req: urllib.request.Request, tries: int = 4, opener=None) -> tuple[int, bytes]:
    """Retries timeouts, throttling (429) and server errors with backoff; 4xx errors are permanent."""
    opener = opener or urllib.request.urlopen
    delay = 5
    for i in range(tries):
        try:
            with opener(req, timeout=40) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()[:600].decode(errors="replace")
            if e.code == 429 or e.code >= 500:
                if i == tries - 1:
                    raise RuntimeError(f"HTTP {e.code} after {tries} tries: {body}")
                wait = int(e.headers.get("Retry-After") or delay) if e.headers else delay
                print(f"  HTTP {e.code}, retrying in {min(wait, 60)}s", flush=True); time.sleep(min(wait, 60)); delay *= 2
                continue
            raise Permanent(f"HTTP {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if i == tries - 1:
                raise RuntimeError(f"network error after {tries} tries: {e}")
            print(f"  network error ({e}), retrying in {delay}s", flush=True); time.sleep(delay); delay *= 2
    raise RuntimeError("unreachable")


def send_graph(subject: str, body: str, to: list[str], csv_bytes: bytes, log: dict, opener=None) -> None:
    tenant, cid, secret = os.environ["MS_TENANT_ID"], os.environ["MS_CLIENT_ID"], os.environ["MS_CLIENT_SECRET"]
    sender = os.environ.get("MS_SENDER") or "joshua.b@gtstreet.au"
    try:
        _, tok = http_json(urllib.request.Request(
            f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}/oauth2/v2.0/token",
            data=urllib.parse.urlencode({"client_id": cid, "client_secret": secret, "scope": "https://graph.microsoft.com/.default",
                                         "grant_type": "client_credentials"}).encode()), opener=opener)
        token = json.loads(tok)["access_token"]
    except Permanent as e:
        raise Permanent(f"Microsoft sign-in failed (check MS_TENANT_ID, MS_CLIENT_ID and that MS_CLIENT_SECRET has not expired): {e}")
    log["graph_auth"] = "ok"; print("Microsoft 365 sign-in: ok", flush=True)
    msg = {"message": {"subject": subject, "body": {"contentType": "HTML", "content": body},
                       "toRecipients": [{"emailAddress": {"address": a}} for a in to],
                       "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": "price_changes.csv",
                                        "contentType": "text/csv", "contentBytes": base64.b64encode(csv_bytes).decode()}]},
           "saveToSentItems": True}
    try:
        st, _ = http_json(urllib.request.Request(f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(sender)}/sendMail",
                                                 data=json.dumps(msg).encode(), headers={"Authorization": f"Bearer {token}",
                                                                                         "Content-Type": "application/json"}), opener=opener)
    except Permanent as e:
        hint = ("the app needs the Mail.Send application permission with admin consent" if "403" in str(e) or "Access" in str(e)
                else f"the sender mailbox {sender} must be a real licensed mailbox" if "404" in str(e) or "ErrorInvalidUser" in str(e) else "")
        raise Permanent(f"Microsoft 365 refused the email{(' (' + hint + ')') if hint else ''}: {e}")
    if st != 202:
        raise RuntimeError(f"Microsoft 365 returned HTTP {st}, expected 202")
    log["graph_send"] = "accepted (HTTP 202)"


def send_smtp(subject: str, body: str, to: list[str], csv_bytes: bytes, log: dict, smtp_cls=None) -> None:
    host, port = os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT") or 587)
    user, pw = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASS", "")
    sender = os.environ.get("SMTP_FROM") or user
    m = MIMEMultipart("mixed"); m["Subject"] = subject; m["From"] = sender; m["To"] = ", ".join(to)
    alt = MIMEMultipart("alternative"); alt.attach(MIMEText(subject + "\n\nOpen this email in an HTML capable client.", "plain")); alt.attach(MIMEText(body, "html"))
    m.attach(alt)
    a = MIMEApplication(csv_bytes, Name="price_changes.csv"); a["Content-Disposition"] = 'attachment; filename="price_changes.csv"'; m.attach(a)
    smtp_cls = smtp_cls or smtplib.SMTP
    last = None
    for i in range(3):
        try:
            with smtp_cls(host, port, timeout=40) as s:
                s.starttls(context=ssl.create_default_context())
                if user: s.login(user, pw)
                refused = s.sendmail(sender, to, m.as_string())
            if refused:
                log["smtp_refused"] = list(refused)
            log["smtp_send"] = "accepted"; return
        except smtplib.SMTPAuthenticationError as e:
            raise Permanent(f"SMTP sign-in failed: {e.smtp_code}")
        except (smtplib.SMTPException, OSError) as e:
            last = e; print(f"  SMTP error ({e}), retry {i + 1}", flush=True); time.sleep(5 * (i + 1))
    raise RuntimeError(f"SMTP failed after 3 tries: {last}")


def deliver(subject: str, body: str, to: list[str], csv_bytes: bytes, log: dict, opener=None, smtp_cls=None) -> str | None:
    """Returns the method that the mail server accepted, or None. Accepted is not the same as delivered to the inbox."""
    if all(os.environ.get(k) for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET")):
        try:
            send_graph(subject, body, to, csv_bytes, log, opener); return "microsoft365"
        except Exception as e:  # noqa: BLE001  recorded and the next method tried
            log["graph_error"] = str(e)[:800]; print("Microsoft 365 FAILED:", log["graph_error"], flush=True)
    else:
        log["graph_error"] = "not configured"
    if os.environ.get("SMTP_HOST"):
        try:
            send_smtp(subject, body, to, csv_bytes, log, smtp_cls); return "smtp"
        except Exception as e:  # noqa: BLE001
            log["smtp_error"] = str(e)[:800]; print("SMTP FAILED:", log["smtp_error"], flush=True)
    else:
        log["smtp_error"] = "not configured"
    return None


def backup_issue(subject: str, summary: dict, log: dict, run=None) -> bool:
    """Opens one attention issue per digest day, or comments on it if a retry also fails. Never closes issues."""
    run = run or subprocess.run
    owner, day, run_url = os.environ.get("REPO_OWNER", ""), summary.get("digest_date", ""), os.environ.get("RUN_URL", "")
    title = f"Daily digest NOT emailed: {day}"
    lines = [f"@{owner} The daily digest email for {day} was **not sent**.", "",
             f"- Microsoft 365: {log.get('graph_error', 'n/a')}", f"- SMTP: {log.get('smtp_error', 'n/a')}", f"- Run: {run_url}", "",
             f"Digest: {summary.get('price_changes')} price changes, {summary.get('stock_changes')} stock changes, "
             f"{summary.get('errors')} errors, {summary.get('mismatches')} recheck mismatches."]
    for c in summary.get("checks", []):
        lines.append(f"- {c['label']}: {'ok' if c['usable'] and not c['problems'] else '; '.join(c['problems']) or 'no report'} {c.get('url') or ''}")
    text = "\n".join(lines)
    found = run(["gh", "issue", "list", "--label", "sync-attention", "--state", "open", "--search", f"{title} in:title", "--json", "number,title"],
                capture_output=True, text=True)
    try:
        existing = [i for i in json.loads(found.stdout or "[]") if i.get("title") == title]
    except ValueError:
        existing = []
    if existing:
        p = run(["gh", "issue", "comment", str(existing[0]["number"]), "--body", text], capture_output=True, text=True)
    else:
        run(["gh", "label", "create", "sync-attention", "--color", "D0102B", "--force"], capture_output=True, text=True)
        p = run(["gh", "issue", "create", "--title", title, "--body", text, "--label", "sync-attention"] + (["--assignee", owner] if owner else []),
                capture_output=True, text=True)
    log["issue"] = (p.stdout or p.stderr or "").strip()[:300]
    return p.returncode == 0


def main() -> int:
    mode = os.environ.get("DIGEST_MODE", "live")
    path = os.environ.get("DIGEST_MANIFEST") or ("tests/fixtures/manifest.json" if mode == "test" else "")
    if not path or not os.path.exists(path):
        print(f"No digest manifest ({path or 'DIGEST_MANIFEST not set'}); digest.py collect must run first.", file=sys.stderr)
        return 3
    manifest = json.load(open(path))
    if mode == "test":
        base = os.path.dirname(path)
        for c in manifest.get("checks", []):
            if c.get("dir"): c["dir"] = os.path.join(base, c["dir"])
    subject, body, summary, csv_bytes = build(manifest)
    summary["digest_date"] = manifest.get("digest_date", "")
    if mode == "test":
        subject = "TEST EMAIL (sample data, nothing changed): " + subject
    open("email.html", "w").write(body)
    log: dict[str, Any] = {"mode": mode, "built": True, "subject": subject, "summary": summary}
    print(f"Email built: {subject} ({len(body):,} bytes, CSV {len(csv_bytes):,} bytes)", flush=True)
    if mode == "preview":
        log["state"] = "preview, not sent"; json.dump(log, open("delivery.json", "w"), indent=1); return 0
    to = [a.strip() for a in (os.environ.get("EMAIL_TO") or DEFAULT_TO).split(",") if re.fullmatch(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+", a.strip())]
    to = list(dict.fromkeys(to))
    if not to:
        log["state"] = "failed: no valid recipients"; json.dump(log, open("delivery.json", "w"), indent=1); print(log["state"]); return 2
    method = deliver(subject, body, to, csv_bytes, log)
    if method:
        log["state"] = f"accepted by {method} for {', '.join(to)} (inbox delivery is not confirmed by the server)"
        print("Email", log["state"], flush=True)
        json.dump(log, open("delivery.json", "w"), indent=1)
        return 0
    log["state"] = "failed: email not sent"
    ok = mode == "live" and os.environ.get("GH_TOKEN") and backup_issue(subject, summary, log)
    log["backup"] = "issue opened or updated" if ok else "backup issue FAILED or not attempted"
    print("EMAIL NOT SENT.", log["backup"], log.get("issue", ""), flush=True)
    json.dump(log, open("delivery.json", "w"), indent=1)
    return 2


if __name__ == "__main__":
    sys.exit(main())
