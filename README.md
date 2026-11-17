# GT Street CNC sync

Keeps GT Street's NYC Wheels brands (GT Form, JSR, Black Rock) priced and stocked to match CNC Wheels, every day.

- **Price:** each variant is set to CNC's price for that rim size and PCD, minus $1 (`PRICE_OFFSET`).
- **Stock:** a size and PCD that CNC shows "Out of stock" becomes Sold out on GT Street (inventory tracked, 0 available). When CNC has it again, it becomes buyable (inventory not tracked).
- **Safety:** a new price under $150 (`MIN_PRICE`) or more than 30% below the current price (`MAX_DROP`) is held and listed in the report. Anything CNC does not list is left alone and reported. If more than 20% of CNC pages fail to load, nothing changes.
- **Report:** each run's summary is on the Actions run page, with `report.json` attached.
- Runs at 4:15am Sydney time. Run it any time from Actions > CNC price and stock sync > Run workflow.

## Setup

1. Repository secrets (Settings > Secrets and variables > Actions > Secrets):
   - `SHOPIFY_STORE`: `srbc11-w2.myshopify.com`
   - Either `SHOPIFY_ADMIN_TOKEN` (starts `shpat_`), or `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET` if the app gives a client ID and secret.
2. Run the workflow by hand with "Dry run" ticked and read the report.
3. When happy, add the repository variable `LIVE` = `true` (same page, Variables tab). Scheduled runs then make changes. Without it they only report.

## Notes

- Matching is by brand, model and finish (`norm.py`), with aliases in `sync.py` for names that differ between GT Street and CNC.
- The script identifies itself to CNC as `GTStreetStockSync` and reads pages slowly (2 at a time, 0.8 s pause).
- The theme shows "Stock checked" using the shop metafield `gt_sync.last_run`.

## Second source: Just Jap

Products tagged `supplier:justjap` are matched to Just Jap's public catalogue (justjap.com) on the exact SKU, priced at
Just Jap's price minus `JJ_PRICE_OFFSET` (defaults to `PRICE_OFFSET`, $1) and Sold out when Just Jap shows them sold out.
Floor for these is `JJ_MIN_PRICE` ($5); the 30% overnight drop check still applies. Switch it off with the variable
`JUST_JAP` = `0`.
