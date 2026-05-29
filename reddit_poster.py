"""
reddit_poster.py
----------------
Automates Reddit post submissions using Playwright (WebKit).
Reads subreddits, post bodies, and titles from posts.json.
Runs 5 posting cycles automatically with detailed terminal logging.
All post history is logged directly into notitrivia_posting_tracker.csv.

Uses your existing Safari Reddit session via browser_cookie3.

SETUP:
    cd /Users/evanlevinsky/Desktop/Projects/NotiTrivia/NotiTriviaBots
    venv/bin/python reddit_poster.py
"""

import csv
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
POSTS_FILE  = "posts.json"
CSV_FILE    = "notitrivia_posting_tracker.csv"
NUM_CYCLES  = 5
BASE_URL    = "https://www.reddit.com"

CSV_HEADERS = ["Subreddit", "Category", "Post ID", "Title Used", "Timestamp", "Status", "Reddit Post URL"]

# CSS selectors
SEL_CREATE_POST = '[data-testid="create-post"]'
SEL_TITLE       = 'textarea[name="title"]'
SEL_BODY        = '[aria-label="Post body text field"][data-lexical-editor="true"]'
SEL_SUBMIT      = "#inner-post-submit-button"
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def random_delay(min_s: float = 3.0, max_s: float = 8.0) -> None:
    delay = random.uniform(min_s, max_s)
    log(f"  ⏳ Waiting {delay:.1f}s...")
    time.sleep(delay)


# ── CSV helpers ───────────────────────────────────────────────────────────────

