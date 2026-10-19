"""Lewati Cloudflare interstitial pakai Chrome asli (nodriver), lalu pakai
cookie hasilnya untuk request cepat via curl_cffi.

Cookie cf_clearance terikat ke IP + User-Agent yang memperolehnya, dan umurnya
pendek. Modul ini men-cache cookie ke disk dan hanya membuka Chrome kembali
saat cache kosong atau sudah ditolak.
"""

import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

CHROME = os.environ.get("CHROME_PATH") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CACHE_FILE = ".cf_session.json"
CACHE_MAX_AGE = 20 * 60          # detik; cf_clearance biasanya hidup lebih lama
CHALLENGE_TIMEOUT = 45           # detik menunggu challenge selesai


def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() - data.get("ts", 0) > CACHE_MAX_AGE:
        return None
    if not data.get("cookies", {}).get("cf_clearance"):
        return None
    return data


def _save_cache(cookies, user_agent):
    data = {"cookies": cookies, "user_agent": user_agent, "ts": time.time()}
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)
    os.chmod(CACHE_FILE, 0o600)   # kredensial sesi - jangan dibaca user lain


async def _solve(url, headless):
    import nodriver

    browser = await nodriver.start(
        headless=headless,
        browser_executable_path=CHROME,
        browser_args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        tab = await browser.get(url)

        # Tunggu sampai judul halaman bukan lagi "Just a moment..."
        deadline = time.time() + CHALLENGE_TIMEOUT
        while time.time() < deadline:
            await tab.sleep(2)
            title = (await tab.evaluate("document.title") or "").lower()
            if "just a moment" not in title and "checking" not in title:
                break

        user_agent = await tab.evaluate("navigator.userAgent")
        raw = await browser.cookies.get_all()
        cookies = {
            c.name: c.value for c in raw
            if "kci.id" in (c.domain or "")
        }
        return cookies, user_agent
    finally:
        browser.stop()   # sinkron di nodriver 0.50.x


def get_session(url="https://www.kci.id/", force=False, headless=False):
    """Returns (cookies dict, user_agent str). Pakai cache kalau masih segar."""
    if not force:
        cached = _load_cache()
        if cached:
            return cached["cookies"], cached["user_agent"]

    if not os.path.exists(CHROME):
        raise RuntimeError(f"Chrome tidak ditemukan di {CHROME}")

    print("[CF] Membuka Chrome untuk menyelesaikan challenge...")
    cookies, user_agent = asyncio.run(_solve(url, headless))

    if not cookies.get("cf_clearance"):
        raise RuntimeError(
            "Challenge tidak menghasilkan cf_clearance. "
            "Coba headless=False, atau selesaikan CAPTCHA manual di jendela yang terbuka."
        )

    print(f"[CF] Dapat cookie: {', '.join(sorted(cookies))}")
    _save_cache(cookies, user_agent)
    return cookies, user_agent


def invalidate():
    """Hapus cache supaya pemanggilan berikutnya solve ulang."""
    try:
        os.remove(CACHE_FILE)
    except OSError:
        pass


if __name__ == "__main__":
    c, ua = get_session(force=True)
    print("cf_clearance:", (c.get("cf_clearance") or "")[:40] + "...")
    print("user_agent:", ua)
