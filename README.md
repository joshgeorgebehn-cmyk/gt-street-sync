# GT Street CNC sync

Keeps GT Street's NYC Wheels brands (GT Form, JSR, Black Rock) priced and stocked to match CNC Wheels, every day.

- **Price:** each variant is set to CNC's price for that rim size and PCD, minus $1 (`PRICE_OFFSET`).
- **Stock:** a size and PCD that CNC shows "Out of stock" becomes Sold out on GT Street (inventory tracked, 0 available). When CNC has it again, it becomes buyable (inventory not tracked).
- **How CNC is read:** price and stock for every product come from CNC's shop listing (about 285 pages, 5 minutes), cross-checked against 40 real product pages each run. If the listing looks incomplete or disagrees with the product pages, the run falls back to reading every product page (about 3 hours). The **Thursday 3:17am check is the weekly full sweep**: it reads every CNC product page (about 3 hours), the only way to find new offsets and sizes. Ticking `full_cnc` forces it on any day. Every run also looks at CNC's 240 newest products and loads any wheel range GT Street does not carry as a **DRAFT** product (up to 5 per run, `NEW_RANGE_LIMIT`; switch off with `CREATE_NEW_PRODUCTS=0`): CNC's title, every size, PCD and offset, price CNC less $1, stock as CNC shows, tagged `status:review`. Never published automatically; images, collections and a read-through are done by a person. The digest lists them under "New on CNC". A dry run (`dry_run` tick) reports what would be loaded and changes nothing. NYC Dealerline stock overrides CNC's stock for matched wheels, and the portal step runs first so CNC never flips it back.
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

## Other suppliers (suppliers.json)

Any supplier whose website runs on Shopify publishes its catalogue at `/products.json`. Each one is a single entry
in `suppliers.json`:

- `query`: which GT Street products belong to this supplier (Shopify search syntax, e.g. `vendor:"BC Racing"`)
- matching is on the exact SKU; `match_title: true` also matches on the exact product title
- `set_sku: true` replaces placeholder SKUs with the supplier's real part number
- price = supplier price minus `price_offset`; sold out at the supplier means Sold out here
- `enabled: false` turns a supplier off

Current: XForce, BC Racing and Alu-Cab on; Just Jap off. To test one supplier quickly, run the workflow with
`only` set to its id (e.g. `xforce`); CNC is skipped unless `cnc` is listed.

## Daily digest email

One email a day, about 10am Sydney, to `EMAIL_TO`. It covers **every scheduled check since yesterday 10am (four a day)**, plus any
manual runs started in between (labelled "manual").

**Flow.** `sync.yml` runs `sync.py` then `verify.py`; each run uploads a `sync-report` artifact (report.json, verify.json,
verify.md, report.md, channels.md, maintenance.md; kept 60 days). `email.yml` runs `digest.py collect`, which finds the two
expected scheduled runs by their Sydney start time, waits up to 60 minutes for one still running, downloads each artifact
and writes `manifest.json`. `notify.py` reads only that manifest (`DIGEST_MANIFEST`), validates every report, merges them,
and sends. There is no other input and no fallback to a stray report.json.

**Schedule and daylight saving.** Each local time has an AEDT and an AEST cron. `digest.py gate` judges the Sydney time the
cron was *due*, not when GitHub started it, so exactly one fires each day (tested for every day of a year, including the
switch days) and a late start still runs. The gate is its own small job, so ignored crons never queue behind a real run.
Checks run every 6 hours at 3:17am, 9:17am, 3:17pm and 9:17pm, off the hour when GitHub drops the most scheduled runs.

**No duplicates, automatic retry.** The digest tries at about 7:07, 7:37 and 8:07 or 8:37. The first attempt whose email is
accepted uploads a `digest-sent-YYYY-MM-DD` artifact; later attempts see it and stop. A failed send records nothing, so
the next attempt retries.

**What the email says.** Each expected check is shown as passed, failed, still running, missing, or with an unusable report
(missing, corrupted, incomplete, from another run, dry run, malformed prices). A check without a usable report is never
counted as zero changes. A variant changed in both checks shows once, from its first price to its last (stock likewise).
Missing or failed independent rechecks, mismatches, errors and held prices are "Need you" items.

