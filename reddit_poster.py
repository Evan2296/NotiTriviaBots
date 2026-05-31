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
SEL_FLAIR_BTN   = "#reddit-post-flair-button"

# Flair keyword preferences (checked in order; first match wins)
FLAIR_PREFERRED_KEYWORDS = [
    "general", "other", "ios", "question", "discussion",
    "app", "show", "showcase", "project", "share", "feedback",
]
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


def _pick_and_confirm_option(page, field_label: str) -> bool:
    """
    After a picker/dropdown has been opened, find all available options,
    prefer keywords from FLAIR_PREFERRED_KEYWORDS, fall back to random,
    click the choice, and try to confirm with an Apply/Save button.
    Returns True if an option was selected, False if nothing was found.
    """
    # Selectors to find picker items — covers flair buttons, tag lists,
    # role-based menus, and generic dialog buttons.
    option_selectors = [
        'button[id^="flair-button-"]',
        '[data-testid="flair-option"]',
        'shreddit-flair-button',
        '.flair-selection-list button',
        '[id^="post-flair-"] button',
        '[role="listbox"] [role="option"]',
        '[role="dialog"] li[role="option"]',
        '[role="menu"] [role="menuitem"]',
        'button[class*="flair"]',
        'button[class*="tag"]',
        '[data-testid="tag-option"]',
    ]

    options_with_text: list[tuple[str, object]] = []
    for sel in option_selectors:
        try:
            page.wait_for_selector(sel, timeout=4_000)
            found = page.locator(sel).all()
            if found:
                for opt in found:
                    try:
                        txt = opt.inner_text().strip()
                        # Sanity-check: skip empty or suspiciously long strings
                        if txt and len(txt) < 80:
                            options_with_text.append((txt.lower(), opt))
                    except Exception:
                        pass
                if options_with_text:
                    log(f"    Found {len(options_with_text)} option(s) via '{sel}'.")
                    break
        except PlaywrightTimeoutError:
            continue

    if not options_with_text:
        log(f"    ❌ No picker options found for '{field_label}'.")
        return False

    log(f"    Options: {[t for t, _ in options_with_text[:12]]}")

    # Prefer keyword match, else random
    chosen_elem = None
    chosen_label = ""
    for keyword in FLAIR_PREFERRED_KEYWORDS:
        for txt, elem in options_with_text:
            if keyword in txt:
                chosen_elem = elem
                chosen_label = txt
                break
        if chosen_elem:
            break

    if chosen_elem:
        log(f"    Keyword match → selecting: '{chosen_label}'")
    else:
        chosen_label, chosen_elem = random.choice(options_with_text)
        log(f"    No keyword match — random pick: '{chosen_label}'")

    chosen_elem.click()
    random_delay(0.5, 1.0)

    # Try to confirm / apply the selection
    apply_selectors = [
        '#flair-apply-button',
        'button[id="flair-apply-button"]',
        'button:has-text("Apply")',
        'button:has-text("Save")',
        'button:has-text("Done")',
        '[data-testid="flair-apply"]',
    ]
    for sel in apply_selectors:
        try:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click()
                log(f"    Selection confirmed via '{sel}'.")
                break
        except Exception:
            pass

    random_delay(0.5, 1.0)
    return True


def handle_required_extras(page) -> bool:
    """
    After filling title + body, scan the page for any remaining required
    fields indicated by buttons that contain a .text-danger-content child
    (the red asterisk Reddit uses for mandatory inputs).

    We always fill title and body — so any danger-content found on a *button*
    belongs to something else (flair, tag, post type, etc.) that we still need
    to handle.  Works generically for both flair pickers and tag selectors.

    Returns True if all required extras were handled (or none existed).
    Returns False only if a required field was found but we couldn't fill it.
    """
    try:
        # Find every button on the page that contains a required indicator.
        # Using :has() so we match the button, not just the span inside it.
        required_btns = page.locator("button:has(.text-danger-content)").all()

        if not required_btns:
            log("    ℹ️  No required extras detected — nothing extra to fill.")
            return True

        log(f"    ⚠️  {len(required_btns)} required extra field(s) found. Handling...")

        all_ok = True
        for i, btn in enumerate(required_btns, start=1):
            try:
                btn_text  = btn.inner_text().strip().replace("\n", " ")
                btn_id    = btn.get_attribute("id") or "(no id)"
                log(f"    → Field {i}: '{btn_text[:60]}' [id={btn_id}]")

                btn.scroll_into_view_if_needed()
                btn.click()
                random_delay(1.0, 2.0)

                ok = _pick_and_confirm_option(page, btn_text[:40])
                if not ok:
                    log(f"    ❌ Could not fill required field {i} — will try pressing Escape and continue.")
                    try:
                        page.keyboard.press("Escape")
                        random_delay(0.5, 1.0)
                    except Exception:
                        pass
                    all_ok = False

            except Exception as e:
                log(f"    ❌ Error on required field {i}: {e}")
                try:
                    page.keyboard.press("Escape")
                    random_delay(0.5, 1.0)
                except Exception:
                    pass
                all_ok = False

        if all_ok:
            log("    ✅ All required extras handled successfully.")
        else:
            log("    ⚠️  One or more required extras could not be filled — post may still go through.")

        return all_ok

    except Exception as e:
        log(f"    ❌ handle_required_extras error: {e}")
        log(traceback.format_exc())
        return False


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

        # ── Handle required extras: flair, tags, etc. ─────────────────────
        log("  → Checking for required flair/tags/extras...")
        extras_ok = handle_required_extras(page)
        if not extras_ok:
            log("    ❌ A required field could not be filled. Skipping post.")
            return False, ""

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


def run_all_subreddits(page, data: dict, csv_rows: list[dict]) -> None:
    """Post once to every subreddit in the list, in shuffled order."""
    posted_keys = get_posted_keys(csv_rows)
    category_map = get_category_map(csv_rows)

    subreddits = list(data["subreddits"])
    random.shuffle(subreddits)
    posts = data["posts"]

    total = len(subreddits)
    successful = 0

    for idx, subreddit in enumerate(subreddits, start=1):
        log(f"\n{'═'*62}")
        log(f"  SUBREDDIT {idx}/{total}  |  {successful} successful so far")
        log(f"{'═'*62}")

        # Pick a fresh post for this subreddit
        fresh_posts = [p for p in posts if (subreddit, str(p["id"])) not in posted_keys]
        if fresh_posts:
            post = random.choice(fresh_posts)
        else:
            post = random.choice(posts)

        title = random.choice(post["titles"])

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

            posted_keys.add((subreddit, str(post["id"])))
            successful += 1
            log(f"  ✅ Posted successfully ({successful} total). Logged to {CSV_FILE}")
            log(f"     [{timestamp}] → {subreddit}")
            if reddit_post_url:
                log(f"     Post URL: {reddit_post_url}")
        else:
            log("  ⚠️  Post failed or could not be confirmed. Moving to next subreddit.")

        if idx < total:
            log("\n  Waiting before next subreddit...")
            random_delay()

    log(f"\n{'═'*62}")
    log(f"  🎉 Done! {successful}/{total} subreddits posted successfully.")
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
        run_all_subreddits(page, data, csv_rows)

        log("Script complete. Browser left open.")


if __name__ == "__main__":
    main()
