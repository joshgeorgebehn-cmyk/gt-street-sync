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

        # Privacy first: decline optional cookies if a banner offers it, otherwise leave it alone.
        for name in [r"reject", r"decline", r"necessary only", r"only necessary"]:
            btn = page.get_by_role("button", name=re.compile(name, re.I))
            for i in range(btn.count()):
                if btn.nth(i).is_visible():
                    btn.nth(i).click(timeout=5000)
                    page.wait_for_timeout(1000)
                    break

        # Open the sign-in form only if it is not already on screen. Click the first VISIBLE control.
        if not page.locator("input[type=password]").first.is_visible():
            clicked = False
            for role in ("button", "link"):
                controls = page.get_by_role(role, name=re.compile(r"log ?in|sign ?in", re.I))
                for i in range(controls.count()):
                    c = controls.nth(i)
                    if c.is_visible() and c.is_enabled():
                        c.click(timeout=15000)
                        clicked = True
                        break
                if clicked:
                    break
            if not clicked:
                sys.exit("equipit: no visible sign-in control found on the home page")

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

        # The submit button stays disabled until both fields have a value, so fill first, then submit.
        submit = form.locator("button[type=submit], input[type=submit]")
        if submit.count() and submit.first.is_enabled():
            submit.first.click(timeout=15000)
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
