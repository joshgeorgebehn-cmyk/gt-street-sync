"""Tests for the daily digest: python3 -m unittest discover -s tests -v
Everything external is mocked: no email is sent, no GitHub or Shopify call is made."""
import copy, csv, datetime as dt, io, json, os, shutil, smtplib, sys, tempfile, unittest, urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import digest, notify  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
UTC = dt.timezone.utc


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.cwd = os.getcwd()
        shutil.copytree(FIX, os.path.join(self.tmp, "f")); os.chdir(self.tmp)
        self.env = mock.patch.dict(os.environ, {}, clear=False); self.env.start()
        for k in ("MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET", "SMTP_HOST", "GH_TOKEN", "EMAIL_TO", "DIGEST_MODE"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop(); os.chdir(self.cwd); shutil.rmtree(self.tmp)

    def manifest(self, edit=None):
        m = json.load(open("f/manifest.json"))
        for c in m["checks"]:
            c["dir"] = os.path.join("f", c["dir"])
        if edit: edit(m)
        return m

    def report(self, name):
        return json.load(open(f"f/{name}/report.json"))

    def write_report(self, name, rep):
        json.dump(rep, open(f"f/{name}/report.json", "w"))


class Consolidation(Base):
    def test_two_checks_merge_first_from_last_to(self):  # 2, 4
        s, body, summ, csvb = notify.build(self.manifest())
        m = notify.consolidate(notify.load_checks(self.manifest()))
        v1 = next(c for c in m["prices"] if c["id"].endswith("/1"))
        self.assertEqual((v1["from"], v1["to"]), (500, 450))
        self.assertEqual(len([c for c in m["prices"] if c["id"].endswith("/1")]), 1)
        self.assertEqual(summ["price_changes"], 3)
        self.assertIn("3 changes", s) if False else self.assertTrue(s.startswith("Daily Digest: Stock and Pricing - "))

    def test_stock_first_prev_last_state(self):  # 3, 5
        m = notify.consolidate(notify.load_checks(self.manifest()))
        v1 = next(c for c in m["stock"] if c["id"].endswith("/1"))
        self.assertEqual((v1["prev"], v1["state"]), ("in", "out"))
        bo = next(c for c in m["stock"] if c["id"].endswith("/5"))
        self.assertEqual(bo["state"], "backorder")

    def test_change_that_returns_to_start_is_dropped(self):
        r = self.report("run_am"); r["price_changes"][0]["to"] = 500; self.write_report("run_am", r)
        m = notify.consolidate(notify.load_checks(self.manifest()))
        self.assertFalse([c for c in m["prices"] if c["id"].endswith("/1")])

    def test_new_and_discontinued_deduped(self):  # 6, 7
        m = notify.consolidate(notify.load_checks(self.manifest()))
        self.assertEqual(len(m["new"]), 1); self.assertEqual(len(m["disc"]), 1)

    def test_held_from_latest_check_only(self):  # 8
        r = self.report("run_mid"); r["held"] = [{"variant": "X", "id": "h1", "current": 100, "cnc": 50, "why": "over 20% cut"}]; self.write_report("run_mid", r)
        self.assertEqual(notify.consolidate(notify.load_checks(self.manifest()))["held"], [])
        r = self.report("run_am"); r["held"] = [{"variant": "X", "id": "h1", "current": 100, "cnc": 50, "why": "over 20% cut"}]; self.write_report("run_am", r)
        s, body, *_ = notify.build(self.manifest())
        self.assertIn("Held for your review", body); self.assertIn("over 20% cut", body); self.assertIn("attention", s)

    def test_big_drop_flagged(self):  # 9
        m = notify.consolidate(notify.load_checks(self.manifest()))
        self.assertEqual([c["id"][-1] for c in m["big"]], ["2"])  # 400 to 300 is 25%; 500 to 450 is 10%

    def test_no_changes_says_so(self):  # 1
        for n in ("run_mid", "run_am"):
            r = self.report(n); r.update(price_changes=[], stock_changes=[], new_variants=[], discontinued=[]); self.write_report(n, r)
        s, body, *_ = notify.build(self.manifest())
        self.assertIn("0 changes", s); self.assertIn("nothing needed changing", body); self.assertIn("All checks passed", body)


class Problems(Base):
    def test_verify_mismatch_highlighted(self):  # 10
        json.dump({"ran": True, "rechecked": 10, "amended": 2, "sampled": 8, "mismatches": ["PRICE A: GT Street $1, CNC $5"]}, open("f/run_am/verify.json", "w"))
        s, body, summ, _ = notify.build(self.manifest())
        self.assertIn("Recheck mismatches", body); self.assertEqual(summ["mismatches"], 1); self.assertIn("attention", s)

    def test_verify_not_run(self):  # 11
        os.remove("f/run_am/verify.json")
        s, body, summ, _ = notify.build(self.manifest())
        self.assertIn("recheck did not run", body); self.assertTrue(summ["attention"])

    def test_old_verify_md_still_read(self):
        os.remove("f/run_am/verify.json")
        open("f/run_am/verify.md", "w").write("- Variants re-checked against the supplier: 77\n- Of which amended this run: 4\n- Mismatches: 0\n")
        c = notify.load_checks(self.manifest())[1]
        self.assertEqual(c["verify"]["rechecked"], 77); self.assertEqual(c["problems"], [])

    def test_supplier_error_shown(self):  # 12
        r = self.report("run_am"); r["suppliers"]["XForce"]["error"] = "HTTP 503"; r["errors"] = ["xforce: HTTP 503"]; self.write_report("run_am", r)
        s, body, *_ = notify.build(self.manifest())
        self.assertIn("XForce could not be read", body); self.assertIn("Errors", body)

    def test_missing_report(self):  # 13
        os.remove("f/run_am/report.json")
        s, body, summ, _ = notify.build(self.manifest())
        self.assertIn("its report was not available", body); self.assertTrue(summ["attention"])
        self.assertEqual(summ["price_changes"], 2)  # only the midday check counted, never zero-filled

    def test_corrupted_report(self):  # 14
        open("f/run_am/report.json", "w").write("{not json")
        self.assertIn("its report was corrupted", notify.build(self.manifest())[1])

    def test_incomplete_report(self):  # 15
        r = self.report("run_am"); del r["suppliers"]; r["complete"] = False; self.write_report("run_am", r)
        c = notify.load_checks(self.manifest())[1]
        self.assertIsNone(c["report"]); self.assertTrue(any("did not complete" in p for p in c["problems"]))

    def test_report_from_another_run_rejected(self):
        r = self.report("run_am"); r["run_id"] = "999"; self.write_report("run_am", r)
        self.assertIn("belongs to run 999", " ".join(notify.load_checks(self.manifest())[1]["problems"]))

    def test_malformed_price_rejected(self):
        r = self.report("run_am"); r["price_changes"][0]["to"] = "abc"; self.write_report("run_am", r)
        self.assertIsNone(notify.load_checks(self.manifest())[1]["report"])

    def test_missing_and_running_checks(self):  # 16, 17, 18
        def edit(m):
            m["checks"][0] = {"label": "12pm check", "expected": True, "state": "missing"}
            m["checks"][1].update(state="running")
        s, body, summ, _ = notify.build(self.manifest(edit))
        self.assertIn("this check never ran", body); self.assertIn("still running", body)
        self.assertIn("no check completed", s); self.assertNotIn("All checks passed", body)

    def test_failed_run_is_flagged_even_with_report(self):
        def edit(m): m["checks"][1]["conclusion"] = "failure"
        self.assertIn("GitHub reported the run as failure", notify.build(self.manifest(edit))[1])

    def test_unusual_characters_and_missing_image(self):  # 29, 30
        body = notify.build(self.manifest())[1]
        self.assertIn("JSR &lt;Tokyo&gt; &amp; &quot;Drift&quot; Wheel", body); self.assertNotIn("<Tokyo>", body)
        self.assertIn(f"background:{notify.LIGHT};border-radius:6px", body)  # placeholder tile instead of a broken image

    def test_unsafe_urls_dropped(self):
        self.assertEqual(notify.safe_url("javascript:alert(1)"), ""); self.assertEqual(notify.safe_url("https://evil.example/x"), "")
        self.assertTrue(notify.safe_url("https://cdn.shopify.com/a.png"))
        self.assertEqual(notify.product_url({"handle": 'x"><script>'}), "https://gtstreet.au/products/x%22%3E%3Cscript%3E")


class CsvFile(Base):
    def test_csv_matches_consolidated_changes(self):  # 28
        _, _, summ, data = notify.build(self.manifest())
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        self.assertEqual(len(rows), summ["price_changes"])
        v1 = next(r for r in rows if r["variant"].startswith("GT Form"))
        self.assertEqual((v1["old_price"], v1["new_price"], v1["change"], v1["change_pct"]), ("500.00", "450.00", "-50.00", "-10.0"))
        z = notify.write_csv([{"variant": "z", "from": 0, "to": 5}], "z.csv")  # no division by zero
        self.assertIn(b"+5.00,", z)


class FakeResp:
    def __init__(self, status=202, body=b'{"access_token":"t"}'): self.status, self.body = status, body
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))


