import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import portals as P

ROW = ('<tr class="BuyingTableRow"><td class="desktop BuyingTableProductCode"><div class="word-break">ABCD1234567</div></td>'
       '<td class="desktop text-center"><div><div><img class="img-fluid w-100 make-image"></div><div>JSR</div></div></td>'
       '<td class="desktop BuyingTableDesc"><div class="text-break">{desc}</div></td>'
       '<td class="desktop TirePopup product-stock-cell"><span class="product-display-quantity">{qty}</span></td></tr>')


def variant(size, pcd, off, qty=20, policy="DENY"):
    return {"id": f"v{size}{pcd}{off}", "sku": "ITM1", "item": f"i{size}{pcd}{off}", "qty": qty, "tracked": True,
            "policy": policy, "opts": {"Rim Size": size, "PCD": pcd, "Offset": off}}


class Portals(unittest.TestCase):
    def test_rows_parse(self):
        rows = P.parse_nyc_rows(ROW.format(desc="JSR ST21 18x8.5 5x112 +35 Black Tinted", qty="3"))
        self.assertEqual(rows[0]["qty"], 3)
        self.assertEqual(rows[0]["make"], "JSR")

    def test_wheel_keys(self):
        self.assertEqual(P.wheel_key("GT FORM TORQUE 20X8.5 5X114.3 ET15"), ("20x8.5", "5x114.3", "+15"))
        self.assertEqual(P.our_wheel_key(variant("17x8.0", "6x139.7", "0")), ("17x8", "6x139.7", "+0"))

    def test_match_needs_model_words(self):
        rows = P.parse_nyc_rows(ROW.format(desc="JSR ST21 18x8.5 5x112 +35 Black Tinted", qty="9")
                                + ROW.format(desc="JSR ST25 18x8.5 5x112 +35 Black Tinted", qty="0"))
        prods = [{"title": "JSR ST25 Black Tinted", "vendor": "JSR", "variants": [variant("18x8.5", "5x112", "+35")]}]
        m, u, a, _ = P.match_nyc(rows, prods)
        self.assertEqual((len(m), u, a), (1, 0, 0))
        self.assertEqual(m[0][2]["qty"], 0)

    def test_ambiguous_left_alone(self):
        rows = P.parse_nyc_rows(ROW.format(desc="JSR ST21 18x8.5 5x112 +35 Black", qty="1") * 2)
        prods = [{"title": "JSR ST21", "vendor": "JSR", "variants": [variant("18x8.5", "5x112", "+35")]}]
        self.assertEqual(P.match_nyc(rows, prods)[2], 1)

    def test_states(self):
        self.assertEqual(P.state_from_qty(12), ("in", P.STOCK_QTY))
        self.assertEqual(P.state_from_qty(4), ("low", 4))
        self.assertEqual(P.state_from_qty(0), ("out", 0))
        self.assertEqual(P.state_from_qty(0, sellable_when_out=True), ("backorder", 0))

    def test_compare_counts_changes_only(self):
        p = {"id": "p", "title": "T", "tags": [], "handle": "h"}
        v1, v2 = variant("18x8", "5x112", "+35", qty=20), variant("19x8", "5x112", "+35", qty=20)
        rep = {"would_change": 0, "examples": []}
        targets, pol = P.compare("x", [(p, v1, ("in", 20)), (p, v2, ("out", 0))], rep)
        self.assertEqual(rep["would_change"], 1)
        self.assertEqual(targets[v2["item"]], 0)

    def test_own_stock_skipped(self):
        p = {"id": "p", "title": "T", "tags": ["own-stock"], "handle": "h"}
        rep = {"would_change": 0, "examples": []}
        targets, _ = P.compare("x", [(p, variant("18x8", "5x112", "+35"), ("out", 0))], rep)
        self.assertEqual(targets, {})


if __name__ == "__main__":
    unittest.main()


class NycCodeMatch(unittest.TestCase):
    def test_exact_code_match_wins(self):
        import portals
        rows = [{"code": "ITM74433-2-P0", "desc": "x", "make": "", "qty": 3}]
        prods = [{"title": "GT Form Hearts", "vendor": "GT Form", "variants": [{"sku": "ITM74433-2-P0", "opts": {}}, {"sku": "ITM1-P5", "opts": {}}]}]
        matched, unmatched, amb, _ = portals.match_nyc(rows, prods)
        self.assertEqual(len(matched), 1)
        self.assertEqual(unmatched, 1)
