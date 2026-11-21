import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SHOPIFY_STORE", "x.myshopify.com")
import sync

PRICE_SALE = ('<span class="price"><del aria-hidden="true"><span class="woocommerce-Price-amount amount"><bdi><span class="woocommerce-Price-currencySymbol">&#36;</span>990.00</bdi></span></del> '
              '<ins aria-hidden="true"><span class="woocommerce-Price-amount amount"><bdi><span class="woocommerce-Price-currencySymbol">&#36;</span>675.00</bdi></span></ins></span>\n<p class="instockc">In Stock</p>')
PRICE_ONE = ('<span class="price"><span class="woocommerce-Price-amount amount"><bdi><span class="woocommerce-Price-currencySymbol">&#36;</span>1,250.00</bdi></span></span>\n')


def card(cls, url, price):
    return f'<div class="product-grid-item product {cls}"><h3 class="product-title"><a href="{url}">T</a></h3>\n\n\t{price}\n</div>'


class Cards(unittest.TestCase):
    def test_price_and_state(self):
        html = ("<ul>" + card("status-publish instock", "https://c/a/", PRICE_SALE) + card("status-publish outofstock", "https://c/b/", PRICE_ONE)
                + card("status-publish onbackorder", "https://c/c/", PRICE_ONE))
        self.assertEqual(sync.cnc_cards(html), [("https://c/a/", 675.0, "in"), ("https://c/b/", 1250.0, "out"), ("https://c/c/", 1250.0, "backorder")])

    def test_card_without_price_is_not_ok_price(self):
        self.assertEqual(sync.cnc_cards(card("instock", "https://c/d/", ""))[0][1], None)


if __name__ == "__main__":
    unittest.main()


class NewOnCnc(unittest.TestCase):
    def test_new_ranges_found_among_newest(self):
        idx = {("jsr st57 shadow", "18x8", "5x120"): "https://c/jsr-st57-shadow-18x8-5x120-wheel/", ("jsr st21", "17x7", "4x100"): "https://c/jsr-st21-17x7-4x100-wheel/"}
        newest = [("https://c/jsr-st57-shadow-18x8-5x120-wheel/", 360.0, "in"), ("https://c/jsr-st21-17x7-4x100-wheel/", 300.0, "in")]
        from unittest import mock
        with mock.patch.object(sync, "cnc_newest", return_value=newest):
            out, not_listed = sync.new_cnc_ranges([{"title": "JSR ST21"}], idx)
        self.assertEqual(out, [{"name": "Jsr St57 Shadow", "range": "jsr st57 shadow", "sizes": 1, "from": 360.0, "url": "https://c/jsr-st57-shadow-18x8-5x120-wheel/"}])
        self.assertEqual(not_listed, 1)


class Title(unittest.TestCase):
    def test_title_from_cnc_heading(self):
        h = '<h1 class="product_title entry-title">BLACK ROCK CAGE DARK BRONZE WITH BLACK RING 22X10.5 6X139.7 WHEEL</h1>'
        self.assertEqual(sync.cnc_title(h), "Black Rock Cage Dark Bronze With Black Ring")
        self.assertEqual(sync.cnc_title('<h1 class="product_title entry-title">GT FORM HF5.1 HYBRID FORGED BRUSHED GUNMETAL 22X10.5 5X108 WHEEL</h1>'),
                         "GT Form HF5.1 Hybrid Forged Brushed Gunmetal")
        self.assertEqual(sync.cnc_title('<h1 class="product_title entry-title">JSR ST57 SHADOW CHROME 18X8 5X120 WHEEL</h1>'), "JSR ST57 Shadow Chrome")

    def test_product_input_is_a_draft_with_every_variant(self):
        spec = {"title": "JSR ST57 Shadow Chrome", "vendor": "JSR", "image": "https://c/i.jpg",
                "variants": [{"opts": ("18x8", "5x120", "+35"), "price": 359.0, "state": "in", "sku": "ab-P35"},
                             {"opts": ("18x8", "5x120", "+40"), "price": 359.0, "state": "out", "sku": ""}]}
        i = sync.product_input(spec, "gid://shopify/Location/1")
        self.assertEqual((i["status"], i["vendor"], len(i["variants"])), ("DRAFT", "JSR", 2))
        self.assertIn("status:review", i["tags"])
        self.assertEqual([v["inventoryQuantities"][0]["quantity"] for v in i["variants"]], [20, 0])
        self.assertNotIn("sku", i["variants"][1]["inventoryItem"])
