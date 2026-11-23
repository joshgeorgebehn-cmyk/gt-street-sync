"""Builds the GT Street order confirmation from Shopify's default template (orig.liquid).

Only adds GT Street settings, styling, copy and two content blocks; every Shopify logic branch is kept.
"""
import pathlib
import sys

src = pathlib.Path("orig.liquid").read_text()
lines = src.split("\n")


def replace_once(text, old, new):
    count = text.count(old)
    if count < 1:
        sys.exit(f"not found: {old[:60]!r}")
    return text.replace(old, new)


SETTINGS = """{%- comment -%}
  GT Street order confirmation. Version 1.0, 23/11/2026.
  Based on Shopify's default template: all order, payment and delivery logic is unchanged.
  Edit only the settings below. Logo and accent colour: Settings > Notifications > Customise email templates.
{%- endcomment -%}
{%- assign gt_phone = '0414 854 221' -%}
{%- assign gt_hours = '8am to 6pm' -%}
{%- assign gt_email = 'sales@gtstreet.au' -%}
{%- assign gt_logo = 'https://cdn.shopify.com/s/files/1/0787/6739/0946/files/gt-street-logo.png?v=1792649268&width=540' -%}
{%- assign gt_logo_width = 180 -%}
{%- assign gt_site = 'https://gtstreet.au' -%}
{%- assign gt_instagram = 'https://instagram.com/gtstreet.au' -%}
{%- comment -%} GT Street Rewards. Set gt_rewards_on to false to remove the block from every email. {%- endcomment -%}
{%- assign gt_rewards_on = true -%}
{%- assign gt_rewards_teaser = true -%}
{%- assign gt_rewards_start = 20270101 -%}
{%- assign gt_rewards_start_text = '01/01/2027' -%}
{%- assign gt_rewards_end = 20270630 -%}
{%- assign gt_rewards_end_text = '30/06/2027' -%}
{%- assign gt_rewards_url = 'https://gtstreet.au/apps/rewards' -%}
{%- assign gt_rewards_info = 'https://gtstreet.au/pages/rewards' -%}
{%- assign gt_rewards_terms = 'https://gtstreet.au/apps/rewards/terms' -%}"""

text = lines[0] + "\n" + SETTINGS + "\n" + "\n".join(lines[1:])

# Title and opening copy (Australian English, plain and specific).
text = replace_once(text, "{% capture email_title %}\n  Thank you for your order!\n{% endcapture %}",
                    "{% capture email_title %}\n  {% if customer.first_name != blank %}Thanks {{ customer.first_name }}, your order is in{% else %}Thanks, your order is in{% endif %}\n{% endcapture %}")
text = text.replace("You’ll receive an email when your order is ready for pickup.",
                    "We will email you as soon as your order is ready to collect.")
text = text.replace("We're getting your order ready to be shipped. We will notify you when it has been sent.",
                    "We are preparing your order now and will email your tracking details as soon as it is dispatched.")
text = text.replace("we're getting your order ready for delivery.", "we are preparing your order for delivery.")
text = text.replace("You’ll get a confirmation email after completing your payment.",
                    "You will get a confirmation email once your payment is complete.")
text = text.replace("Your payment is being processed. You'll get an email when your order is confirmed.",
                    "Your payment is being processed. We will email you as soon as your order is confirmed.")
text = text.replace("we received your order. Your payment method will be charged when your order is ready to be fulfilled. We'll notify you when it's on its way.",
                    "we have received your order. Your payment method is charged when your order is ready to dispatch, and we will email you when it is on its way.")
text = text.replace("<p>You’ll receive separate emails for any gift cards.</p>", "<p>Gift cards arrive in a separate email.</p>")