**Delivery.** Microsoft 365 first, then SMTP, with `price_changes.csv` attached. Throttling, server errors and timeouts are
retried with backoff; bad credentials or permissions are not. If both fail, one GitHub issue per day is opened (or commented
on) with label `sync-attention`, and the job goes red. Issues are never closed automatically. `delivery.json` (in the
`digest-<run>` artifact and the job summary) records sign-in, acceptance and any error. "Accepted" means the mail server
took it; inbox delivery cannot be confirmed from here.

**Manual use (Actions, Daily digest email, Run workflow).** `mode: preview` builds the email and attaches `email.html`
without sending. `resend: true` sends even if today's has gone. The **Send test email** workflow sends a sample digest built
from `tests/fixtures`; it never touches Shopify.

**Settings.**
- Secrets: `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET` (expires; renew in Entra before it does). Optional SMTP:
  `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`.
- Variables (optional): `EMAIL_TO` (default joshua.b@gtstreet.au, will.k@gtstreet.au, billy@breadenmedia.com.au),
  `MS_SENDER` (default joshua.b@gtstreet.au; must be a real licensed mailbox).
- Entra app: Microsoft Graph **Mail.Send, Application** permission, with **admin consent granted**.
- Workflow permissions are set in the YAML: digest `contents: read, actions: read, issues: write`.

**Tests.** `python3 -m unittest discover -s tests -v` (41 tests, all external services mocked).

## Dealer portals (NYC Wheels and CoolDrive)

Logins are stored as GitHub Actions secrets only, never in code, README or logs. Secret names: `NYC_USER`, `NYC_PASS`,
`COOLDRIVE_USER`, `COOLDRIVE_PASS`.

Agreed rules, which the portal code must follow:
- **NYC Wheels: stock levels only.** It never sets or changes a price.
- **CoolDrive: stock and pricing.** Its prices and stock may both be applied, under the same rule as the other suppliers
  (supplier's shown price less $1, with the same 20% hold and minimum price protections).
- Neither portal is read until its terms have been checked for automated access.

## Dealer portal stock (portals.py)

Runs in every check after the supplier websites. NYC Dealerline gives stock for GT Form, JSR and Black Rock
(matched on size, PCD, offset and model words; prices stay with CNC). Equipit gives stock for the vendors in the
EQUIPIT_VENDORS variable (default Tough Dog), matched on exact part number. Equipit prices are dealer buy prices and
are never used for the "less $1" rule.

Review mode is the default: the portals are read and compared and the digest lists the differences, but nothing
changes. Set the repository variable PORTALS_APPLY to 1 to apply. A portal that cannot be read, or that matches
fewer than 20% of our variants, never changes anything. Test on its own with the sync workflow's "only" box set to
`portals`.

## Switches and dry runs (new features start switched off)

Repository variables (Settings > Secrets and variables > Actions > Variables). Unset means 0.

| Variable | Set to 1 to | Dry run behaviour |
|---|---|---|
| `CREATE_NEW_PRODUCTS` | load new CNC wheel ranges as DRAFT products (5 per run) | reports what would be loaded |
| `CNC_DESCRIPTIONS_APPLY` | write CNC's opening sentence into wheel descriptions | reports counts and examples |
| `PORTALS_APPLY`, `TD_PRICES_APPLY` | apply dealer portal stock and Tough Dog prices | portals read and compared only |

To try everything first: Actions > Supplier price and stock sync > Run workflow with `dry_run` ticked. Nothing changes in Shopify.
Read `report.md` (new products) and `maintenance.md` (CNC opening sentence examples) in the run's `sync-report` artifact.

To try the email: Actions > Daily digest email > Run workflow with mode `send_to_me`. It goes to joshua.b@gtstreet.au only, is labelled TEST,
and does not stop the real digest going out later. Dry run reports are never counted in the digest totals.

CNC has no product description field. The only copy on its pages is a short SEO snippet; the script keeps its first sentence when that
sentence is complete and not about one size (class `cnc-intro`, first paragraph) and leaves out snippets that are cut off or mention sizes or social handles.

## Store policies (policies.py)

`policies/*.md` are the source of truth for the Contact, Shipping, Terms of service and Privacy pages in Shopify.
Edit a file (bump its version and "Last updated" date), push to main, and the Store policies workflow updates
Shopify. It also runs every Monday: it compares the live pages with the files, checks required Australian
Consumer Law and privacy wording, flags any page older than 12 months and lists legal changes coming up, opening
a GitHub issue if anything needs attention. The refund policy is lawyer approved and edited in Shopify; it is
checked but never pushed. Pushing needs the `write_legal_policies` scope on the Shopify app.
