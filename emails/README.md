# Shopify notification templates

order-confirmation.liquid is the live Order confirmation email (Settings > Notifications > Order confirmation).
It is built from Shopify's default template by build_order_confirmation.py (run it next to Shopify's default saved as
orig.liquid). Edit the settings block at the top of the template for phone, email, logo and GT Street Rewards dates.
After changing it here, paste it into Shopify again; Shopify does not read this file.