STYLE = """    a, a:hover, a:active, a:visited { color: {{ shop.email_accent_color }}; }
    /* GT Street */
    body, .body { background: #f2f2f4 !important; }
    body, td, p, h1, h2, h3, h4 { font-family: 'Barlow', 'Helvetica Neue', Helvetica, Arial, sans-serif; }
    .header { background: #0b0b0d; border-top: 4px solid #d0102b; }
    .header__cell { padding: 22px 0 !important; }
    .shop-name__text, .shop-name__text a { color: #ffffff !important; font-family: 'Barlow Condensed', 'Arial Narrow', Arial, sans-serif; font-style: italic; font-weight: 800; text-transform: uppercase; letter-spacing: 0.02em; text-decoration: none; }
    .order-number__text, .po-number__text { color: #c9ccd1 !important; text-transform: uppercase; letter-spacing: 0.08em; font-size: 13px; }
    .content, .section, .footer { background: #ffffff; }
    .content__cell { padding-top: 32px !important; }
    .header { margin-bottom: 0 !important; }
    h2 { font-family: 'Barlow Condensed', 'Arial Narrow', Arial, sans-serif; font-style: italic; font-weight: 800; text-transform: uppercase; font-size: 28px; line-height: 1.1; color: #121214; }
    h3 { color: #121214; }
    .button__cell, .actions-buttons .button__cell--primary { background: #d0102b !important; border-radius: 4px; }
    .button__text { font-weight: 600; letter-spacing: 0.02em; }
    a, a:hover, a:active, a:visited { color: #d0102b; }
    .gt-box { background: #f6f6f8; border-radius: 6px; padding: 18px 20px; margin-top: 24px; }
    .gt-box--dark { background: #121214; color: #ececef; border-left: 4px solid #d0102b; }
    .gt-box--dark p, .gt-box--dark td { color: #c9ccd1; }
    .gt-box--dark a { color: #ffffff !important; }
    .gt-eyebrow { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.12em; color: #d0102b !important; margin: 0 0 6px; }
    .gt-h { font-family: 'Barlow Condensed', 'Arial Narrow', Arial, sans-serif; font-style: italic; font-weight: 800; text-transform: uppercase; font-size: 20px; line-height: 1.15; margin: 0 0 10px; color: #121214; }
    .gt-box--dark .gt-h { color: #ffffff; }
    .gt-step td { padding: 6px 0; vertical-align: top; font-size: 15px; line-height: 1.5; color: #45454d; }
    .gt-step__n { width: 28px; font-weight: 700; color: #d0102b !important; }
    .gt-btn { display: inline-block; background: #d0102b; color: #ffffff !important; text-decoration: none; font-weight: 600; padding: 12px 20px; border-radius: 4px; margin-top: 6px; }
    .gt-small { font-size: 13px; line-height: 1.5; color: #8a8a94 !important; margin: 10px 0 0; }
    .gt-foot p { font-size: 14px; line-height: 1.6; color: #45454d; }
    .gt-foot a { color: #121214 !important; font-weight: 600; }"""

text = replace_once(text, "    a, a:hover, a:active, a:visited { color: {{ shop.email_accent_color }}; }\n  </style>",
                    STYLE + "\n  </style>\n  <link href=\"https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@1,800&family=Barlow:wght@400;600;700&display=swap\" rel=\"stylesheet\">")

BLOCKS = """            {%- comment -%} GT Street: what happens next {%- endcomment -%}
            {%- unless has_pending_payment -%}
            <table class="row gt-box"><tr><td>
              <p class="gt-eyebrow">What happens next</p>
              <table class="row gt-step">
                <tr><td class="gt-step__n">1</td><td><b>Fitment check.</b> We check your order against your vehicle before anything is dispatched. If something needs confirming, we will contact you first.</td></tr>
                {%- if requires_shipping -%}
                <tr><td class="gt-step__n">2</td><td><b>Dispatch.</b> You will get an email with tracking as soon as your order leaves us.</td></tr>
                {%- endif -%}
                <tr><td class="gt-step__n">{% if requires_shipping %}3{% else %}2{% endif %}</td><td><b>Fitting.</b> Parts are supplied ready for your own mechanic or installer. Check the parts before fitting and keep the packaging until you are happy.</td></tr>
              </table>
            </td></tr></table>
            {%- endunless -%}

            {%- comment -%}
              GT Street Rewards. Shown only for paid Australian orders outside SA that include merchandise (not only gift cards).
              Before the start date a one line teaser shows (gt_rewards_teaser); after the end date nothing shows.
            {%- endcomment -%}
            {%- assign gt_today = 'now' | date: '%Y%m%d' | plus: 0 -%}
            {%- assign gt_state = shipping_address.province_code | default: billing_address.province_code | default: '' | upcase -%}
            {%- assign gt_country = shipping_address.country_code | default: billing_address.country_code | default: 'AU' | upcase -%}
            {%- assign gt_has_merch = false -%}
            {%- for gt_li in line_items -%}
              {%- unless gt_li.gift_card -%}{%- assign gt_has_merch = true -%}{%- break -%}{%- endunless -%}
            {%- endfor -%}
            {%- assign gt_rw_ok = false -%}
            {%- if gt_rewards_on and gt_has_merch and gt_country == 'AU' and gt_state != 'SA' -%}{%- assign gt_rw_ok = true -%}{%- endif -%}
            {%- if gt_rw_ok and gt_today >= gt_rewards_start and gt_today <= gt_rewards_end and has_pending_payment != true -%}
            <table class="row gt-box gt-box--dark"><tr><td>
              <p class="gt-eyebrow">GT Street Rewards</p>
              <p class="gt-h">This order counts towards your entries</p>
              <p>Every A$500 of qualifying spend from {{ gt_rewards_start_text }} to {{ gt_rewards_end_text }} earns one entry, and spend carries over between orders. Entries appear in your rewards account once this order is paid in full. Sign in with this email address to reveal them.</p>
              <a class="gt-btn" href="{{ gt_rewards_url }}">Reveal my entries</a>
              <p class="gt-small">Shipping and gift cards are not qualifying spend. Australian residents 18 and over, excluding SA. No newsletter sign up needed. <a href="{{ gt_rewards_terms }}">Terms apply</a>.</p>
            </td></tr></table>
            {%- elsif gt_rw_ok and gt_rewards_teaser and gt_today < gt_rewards_start -%}
            <table class="row gt-box gt-box--dark"><tr><td>
              <p class="gt-eyebrow">Coming {{ gt_rewards_start_text }}</p>
              <p style="margin:0;">GT Street Rewards: every A$500 you spend with us from {{ gt_rewards_start_text }} earns an entry to win a fuel voucher of up to A$200. <a href="{{ gt_rewards_info }}">How it works</a></p>
            </td></tr></table>
            {%- endif -%}
"""

