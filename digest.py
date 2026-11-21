"""Scheduling and run selection for the supplier checks and the Daily digest. Standard library only.

  python3 digest.py gate "<cron that fired>" HOUR [HOUR...]
      For scheduled runs: prints go=true only if the Sydney local time of the *scheduled* moment (not the moment
      GitHub got around to starting it) falls in one of the hours. Each local time has one UTC cron for AEDT and one
      for AEST; exactly one of them lands on the wanted hour on any day, including the two daylight saving switch days,
      so a late start is never skipped and a duplicate never runs. Manual runs always go.

  python3 digest.py sent DATE          prints sent=true if a digest for that Sydney date was already accepted
  python3 digest.py collect            waits for the expected checks, downloads their reports, writes manifest.json

The digest covers every scheduled check due since yesterday 10am (four a day, every 6 hours), plus any manual runs
started in that period, which are labelled as manual. Each expected check is reported as ok, failed, running, missing or
without a usable report; notify.py turns those into the email.
"""
from __future__ import annotations

import datetime as dt, json, os, re, subprocess, sys, time
from zoneinfo import ZoneInfo

SYD = ZoneInfo("Australia/Sydney")
UTC = dt.timezone.utc
SYNC_TIMEOUT_MIN = 355
SLOT_HOURS = (3, 9, 15, 21)  # Sydney hours of the four daily checks (minute 17), every 6 hours
SLOT_MINUTE = 17


def scheduled_moment(cron: str, started: dt.datetime) -> dt.datetime:
    """The UTC time this cron was due, on the most recent day at or before the actual start."""
    minute, hour = (int(x) for x in cron.split()[:2])
    due = started.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return due - dt.timedelta(days=1) if due > started + dt.timedelta(minutes=5) else due


def gate(cron: str, hours: list[int], started: dt.datetime | None = None) -> bool:
    started = started or dt.datetime.now(UTC)
    return scheduled_moment(cron, started).astimezone(SYD).hour in hours


FULL_SWEEP_WEEKDAY, FULL_SWEEP_HOUR = 3, 3  # Thursday (Monday is 0), the 3:17am check: every CNC product page is read


def is_full_sweep(cron: str, started: dt.datetime) -> bool:
    local = scheduled_moment(cron, started).astimezone(SYD)
    return local.weekday() == FULL_SWEEP_WEEKDAY and local.hour == FULL_SWEEP_HOUR


EXTERNAL_TITLE = "Scheduled check"


def external_run_recent(hours: float = 5.0) -> bool:
    """True if the external timer already started a check lately, so the late GitHub cron backup must not run a second one."""
    try:
        out = json.loads(gh("api", f"repos/{repo()}/actions/workflows/sync.yml/runs?event=workflow_dispatch&per_page=20"))
    except Exception:
        return False  # fail open: a backup that cannot check still runs
    now = dt.datetime.now(UTC)
    return any((r.get("display_title") or "").startswith(EXTERNAL_TITLE)
               and (now - parse(r["created_at"])).total_seconds() < hours * 3600 for r in out.get("workflow_runs", []))


def gh(*args: str) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {p.stderr.strip()[:300]}")
    return p.stdout


def repo() -> str:
    return os.environ["GITHUB_REPOSITORY"]


def already_sent(day: str) -> bool:
    out = json.loads(gh("api", f"repos/{repo()}/actions/artifacts?name=digest-sent-{day}&per_page=5"))
    return any(not a.get("expired") for a in out.get("artifacts", []))


def parse(t: str) -> dt.datetime:
    return dt.datetime.fromisoformat(t.replace("Z", "+00:00"))


SCHEDULED_TITLE = re.compile(r"Scheduled sync \((.+)\)")


def due_slots(now: dt.datetime) -> list[dt.datetime]:
    """The checks that should have started in the digest window (yesterday 10am Sydney up to now), as UTC instants."""
    today = now.astimezone(SYD).date()
    start = dt.datetime.combine(today - dt.timedelta(days=1), dt.time(10), SYD)
    out = []
    for d in (today - dt.timedelta(days=1), today):
        for h in SLOT_HOURS:
            due = dt.datetime.combine(d, dt.time(h, SLOT_MINUTE), SYD)
            if start <= due <= now:
                out.append(due.astimezone(UTC))
    return out


def slot_for(run: dict, slots: list[dt.datetime]) -> dt.datetime | None:
    """Which due check a run belongs to. A GitHub scheduled run carries its cron in its title, so its due time is exact
    even when GitHub starts it hours late; the external timer's runs go to the latest due check before they started."""
    created = parse(run["created_at"])
    m = SCHEDULED_TITLE.search(run.get("display_title") or "")
    if m:
        moment = scheduled_moment(m.group(1), created)
        return next((s for s in slots if abs((s - moment).total_seconds()) < 120), None)
    if (run.get("display_title") or "").startswith(EXTERNAL_TITLE):
        before = [s for s in slots if s <= created and (created - s).total_seconds() < 6 * 3600]
        return max(before) if before else None
    return None


def has_report(run_id: int) -> bool:
    out = json.loads(gh("api", f"repos/{repo()}/actions/runs/{run_id}/artifacts"))
    return any(a["name"] == "sync-report" and not a.get("expired") for a in out.get("artifacts", []))


