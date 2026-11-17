"""Equipit (Unleashed storefront) sign-in probe, run in a headless browser.

Logs structure and outcome only: input types and names, the page path after sign-in, and how many
alert blocks appear. Never prints page text, values, tokens or screenshots.
"""
import os
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

HOME = "https://equipit.store.unleashedsoftware.com/home"


def main():
    user = os.environ.get("EQUIPIT_USER", "")
    password = os.environ.get("EQUIPIT_PASS", "")
    if not user or not password:
        sys.exit("equipit: secrets EQUIPIT_USER / EQUIPIT_PASS are not set")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(HOME, wait_until="networkidle", timeout=60000)

        # Open the sign-in form from whichever control offers it.
        opened = False
        for locator in [
            page.get_by_role("button", name=re.compile(r"log ?in|sign ?in", re.I)),
            page.get_by_role("link", name=re.compile(r"log ?in|sign ?in", re.I)),
        ]:
            if locator.count():
                locator.first.click()
                opened = True
                break
        if not opened:
            sys.exit("equipit: no sign-in control found on the home page")

        try:
            page.wait_for_selector("input[type=password]", timeout=20000)
        except PWTimeout:
            sys.exit("equipit: password field did not appear after opening sign-in")

        # Structure only: what fields the form has.
        inputs = page.eval_on_selector_all(
            "input",
            "els => els.filter(e => e.offsetParent !== null).map(e => [e.type, e.name, e.id, e.autocomplete])",
        )
        print("equipit: visible inputs (type, name, id, autocomplete):")
        for row in inputs:
            print("  ", row)

        pwd = page.locator("input[type=password]").first
        form = pwd.locator("xpath=ancestor::form[1]")
        user_field = form.locator("input[type=email], input[type=text], input:not([type])").first
        user_field.fill(user)
        pwd.fill(password)

        submit = form.locator("button[type=submit], input[type=submit]")
        if submit.count():
            submit.first.click()
        else:
            pwd.press("Enter")

        page.wait_for_timeout(8000)
        page.wait_for_load_state("networkidle", timeout=60000)

        still_shown = page.locator("input[type=password]").count() > 0 and page.locator("input[type=password]").first.is_visible()
        alerts = page.locator("[role=alert], .error, .validation-message").count()
        print(f"equipit: landed on {urlsplit(page.url).path or '/'}")
        print(f"equipit: sign-in form still shown: {still_shown}; alert blocks: {alerts}")
        browser.close()
        if still_shown:
            sys.exit("equipit: still on the sign-in form after submitting")
        print("equipit: signed in")


if __name__ == "__main__":
    main()
