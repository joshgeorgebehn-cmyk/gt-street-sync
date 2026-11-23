import datetime as dt
import policies


def test_sources_render_and_carry_dates():
    s = policies.sources()
    assert set(s) == {"CONTACT_INFORMATION", "PRIVACY_POLICY", "SHIPPING_POLICY", "TERMS_OF_SERVICE"}
    for typ, p in s.items():
        text = policies.plain(p["html"])
        assert "last updated" in text
        for phrase in policies.REQUIRED[typ]:
            assert phrase in text, (typ, phrase)
        for phrase in policies.FORBIDDEN:
            assert phrase not in text, (typ, phrase)
        assert "—" not in p["html"] and "–" not in p["html"]


def test_check_flags_drift_and_age(monkeypatch):
    s = policies.sources()
    live = {t: {"type": t, "body": v["html"]} for t, v in s.items()}
    live["REFUND_POLICY"] = {"type": "REFUND_POLICY", "body": "<p>Australian Consumer Law. major failure.</p><p>Version 1.0. Last updated 22/10/2026.</p>"}
    monkeypatch.setattr(policies, "live", lambda: live)
    assert [p for p in policies.check(dt.date(2026, 8, 1)) if not p.startswith(("Coming", "Since"))] == []
    live["CONTACT_INFORMATION"] = {"type": "CONTACT_INFORMATION", "body": "<p>Orders of A$10,000 or more ship free</p>"}
    found = policies.check(dt.date(2028, 1, 1))
    assert any("differs" in p for p in found) and any("a$10,000" in p for p in found)
    assert any("days ago" in p for p in found)