marker = "            {% endif %}\n\n            </td>\n          </tr>\n        </table>\n      </center>\n    </td>\n  </tr>\n</table>\n\n          <table class=\"row section\">"
idx = text.find(marker)
if idx < 0:
    sys.exit("insertion point not found")
cut = idx + len("            {% endif %}\n")
text = text[:cut] + "\n" + BLOCKS + text[cut:]

FOOTER_OLD = """<p class="disclaimer__subtext">If you have any questions, reply to this email or contact us at <a href="mailto:{{ shop.email }}">{% if buyer_email_rtl == true %}<span dir="ltr" style="display: inline-block; unicode-bidi: isolate; font-size: inherit;">{% endif %}{{ shop.email }}{% if buyer_email_rtl == true %}</span>{% endif %}</a>.<!-- shopify-managed-taxes-footer --></p>"""
FOOTER_NEW = """<div class="gt-foot">
                <p class="gt-h" style="font-size:18px;">Questions about your order?</p>
                <p>Call <a href="tel:{{ gt_phone | remove: ' ' }}">{{ gt_phone }}</a> ({{ gt_hours }}) or email <a href="mailto:{{ gt_email }}">{{ gt_email }}</a>. Please quote order {{ order_name }}.</p>
                <p class="disclaimer__subtext" style="margin-top:14px;">GT Street Pty Ltd, East Maitland NSW. ABN 82 692 485 868. <a href="{{ gt_site }}">gtstreet.au</a> &nbsp;|&nbsp; <a href="{{ gt_instagram }}">Instagram</a><!-- shopify-managed-taxes-footer --></p>
              </div>"""
text = replace_once(text, FOOTER_OLD, FOOTER_NEW)

LOGO_OLD = """                    {%- if shop.email_logo_url %}
                      <img src="{{shop.email_logo_url}}" alt="{{ shop.name }}" width="{{ shop.email_logo_width }}">
                    {%- else %}"""
LOGO_NEW = """                    {%- if gt_logo != blank %}
                      <a href="{{ gt_site }}" style="text-decoration:none;"><img src="{{ gt_logo }}" alt="GT Street" width="{{ gt_logo_width }}" style="display:block; width:{{ gt_logo_width }}px; max-width:100%; height:auto; border:0; color:#ffffff; font-family:'Barlow Condensed','Arial Narrow',Arial,sans-serif; font-size:24px; font-style:italic; font-weight:800;"></a>
                    {%- elsif shop.email_logo_url %}
                      <img src="{{shop.email_logo_url}}" alt="{{ shop.name }}" width="{{ shop.email_logo_width }}">
                    {%- else %}"""
text = replace_once(text, LOGO_OLD, LOGO_NEW)
pathlib.Path("gtstreet-order-confirmation.liquid").write_text(text)
print("ok", len(text))
