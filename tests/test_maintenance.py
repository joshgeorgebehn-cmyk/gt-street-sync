import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SHOPIFY_STORE", "x.myshopify.com")
import maintenance

META = '<meta name="description" content="The JSR ST57 is a sharp multi-spoke wheel designed for drivers who want a balance of clean styling and performance. Its slim, flowing spokes and deep concav">'


def prod(desc):
    return {"id": "gid://shopify/Product/1", "title": "JSR ST57 Shadow Chrome", "descriptionHtml": desc, "options": []}


class IntroPass(unittest.TestCase):
    def run_pass(self, desc, apply, dry=False):
        calls = []
        with mock.patch.object(maintenance, "cnc_index", return_value={("jsr st57 shadow chrome", "18x8", "5x120"): "https://c/p/"}), \
             mock.patch.object(maintenance, "products", return_value=iter([prod(desc)])), \
             mock.patch.object(maintenance, "http", return_value=(200, META)), mock.patch.object(maintenance, "CNC_DELAY", 0), \
             mock.patch.object(maintenance, "INTRO_APPLY", apply), mock.patch.object(maintenance, "DRY_RUN", dry), \
             mock.patch.object(maintenance, "gql", side_effect=lambda q, v: calls.append(v) or {"productUpdate": {"userErrors": []}}):
            lines, errors = maintenance.intro_pass()
        return "\n".join(lines), errors, calls

    def test_switch_off_writes_nothing_but_reports(self):
        text, errors, calls = self.run_pass("<p>Old.</p>", apply=False)
        self.assertEqual(calls, []); self.assertIn("that would be written: 1", text); self.assertIn("sharp multi-spoke wheel", text)

    def test_dry_run_writes_nothing_even_when_on(self):
        self.assertEqual(self.run_pass("<p>Old.</p>", apply=True, dry=True)[2], [])

    def test_apply_writes_intro_first_and_is_idempotent(self):
        _, _, calls = self.run_pass("<p>Old.</p>", apply=True)
        new = calls[0]["p"]["descriptionHtml"]
        self.assertTrue(new.startswith('<p class="cnc-intro">The JSR ST57 is a sharp'))
        self.assertTrue(new.endswith("<p>Old.</p>"))
        self.assertEqual(self.run_pass(new, apply=True)[2], [])


if __name__ == "__main__":
    unittest.main()
