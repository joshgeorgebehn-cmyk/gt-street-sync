"""
Sales channel coverage.

Makes sure every ACTIVE product is published to every sales channel the store
uses (Online Store, Point of Sale, Shop, Facebook & Instagram, TikTok, Google &
YouTube). Products imported by CSV or by supplier feeds often land on the
Online Store only, which leaves them out of Shop, Meta and TikTok catalogues.

Draft and archived products are never touched. Nothing is ever removed from a
channel. Set DRY_RUN=1 to report only.

Needs the app scopes read_publications and write_publications.
Optional: SKIP_CHANNELS, a comma separated list of words matched against the
channel catalogue title (for example "Point of Sale") to leave alone.
"""
import os, sys, json, time
from sync import gql, DRY_RUN

SKIP = [s.strip().lower() for s in os.environ.get("SKIP_CHANNELS", "").split(",") if s.strip()]


def main():
    granted = {x["handle"] for x in gql("{ currentAppInstallation { accessScopes { handle } } }")["currentAppInstallation"]["accessScopes"]}
    if "write_publications" not in granted:
        print("Channel check skipped: the app needs the write_publications scope.")
        return

    pubs = gql("{ publications(first: 20) { nodes { id catalog { title } } } }")["publications"]["nodes"]
    pubs = [p for p in pubs if not any(s in (p["catalog"] or {}).get("title", "").lower() for s in SKIP)]
    if not pubs:
        print("No channels to check."); return

    fields = " ".join(f'p{i}: publishedOnPublication(publicationId: "{p["id"]}")' for i, p in enumerate(pubs))
    q = f"""query($c: String) {{ products(first: 250, after: $c, query: "status:active") {{
      pageInfo {{ hasNextPage endCursor }} nodes {{ id {fields} }} }} }}"""

    missing = {p["id"]: [] for p in pubs}
    cursor, total = None, 0
    while True:
        d = gql(q, {"c": cursor})["products"]
        for n in d["nodes"]:
            total += 1
            for i, p in enumerate(pubs):
                if not n[f"p{i}"]:
                    missing[p["id"]].append(n["id"])
        if not d["pageInfo"]["hasNextPage"]:
            break
        cursor = d["pageInfo"]["endCursor"]

    lines = [f"# Sales channel coverage", "", f"- Mode: {'DRY RUN (nothing changed)' if DRY_RUN else 'LIVE'}",
             f"- Active products checked: {total}"]
    errors = []
    for p in pubs:
        ids = missing[p["id"]]
        name = (p["catalog"] or {}).get("title", p["id"]).split(" for ")[-1]
        lines.append(f"- {name}: {len(ids)} active products missing" + ("" if DRY_RUN or not ids else ", adding"))
        if DRY_RUN:
            continue
        # Shopify processes publication changes in the background and refuses new ones while too many are queued
        # ("limit for simultaneous publication updates"), so send small batches and wait and retry when refused.
        for k in range(0, len(ids), 50):
            for attempt in range(8):
                r = gql("""mutation($id: ID!, $add: [ID!]) { publicationUpdate(id: $id, input: { publishablesToAdd: $add }) {
                           userErrors { field message } } }""", {"id": p["id"], "add": ids[k:k + 50]})
                errs = r["publicationUpdate"]["userErrors"]
                if errs and any("simultaneous" in e["message"] for e in errs):
                    time.sleep(15 * (attempt + 1)); continue
                if errs: errors.append({"channel": name, "errors": errs})
                break
            else:
                errors.append({"channel": name, "errors": [{"message": "still busy after retries; will finish on the next run"}]})
            time.sleep(2)
    if errors:
        lines += ["", "## Errors"] + [f"- {json.dumps(e)[:300]}" for e in errors[:30]]
    out = "\n".join(lines) + "\n"
    print(out)
    with open("channels.md", "w") as f:
        f.write(out)


if __name__ == "__main__":
    main()
