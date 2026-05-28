"""
reddit_poster.py
----------------
Automates Reddit post submissions using Playwright (WebKit).
Reads subreddits, post bodies, and titles from posts.json.
Runs 5 posting cycles automatically with detailed terminal logging.
Logs all successful posts to post_log.json to avoid repeats.

Uses your existing Safari Reddit session via browser_cookie3.

SETUP:
    cd /Users/evanlevinsky/Desktop/Projects/NotiTrivia/NotiTriviaBots
    venv/bin/python reddit_poster.py
"""

import json
import random
import time
import datetime
import os
import signal
import sys
import traceback

import browser_cookie3
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ── Config ────────────────────────────────────────────────────────────────────
POSTS_FILE      = "posts.json"
LOG_FILE        = "post_log.json"
NUM_CYCLES      = 5
BASE_URL        = "https://www.reddit.com"

# CSS selectors
SEL_CREATE_POST = '[data-testid="create-post"]'
SEL_TITLE       = 'textarea[name="title"]'           # correct selector from DOM
SEL_BODY        = '[aria-label="Post body text field"][data-lexical-editor="true"]'
SEL_SUBMIT      = "#inner-post-submit-button"
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_log() -> list:
    if os.path.exists(LOG_FILE):
        return load_json(LOG_FILE)
    return []


def random_delay(min_s: float = 3.0, max_s: float = 8.0) -> None:
    delay = random.uniform(min_s, max_s)
    log(f"  ⏳ Waiting {delay:.1f}s...")
    time.sleep(delay)


def get_posted_keys(post_log: list) -> set:
    return {(entry["subreddit"], entry["post_id"]) for entry in post_log}


def pick_combination(data: dict, post_log: list) -> tuple:
    posted_keys = get_posted_keys(post_log)
    subreddits = data["subreddits"]
    posts = data["posts"]

    fresh = [
        (sub, post)
        for sub in subreddits
        for post in posts
        if (sub, post["id"]) not in posted_keys
    ]

    if fresh:
        subreddit, post = random.choice(fresh)
        log(f"  Picked fresh combo: Post #{post['id']} → {subreddit}")
    else:
        log("  ⚠️  All subreddit/post combos have been used. Picking randomly.")
        subreddit = random.choice(subreddits)
        post = random.choice(posts)

    title = random.choice(post["titles"])
    return subreddit, post, title


def post_to_reddit(page, subreddit: str, title: str, body: str) -> bool:
    try:
        # ── Navigate to subreddit ──────────────────────────────────────────
        log(f"  → Navigating to {subreddit}")
        page.goto(subreddit, wait_until="domcontentloaded", timeout=30_000)
        log("    Page loaded.")
        random_delay(2, 5)

        # ── Click "Create Post" ────────────────────────────────────────────
        log(f"  → Looking for Create Post button ({SEL_CREATE_POST})...")
        try:
            page.wait_for_selector(SEL_CREATE_POST, timeout=15_000)
            log("    Found. Clicking...")
            page.click(SEL_CREATE_POST)
        except PlaywrightTimeoutError:
            log("    ❌ Create Post button not found after 15s. Subreddit may require karma or mod approval.")
            return False
        random_delay(2, 4)

        # ── Fill Title ─────────────────────────────────────────────────────
        log(f"  → Looking for title field ({SEL_TITLE})...")
        try:
            page.wait_for_selector(SEL_TITLE, timeout=15_000)
            log(f"    Found. Typing title: \"{title}\"")
            title_field = page.locator(SEL_TITLE)
            title_field.click()
            random_delay(0.5, 1.5)
            title_field.fill(title)
            log("    Title filled.")
        except PlaywrightTimeoutError:
            log("    ❌ Title field not found after 15s.")
            # Try fallback selector
            log("    Trying fallback selector: #innerTextArea ...")
            try:
                page.wait_for_selector("#innerTextArea", timeout=8_000)
                title_field = page.locator("#innerTextArea")
                title_field.click()
                random_delay(0.5, 1)
                title_field.fill(title)
                log("    Title filled via fallback selector.")
            except PlaywrightTimeoutError:
                log("    ❌ Fallback title field also not found. Skipping post.")
                return False
        random_delay(1, 3)

        # ── Fill Body ──────────────────────────────────────────────────────
        log(f"  → Looking for body field ({SEL_BODY})...")
        try:
            page.wait_for_selector(SEL_BODY, timeout=15_000)
            log("    Found. Clicking and typing body...")
            body_field = page.locator(SEL_BODY)
            body_field.click()
            random_delay(0.8, 1.5)
            # Use keyboard.type for Lexical rich-text editor compatibility
            page.keyboard.type(body, delay=20)
            log(f"    Body typed ({len(body)} chars).")
        except PlaywrightTimeoutError:
            log("    ❌ Body field not found after 15s. Post may be text-only or requires different flow.")
            return False
        random_delay(2, 4)

        # ── Submit ─────────────────────────────────────────────────────────
        log(f"  → Looking for submit button ({SEL_SUBMIT})...")
        try:
            page.wait_for_selector(SEL_SUBMIT, timeout=10_000)
            log("    Found. Clicking submit...")
            page.click(SEL_SUBMIT)
        except PlaywrightTimeoutError:
            log("    ❌ Submit button not found after 10s.")
            return False

        log("    Submit clicked. Waiting for confirmation...")
        random_delay(3, 6)

        # Check if we landed on a post page (URL changes after successful submission)
        current_url = page.url
        log(f"    Current URL after submit: {current_url}")
        if "/comments/" in current_url:
            log("    ✅ Post submitted successfully (redirected to post page).")
        else:
            log("    ⚠️  URL didn't redirect to a post page — post may or may not have gone through.")

        return True

    except PlaywrightTimeoutError as e:
        log(f"  ❌ Unexpected timeout: {e}")
        return False
    except Exception as e:
        log(f"  ❌ Unexpected error: {e}")
        log(traceback.format_exc())
        return False