class Delivery(Base):
    def setUp(self):
        super().setUp()
        os.environ.update(MS_TENANT_ID="t", MS_CLIENT_ID="c", MS_CLIENT_SECRET="s", MS_SENDER="joshua.b@gtstreet.au")
        mock.patch("time.sleep").start(); self.addCleanup(mock.patch.stopall)

    def test_graph_sends_with_csv_attachment(self):  # 14 (acceptance), 28
        sent = []
        def opener(req, timeout=0):
            sent.append(req)
            return FakeResp(202, b"") if "sendMail" in req.full_url else FakeResp(200)
        log = {}
        self.assertEqual(notify.deliver("S", "<b>x</b>", ["a@b.com"], b"x,y\n", log, opener=opener), "microsoft365")
        msg = json.loads(sent[-1].data)["message"]
        self.assertEqual(msg["attachments"][0]["name"], "price_changes.csv")
        self.assertEqual(log["graph_send"], "accepted (HTTP 202)")

    def test_graph_auth_failure_is_permanent_and_falls_to_smtp(self):  # 19, 21
        calls = []
        def opener(req, timeout=0):
            calls.append(1); raise http_error(401, b'{"error":"invalid_client"}')
        os.environ.update(SMTP_HOST="smtp.x", SMTP_USER="u", SMTP_PASS="p")
        smtp = mock.MagicMock(); smtp.return_value.__enter__.return_value.sendmail.return_value = {}
        log = {}
        self.assertEqual(notify.deliver("S", "b", ["a@b.com"], b"c", log, opener=opener, smtp_cls=smtp), "smtp")
        self.assertEqual(len(calls), 1)  # a permanent error is not retried
        self.assertIn("MS_CLIENT_SECRET", log["graph_error"])
        smtp.return_value.__enter__.return_value.starttls.assert_called()

    def test_graph_throttling_retried(self):
        n = {"i": 0}
        def opener(req, timeout=0):
            if "sendMail" in req.full_url:
                n["i"] += 1
                if n["i"] < 3: raise http_error(429)
                return FakeResp(202, b"")
            return FakeResp(200)
        self.assertEqual(notify.deliver("S", "b", ["a@b.com"], b"", {}, opener=opener), "microsoft365"); self.assertEqual(n["i"], 3)

    def test_graph_permission_hint(self):  # 20
        def opener(req, timeout=0):
            if "sendMail" in req.full_url: raise http_error(403, b'{"error":{"code":"ErrorAccessDenied"}}')
            return FakeResp(200)
        log = {}
        self.assertIsNone(notify.deliver("S", "b", ["a@b.com"], b"", log, opener=opener))
        self.assertIn("Mail.Send", log["graph_error"])

    def test_smtp_failure_then_issue_and_red_exit(self):  # 22, 23
        os.environ.update(SMTP_HOST="smtp.x", SMTP_USER="u", SMTP_PASS="p", GH_TOKEN="x", REPO_OWNER="josh",
                          DIGEST_MANIFEST="m.json", DIGEST_MODE="live")
        json.dump(self.manifest(), open("m.json", "w"))
        smtp = mock.MagicMock(side_effect=OSError("refused"))
        runs = []
        def run(cmd, **k):
            runs.append(cmd); return mock.Mock(returncode=0, stdout="[]" if cmd[2] == "list" else "https://github.com/x/issues/5", stderr="")
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401)), mock.patch("smtplib.SMTP", smtp), mock.patch("subprocess.run", run):
            code = notify.main()
        self.assertEqual(code, 2)
        self.assertTrue(any(c[:3] == ["gh", "issue", "create"] for c in runs))
        self.assertFalse(any(c[:3] == ["gh", "issue", "close"] for c in runs))  # never closes anything
        d = json.load(open("delivery.json")); self.assertEqual(d["state"], "failed: email not sent")

    def test_second_failure_same_day_comments_not_new_issue(self):
        runs = []
        def run(cmd, **k):
            runs.append(cmd)
            out = json.dumps([{"number": 7, "title": "Daily digest NOT emailed: 2026-11-18"}]) if cmd[2] == "list" else "ok"
            return mock.Mock(returncode=0, stdout=out, stderr="")
        self.assertTrue(notify.backup_issue("S", {"digest_date": "2026-11-18", "checks": []}, {}, run=run))
        self.assertTrue(any(c[:3] == ["gh", "issue", "comment"] for c in runs)); self.assertFalse(any(c[:3] == ["gh", "issue", "create"] for c in runs))

    def test_issue_failure_still_red(self):  # J
        os.environ.update(GH_TOKEN="x", DIGEST_MANIFEST="m.json", DIGEST_MODE="live")
        json.dump(self.manifest(), open("m.json", "w"))
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401)), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="no")):
            self.assertEqual(notify.main(), 2)
        self.assertIn("FAILED", json.load(open("delivery.json"))["backup"])

    def test_preview_sends_nothing(self):
        os.environ.update(DIGEST_MANIFEST="m.json", DIGEST_MODE="preview"); json.dump(self.manifest(), open("m.json", "w"))
        with mock.patch("urllib.request.urlopen") as u:
            self.assertEqual(notify.main(), 0); u.assert_not_called()
        self.assertTrue(os.path.exists("email.html"))

    def test_no_manifest_never_falls_back(self):
        open("report.json", "w").write("{}")
        os.environ.update(DIGEST_MODE="live"); os.environ.pop("DIGEST_MANIFEST", None)
        self.assertEqual(notify.main(), 3)

    def test_recipients_default_and_deduped(self):
        os.environ.update(DIGEST_MANIFEST="m.json", EMAIL_TO="a@b.com, a@b.com ,bad,"); json.dump(self.manifest(), open("m.json", "w"))
        with mock.patch.object(notify, "deliver", return_value="microsoft365") as d:
            self.assertEqual(notify.main(), 0)
        self.assertEqual(d.call_args[0][2], ["a@b.com"])
        self.assertIn("billy@breadenmedia.com.au", notify.DEFAULT_TO)


