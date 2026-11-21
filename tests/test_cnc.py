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
        self.assertEqual(out, [{"name": "Jsr St57 Shadow", "sizes": 1, "from": 360.0, "url": "https://c/jsr-st57-shadow-18x8-5x120-wheel/"}])
        self.assertEqual(not_listed, 1)