def load_csv_rows() -> list[dict]:
    """Read all rows from the tracking CSV. Returns list of dicts."""
    if not os.path.exists(CSV_FILE):
        return []
    with open(CSV_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def get_posted_keys(rows: list[dict]) -> set:
    """
    Return a set of (subreddit_url, post_id_str) pairs that have already been
    successfully posted, so we don't repeat them.
    Rows without a Post ID (legacy / manual entries) are ignored for this check.
    """
    keys = set()
    for row in rows:
        pid = row.get("Post ID", "").strip()
        sub = row.get("Subreddit", "").strip()
        status = row.get("Status", "").strip().lower()
        if pid and sub and status == "posted":
            keys.add((sub, pid))
    return keys


def get_category_map(rows: list[dict]) -> dict:
    """Build a subreddit → category lookup from existing CSV rows."""
    mapping = {}
    for row in rows:
        sub = row.get("Subreddit", "").strip()
        cat = row.get("Category", "").strip()
        if sub and cat:
            mapping[sub] = cat
    return mapping


def append_csv_row(row: dict) -> None:
    """Append a single row dict to the CSV file."""
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ── Posting logic ─────────────────────────────────────────────────────────────

def pick_combination(data: dict, posted_keys: set) -> tuple:
    subreddits = data["subreddits"]
    posts = data["posts"]

    fresh = [
        (sub, post)
        for sub in subreddits
        for post in posts
        if (sub, str(post["id"])) not in posted_keys
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


def post_to_reddit(page, subreddit: str, title: str, body: str) -> tuple[bool, str]:
    """
    Attempt to post to the given subreddit.
    Returns (success: bool, reddit_post_url: str).
    """
    reddit_post_url = ""
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
            return False, ""
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
                return False, ""
        random_delay(1, 3)

        # ── Fill Body ──────────────────────────────────────────────────────
        log(f"  → Looking for body field ({SEL_BODY})...")
        try:
            page.wait_for_selector(SEL_BODY, timeout=15_000)
            log("    Found. Clicking and typing body...")
            body_field = page.locator(SEL_BODY)
            body_field.click()
            random_delay(0.8, 1.5)
            page.keyboard.type(body, delay=20)
            log(f"    Body typed ({len(body)} chars).")
        except PlaywrightTimeoutError:
            log("    ❌ Body field not found after 15s.")
            return False, ""
        random_delay(2, 4)

        # ── Submit ─────────────────────────────────────────────────────────
        log(f"  → Looking for submit button ({SEL_SUBMIT})...")
        try:
            page.wait_for_selector(SEL_SUBMIT, timeout=10_000)
            log("    Found. Clicking submit...")
            page.click(SEL_SUBMIT)
        except PlaywrightTimeoutError:
            log("    ❌ Submit button not found after 10s.")
            return False, ""

        log("    Submit clicked. Waiting for confirmation...")
        random_delay(3, 6)

        current_url = page.url
        log(f"    Current URL after submit: {current_url}")
        if "/comments/" in current_url:
            reddit_post_url = current_url
            log("    ✅ Post submitted successfully (redirected to post page).")
        else:
            log("    ⚠️  URL didn't redirect to a post page — post may or may not have gone through.")

        return True, reddit_post_url

    except PlaywrightTimeoutError as e:
        log(f"  ❌ Unexpected timeout: {e}")
        return False, ""
    except Exception as e:
        log(f"  ❌ Unexpected error: {e}")
        log(traceback.format_exc())
        return False, ""


def run_cycles(page, data: dict, csv_rows: list[dict]) -> None:
    posted_keys = get_posted_keys(csv_rows)
    category_map = get_category_map(csv_rows)

    successful = 0
    cycle = 1

    while successful < NUM_CYCLES:
        log(f"\n{'═'*62}")
        log(f"  CYCLE {cycle}  |  {successful}/{NUM_CYCLES} successful so far")
        log(f"{'═'*62}")

        subreddit, post, title = pick_combination(data, posted_keys)

        log(f"  Subreddit : {subreddit}")
        log(f"  Title     : {title}")
        log(f"  Post ID   : {post['id']}")
        log(f"  Body len  : {len(post['body'])} chars")

        ok, reddit_post_url = post_to_reddit(page, subreddit, title, post["body"])

        if ok:
            timestamp = datetime.datetime.now().isoformat(timespec="seconds")
            category  = category_map.get(subreddit, "Unknown")
            new_row = {
                "Subreddit":       subreddit,
                "Category":        category,
                "Post ID":         str(post["id"]),
                "Title Used":      title,
                "Timestamp":       timestamp,
                "Status":          "Posted",
                "Reddit Post URL": reddit_post_url,
            }
            append_csv_row(new_row)

            # Keep in-memory state current so we don't repeat in this session
            posted_keys.add((subreddit, str(post["id"])))

            successful += 1
            log(f"  ✅ Post {successful}/{NUM_CYCLES} logged to {CSV_FILE}")
            log(f"     [{timestamp}] → {subreddit}")
            if reddit_post_url:
                log(f"     Post URL: {reddit_post_url}")
        else:
            log("  ⚠️  Post failed or could not be confirmed. Moving to next cycle.")

        cycle += 1

        if successful < NUM_CYCLES:
            log("\n  Waiting before next cycle...")
            random_delay()

    log(f"\n{'═'*62}")
    log(f"  🎉 Done! {successful}/{NUM_CYCLES} posts completed.")
    log(f"  Full history saved in: {CSV_FILE}")
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

    playwright_cookies = []
    for c in cookies:
        domain = c.domain if c.domain else ".reddit.com"
        if not domain.startswith(".") and not domain.startswith("http"):
            domain = "." + domain
        cookie = {
            "name":     c.name,
            "value":    c.value,
            "domain":   domain,
            "path":     c.path if c.path else "/",
            "httpOnly": bool(c.has_nonstandard_attr("HttpOnly") or c.has_nonstandard_attr("httponly")),
            "secure":   bool(c.secure),
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

    data     = load_json(POSTS_FILE)
    csv_rows = load_csv_rows()

    posted_keys = get_posted_keys(csv_rows)
    log(f"Loaded {len(data['posts'])} posts, {len(data['subreddits'])} subreddits.")
    log(f"{len(posted_keys)} (subreddit, post) combos already posted (from {CSV_FILE}).")

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
        run_cycles(page, data, csv_rows)

        log("Script complete. Browser left open.")


if __name__ == "__main__":
    main()