def list_runs() -> list[dict]:
    out = json.loads(gh("api", f"repos/{repo()}/actions/workflows/sync.yml/runs?per_page=60"))
    return out.get("workflow_runs", [])


def classify(runs: list[dict], now: dt.datetime, report_check=has_report) -> list[dict]:
    """One entry per due check (found or not) plus manual runs in the window, oldest first."""
    today = now.astimezone(SYD).date()
    period_start = dt.datetime.combine(today - dt.timedelta(days=1), dt.time(10), SYD)
    slots = due_slots(now)
    found: dict[dt.datetime, list[dict]] = {s: [] for s in slots}
    manual = []
    for r in runs:
        created = parse(r["created_at"])
        if r["event"] == "schedule" or (r["event"] == "workflow_dispatch" and (r.get("display_title") or "").startswith(EXTERNAL_TITLE)):
            s = slot_for(r, slots)
            if s:
                found[s].append(r)
            elif r["event"] == "workflow_dispatch" and period_start <= created <= now:
                manual.append(r)
        elif r["event"] == "workflow_dispatch" and period_start <= created <= now:
            manual.append(r)

    def entry(r: dict, label: str, expected: bool) -> dict:
        age = (now - parse(r["created_at"])).total_seconds() / 60
        if r["status"] != "completed":
            state = "timed_out" if age > SYNC_TIMEOUT_MIN + 5 else "running"
        elif r["conclusion"] == "cancelled":
            state = "cancelled"
        elif r["conclusion"] == "timed_out":
            state = "timed_out"
        else:
            state = "done"
        return {"label": label, "expected": expected, "run_id": r["id"], "url": r["html_url"], "event": r["event"],
                "created": r["created_at"], "status": r["status"], "conclusion": r.get("conclusion"), "state": state}

    checks = []
    for slot in slots:
        loc = slot.astimezone(SYD)
        full = loc.weekday() == FULL_SWEEP_WEEKDAY and loc.hour == FULL_SWEEP_HOUR
        nice = f"{loc.strftime('%-I%p').lower()} {'full sweep' if full else 'check'} {loc.strftime('%a %d %b')}"
        # Gated duplicates finish in seconds with no report; the real run is the one that produced (or is producing) one.
        real = [r for r in found[slot] if r["status"] != "completed" or r.get("conclusion") != "success" or report_check(r["id"])]
        if not real:
            checks.append({"label": nice, "expected": True, "state": "missing", "run_id": None, "url": "", "created": None,
                           "due": slot.isoformat().replace("+00:00", "Z")})
        else:
            checks.append(entry(max(real, key=lambda r: r["created_at"]), nice, True))
    for r in manual:
        loc = parse(r["created_at"]).astimezone(SYD)
        checks.append(entry(r, f"Manual check {loc.strftime('%-I:%M%p %a').replace('AM', 'am').replace('PM', 'pm')}", False))
    return sorted(checks, key=lambda c: c.get("created") or c.get("due") or "")


def collect(wait_min: int = 60) -> dict:
    now = dt.datetime.now(UTC)
    for i in range(wait_min + 1):
        checks = classify(list_runs(), now)
        busy = [c for c in checks if c["state"] == "running"]
        if not busy or i == wait_min:
            break
        print(f"Waiting for {', '.join(c['label'] for c in busy)} to finish ({i + 1} of {wait_min} min)", flush=True)
        time.sleep(60); now = dt.datetime.now(UTC)
    for c in checks:
        if c["state"] != "done":
            continue
        d = f"run_{c['run_id']}"
        p = subprocess.run(["gh", "run", "download", str(c["run_id"]), "-n", "sync-report", "-D", d], capture_output=True, text=True)
        if p.returncode == 0:
            c["dir"] = d
        else:
            c["state"] = "no_report"; print(f"{c['label']}: report download failed: {p.stderr.strip()[:200]}", flush=True)
    today = now.astimezone(SYD)
    manifest = {"digest_date": today.date().isoformat(), "digest_label": today.strftime("%a %d %b %Y"),
                "generated": now.isoformat(), "runs_url": f"https://github.com/{repo()}/actions/workflows/sync.yml", "checks": checks}
    json.dump(manifest, open("manifest.json", "w"), indent=1)
    for c in checks:
        print(f"{c['label']}: {c['state']} {c.get('url', '')}")
    return manifest


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "gate":
        if os.environ.get("GITHUB_EVENT_NAME", "schedule") != "schedule":
            print("go=true"); print("full=false")
        else:
            ok = gate(sys.argv[2], [int(h) for h in sys.argv[3:]])
            print("full=" + ("true" if ok and is_full_sweep(sys.argv[2], dt.datetime.now(UTC)) else "false"))
            if ok and external_run_recent():
                print("The external timer already started this check; the GitHub backup stands down.", file=sys.stderr)
                ok = False
            print("go=" + ("true" if ok else "false"))
    elif cmd == "sent":
        print("sent=" + ("true" if already_sent(sys.argv[2]) else "false"))
    elif cmd == "collect":
        collect(int(os.environ.get("DIGEST_WAIT_MIN", "60")))
    else:
        sys.exit(__doc__)