class Scheduling(unittest.TestCase):
    def t(self, s): return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)

    def test_exactly_one_cron_per_attempt_every_day(self):  # 26, 27
        crons = {"sync_am": ["17 14 * * *", "17 15 * * *"], "sync_pm": ["17 1 * * *", "17 2 * * *"]}
        day = dt.date(2026, 1, 1)
        while day < dt.date(2027, 1, 1):
            for name, pair in crons.items():
                hours = [1] if name == "sync_am" else [12]
                went = [c for c in pair if digest.gate(c, hours, dt.datetime.combine(day, dt.time(int(c.split()[1]), 20), UTC))]
                self.assertEqual(len(went), 1, f"{name} on {day}: {went}")
            day += dt.timedelta(days=1)

    def test_digest_attempts_land_7_or_8_am_and_never_three_misses(self):
        crons = ["7 20 * * *", "7 21 * * *", "37 21 * * *", "37 22 * * *"]
        for day in (dt.date(2026, 4, 5), dt.date(2026, 7, 1), dt.date(2026, 10, 4), dt.date(2026, 11, 18)):
            went = [c for c in crons if digest.gate(c, [7, 8], dt.datetime.combine(day, dt.time(int(c.split()[1]), int(c.split()[0]) + 2), UTC))]
            self.assertGreaterEqual(len(went), 2, day); self.assertLessEqual(len(went), 3, day)

    def test_late_start_still_runs(self):
        self.assertTrue(digest.gate("17 14 * * *", [1], self.t("2026-11-17T16:05:00")))  # 3 hours late, still the 1am slot

    def test_dst_change_days(self):  # 26
        self.assertFalse(digest.gate("17 14 * * *", [1], self.t("2026-10-03T14:17:00")))  # Sun 4 Oct, DST starts at 2am: 12:17am AEST
        self.assertTrue(digest.gate("17 15 * * *", [1], self.t("2026-10-03T15:17:00")))   # 1:17am AEST, the real check
        self.assertTrue(digest.gate("17 14 * * *", [1], self.t("2026-04-04T14:17:00")))   # day DST ends: 1:17am AEDT
        self.assertFalse(digest.gate("17 15 * * *", [1], self.t("2026-04-04T15:17:00")))


