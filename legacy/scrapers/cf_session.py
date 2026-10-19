"""Headed Chrome clearance cache; never log cookie values."""
import json
import os
import time
from pathlib import Path

CACHE = Path(__file__).resolve().parent / ".cf_cache.json"
URL = "https://www.kci.id/"


def invalidate():
    CACHE.unlink(missing_ok=True)


def _read_cache():
    try:
        cached = json.loads(CACHE.read_text())
        if (cached["expires_at"] > time.time() + 60 and cached["cookies"].get("cf_clearance")
                and isinstance(cached["user_agent"], str) and cached["user_agent"].strip()):
            return cached["cookies"], cached["user_agent"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def get_session(force=False):
    """Return (cookie dict, exact browser UA). force=True opens a new browser."""
    if not force:
        cached = _read_cache()
        if cached:
            return cached
    invalidate()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        try:
            context = browser.new_context()
            page = context.new_page()
            user_agent = page.evaluate("navigator.userAgent")
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                browser_cookies = context.cookies(URL)
                clearance = next((c for c in browser_cookies if c["name"] == "cf_clearance" and c["value"]), None)
                if clearance and "just a moment" not in page.title().lower():
                    cookies = {c["name"]: c["value"] for c in browser_cookies}
                    expirations = [c["expires"] for c in browser_cookies if c.get("expires", -1) > 0]
                    expires_at = min([time.time() + 3600] + expirations)
                    temporary = CACHE.with_suffix(".tmp")
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    os.chmod(temporary, 0o600)
                    with os.fdopen(fd, "w") as handle:
                        json.dump({"cookies": cookies, "user_agent": user_agent, "expires_at": expires_at}, handle)
                    temporary.replace(CACHE)
                    return cookies, user_agent
                page.wait_for_timeout(1000)
            raise RuntimeError("No Cloudflare clearance after 180 seconds. Complete the challenge in the browser and retry.")
        finally:
            browser.close()