def run_cycles(page, data: dict, post_log: list) -> None:
    successful = 0
    cycle = 1

    while successful < NUM_CYCLES:
        log(f"\n{'═'*62}")
        log(f"  CYCLE {cycle}  |  {successful}/{NUM_CYCLES} successful so far")
        log(f"{'═'*62}")

        subreddit, post, title = pick_combination(data, post_log)

        log(f"  Subreddit : {subreddit}")
        log(f"  Title     : {title}")
        log(f"  Post ID   : {post['id']}")
        log(f"  Body len  : {len(post['body'])} chars")

        ok = post_to_reddit(page, subreddit, title, post["body"])

        if ok:
            entry = {
                "subreddit": subreddit,
                "post_id": post["id"],
                "title": title,
                "timestamp": datetime.datetime.now().isoformat()
            }
            post_log.append(entry)
            save_json(LOG_FILE, post_log)
            successful += 1
            log(f"  ✅ Post {successful}/{NUM_CYCLES} logged.")
            log(f"     [{entry['timestamp']}] → {subreddit}")
        else:
            log("  ⚠️  Post failed or could not be confirmed. Moving to next cycle.")

        cycle += 1

        if successful < NUM_CYCLES:
            log("\n  Waiting before next cycle...")
            random_delay()

    log(f"\n{'═'*62}")
    log(f"  🎉 Done! {successful}/{NUM_CYCLES} posts completed.")
    log(f"  Log saved to: {LOG_FILE}")
    log(f"{'═'*62}")


def get_safari_reddit_cookies() -> list:
    """
    Pull Reddit cookies from Safari using browser_cookie3.
    Returns a list of dicts in Playwright cookie format.
    """
    log("  → Extracting Reddit session from Safari...")
    try:
        cookiejar = browser_cookie3.safari(domain_name="reddit.com")
    except Exception as e:
        log(f"  ❌ Could not read Safari cookies: {e}")
        log("     Make sure Safari is installed and you're logged into Reddit in Safari.")
        log("     If you see a permissions error, grant Full Disk Access to Terminal in:")
        log("     System Settings → Privacy & Security → Full Disk Access")
        sys.exit(1)

    cookies = list(cookiejar)
    if not cookies:
        log("  ❌ No Reddit cookies found in Safari. Are you logged into Reddit in Safari?")
        sys.exit(1)

    # Convert http.cookiejar.Cookie → Playwright cookie format
    playwright_cookies = []
    for c in cookies:
        domain = c.domain if c.domain else ".reddit.com"
        if not domain.startswith(".") and not domain.startswith("http"):
            domain = "." + domain
        cookie = {
            "name": c.name,
            "value": c.value,
            "domain": domain,
            "path": c.path if c.path else "/",
            "httpOnly": bool(c.has_nonstandard_attr("HttpOnly") or c.has_nonstandard_attr("httponly")),
            "secure": bool(c.secure),
        }
        if c.expires and c.expires > 0:
            cookie["expires"] = float(c.expires)
        playwright_cookies.append(cookie)

    log(f"  ✅ Loaded {len(playwright_cookies)} Reddit cookies from Safari.")
    return playwright_cookies


def main():
    print("\n" + "═"*62)
    print("  NotiTrivia Reddit Auto-Poster")
    print("═"*62 + "\n")

    if not os.path.exists(POSTS_FILE):
        log(f"  ❌ {POSTS_FILE} not found. Run from the project directory.")
        sys.exit(1)

    data = load_json(POSTS_FILE)
    post_log = load_log()

    log(f"Loaded {len(data['posts'])} posts, {len(data['subreddits'])} subreddits.")
    log(f"{len(post_log)} previous posts in log.")

    safari_cookies = get_safari_reddit_cookies()

    with sync_playwright() as p:
        log("Launching WebKit browser...")
        browser = p.webkit.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Safari/605.1.15"
            )
        )

        context.add_cookies(safari_cookies)
        log("Safari cookies injected into browser context.")

        def handle_interrupt(sig, frame):
            log("\nInterrupted by user. Browser left open. Exiting.")
            sys.exit(0)
        signal.signal(signal.SIGINT, handle_interrupt)

        page = context.new_page()
        log(f"Opening {BASE_URL}...")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        random_delay(2, 4)

        log("Starting posting cycles...\n")
        run_cycles(page, data, post_log)

        log("Script complete. Browser left open.")


if __name__ == "__main__":
    main()