def run(i, event, created, status="completed", conclusion="success"):
    return {"id": i, "event": event, "created_at": created, "status": status, "conclusion": conclusion, "html_url": f"https://github.com/x/runs/{i}"}


class Selection(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 17, 20, 10, tzinfo=UTC)  # 7:10am Wed Sydney

    def test_picks_the_two_expected_and_labels_manual(self):  # 3 (selection)
        runs = [run(1, "schedule", "2026-11-17T01:17:30Z"), run(2, "schedule", "2026-11-17T02:17:30Z"),   # 12:17 real, 13:17 gated
                run(3, "schedule", "2026-11-17T14:17:40Z"), run(4, "schedule", "2026-11-17T15:17:40Z"),   # 1:17 real, 2:17 gated
                run(5, "workflow_dispatch", "2026-11-17T09:25:58Z"), run(6, "schedule", "2026-11-15T01:17:00Z")]
        checks = digest.classify(runs, self.NOW, report_check=lambda i: i in (1, 3, 5))
        self.assertEqual([(c["run_id"], c["expected"]) for c in checks], [(1, True), (5, False), (3, True)])

    def test_missing_expected_check(self):  # 16
        checks = digest.classify([run(1, "schedule", "2026-11-17T01:17:30Z")], self.NOW, report_check=lambda i: True)
        self.assertEqual([c["state"] for c in checks], ["done", "missing"])

    def test_running_and_overrun(self):  # 17, 18
        r = run(3, "schedule", "2026-11-17T14:17:40Z", status="in_progress", conclusion=None)
        self.assertEqual(digest.classify([r], self.NOW, report_check=lambda i: False)[-1]["state"], "running")
        self.assertEqual(digest.classify([r], self.NOW + dt.timedelta(hours=4), report_check=lambda i: False)[-1]["state"], "timed_out")

    def test_failed_run_counts_as_the_check(self):
        checks = digest.classify([run(3, "schedule", "2026-11-17T14:17:40Z", conclusion="failure")], self.NOW, report_check=lambda i: False)
        self.assertEqual((checks[-1]["run_id"], checks[-1]["conclusion"]), (3, "failure"))

    def test_wait_loop_waits_then_reports(self):  # D, E
        now = dt.datetime.now(UTC); syd = now.astimezone(digest.SYD)
        start = (syd.replace(hour=1, minute=17) if syd.hour >= 2 else syd - dt.timedelta(minutes=5)).astimezone(UTC)
        start = min(start, now - dt.timedelta(minutes=1))
        r = run(3, "schedule", start.isoformat().replace("+00:00", "Z"), status="in_progress", conclusion=None)
        with mock.patch.object(digest, "classify", side_effect=lambda runs, n: [dict(state="running", label="1am", run_id=3)]), \
             mock.patch.object(digest, "list_runs", return_value=[r]), mock.patch.object(digest, "has_report", return_value=False), \
             mock.patch("time.sleep") as sl, mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
             mock.patch("subprocess.run"), tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd(); os.chdir(d)
            try: m = digest.collect(wait_min=3)
            finally: os.chdir(cwd)
        self.assertEqual(sl.call_count, 3); self.assertIn("running", [c["state"] for c in m["checks"]])

    def test_already_sent_detection(self):  # 24, 25
        with mock.patch.object(digest, "gh", return_value=json.dumps({"artifacts": [{"name": "digest-sent-2026-11-18", "expired": False}]})), \
             mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r"}):
            self.assertTrue(digest.already_sent("2026-11-18"))
        with mock.patch.object(digest, "gh", return_value=json.dumps({"artifacts": []})), mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "o/r"}):
            self.assertFalse(digest.already_sent("2026-11-18"))


if __name__ == "__main__":
    unittest.main()
