#!/usr/bin/env python3
"""
Polite scraper for https://www.4x4community.co.za/forum — Tracks4Africa subforum.
Uses the forum's own search engine (search.php) rather than scraping listing pages.

Usage:
    uv run main.py "Moremi" "Nxai Pan"
    uv run main.py "Sani Pass" "Lesotho" --content
"""

import os
import re
import time
import json
import random
import hashlib
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from urllib.robotparser import RobotFileParser

from curl_cffi import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.text import Text

load_dotenv()

console = Console(highlight=False)

BASE_URL    = "https://www.4x4community.co.za/forum"
SEARCH_URL  = f"{BASE_URL}/search.php"
FORUM_ID    = "247"   # Tracks4Africa-ONLY subforum

CACHE_DIR = Path.home() / ".cache" / "tracks4africa-forum-scraper"
CACHE_TTL = timedelta(hours=2)
MAX_RETRIES = 3

# curl_cffi handles the User-Agent and TLS fingerprint automatically via impersonate="chrome120".
# These headers are supplementary — the UA below is overridden by curl_cffi.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": BASE_URL,
}

# Extra headers needed for POSTs (search, login) to pass CSRF checks
POST_HEADERS = {
    **HEADERS,
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.4x4community.co.za",
    "Referer": SEARCH_URL,
}


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / (hashlib.md5(key.encode()).hexdigest() + ".json")


def cache_get(key: str) -> Optional[str]:
    path = _cache_path(key)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if datetime.now() - datetime.fromisoformat(data["fetched_at"]) > CACHE_TTL:
        return None
    return data["html"]


def cache_set(key: str, html: str):
    _cache_path(key).write_text(
        json.dumps({"fetched_at": datetime.now().isoformat(), "html": html})
    )


# ── Polite delay ──────────────────────────────────────────────────────────────

def polite_delay(base: float):
    """Sleep for base ± 30% jitter, animating a countdown so the user can see we're waiting."""
    wait = max(1.0, base + random.uniform(-base * 0.3, base * 0.3))
    with Live(console=console, refresh_per_second=8) as live:
        start = time.monotonic()
        while True:
            remaining = wait - (time.monotonic() - start)
            if remaining <= 0:
                break
            live.update(Text(f"  ⏳  Being polite — next request in {remaining:.1f}s", style="dim"))
            time.sleep(0.05)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def get(url: str, session: requests.Session, cache_key: Optional[str] = None) -> Optional[BeautifulSoup]:
    if cache_key:
        cached = cache_get(cache_key)
        if cached:
            console.print("  [dim](from cache)[/dim]")
            return BeautifulSoup(cached, "html.parser")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 429:
                wait = 30 * attempt
                console.print(f"\n  [yellow]⚠  Rate limited — backing off {wait}s[/yellow]")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            if cache_key:
                cache_set(cache_key, resp.text)
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 10 * attempt
                console.print(f"\n  [yellow]⚠  {e} — retry {attempt}/{MAX_RETRIES} in {wait}s[/yellow]")
                time.sleep(wait)
            else:
                console.print(f"\n  [red]✗  Failed after {MAX_RETRIES} attempts: {e}[/red]")
    return None


def post(url: str, data: dict, session: requests.Session) -> Optional[requests.Response]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(url, data=data, headers=POST_HEADERS, timeout=15, allow_redirects=True)
            if resp.status_code == 429:
                wait = 30 * attempt
                console.print(f"\n  [yellow]⚠  Rate limited — backing off {wait}s[/yellow]")
                time.sleep(wait)
                continue
            if resp.status_code == 403:
                console.print(f"\n  [bold red]✗  403 Forbidden — your CF_CLEARANCE cookie has expired.[/bold red]")
                console.print("  Refresh it: browser DevTools → Cookies → www.4x4community.co.za → cf_clearance")
                console.print("  Then update [bold]CF_CLEARANCE[/bold] in your .env file and re-run.\n")
                return None
            resp.raise_for_status()
            return resp
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = 10 * attempt
                console.print(f"\n  [yellow]⚠  {e} — retry {attempt}/{MAX_RETRIES} in {wait}s[/yellow]")
                time.sleep(wait)
            else:
                console.print(f"\n  [red]✗  Failed after {MAX_RETRIES} attempts: {e}[/red]")
    return None


# ── vBulletin auth ────────────────────────────────────────────────────────────

def get_security_token(session: requests.Session, url: str = BASE_URL) -> str:
    """vBulletin requires a CSRF-style security token on POSTs."""
    soup = get(url, session)
    if soup:
        match = re.search(r"SECURITYTOKEN\s*=\s*['\"]([^'\"]+)['\"]", str(soup))
        if match:
            return match.group(1)
        tag = soup.find("input", {"name": "securitytoken"})
        if tag:
            return tag.get("value", "guest")
    return "guest"


def login(session: requests.Session, username: str, password: str) -> bool:
    """Log in to the forum and return True on success."""
    login_url = f"{BASE_URL}/login.php"

    console.print(f"  Logging in as [cyan]{username}[/cyan]...", end=" ")
    token = get_security_token(session, login_url)

    data = {
        "do": "login",
        "vb_login_username": username,
        "vb_login_password": password,
        "securitytoken": token,
        "cookieuser": "1",
    }

    resp = post(f"{login_url}?do=login", data, session)
    if not resp:
        console.print("[red]✗  Request failed[/red]")
        return False

    # vBulletin sets a userid cookie on success
    logged_in = "userid" in session.cookies or "bb_userid" in session.cookies
    if logged_in:
        console.print("[green]✓[/green]")
    else:
        console.print("[red]✗  Wrong credentials or login blocked[/red]")
    return logged_in


# ── vBulletin search ──────────────────────────────────────────────────────────

def search_forum(keyword: str, session: requests.Session, days: int = 0) -> Optional[str]:
    """
    POST a search to vBulletin and return the searchid.
    Searches post content (not just titles) within forum 247.
    Results are sorted newest-first by default.
    days=0 means no date filter; days=90 means last 90 days only.
    """
    token = get_security_token(session, BASE_URL)

    data = {
        "do": "process",
        "query": keyword,
        "titleonly": "0",        # search post body, not just thread titles
        "forumchoice[]": FORUM_ID,
        "childforums": "1",
        "showposts": "0",        # group by thread, not individual posts
        "sortby": "dateline",    # sort by post date
        "order": "descending",   # newest first
        "searchdate": str(days) if days > 0 else "0",
        "beforeafter": "after",  # threads newer than searchdate
        "searchsubmit": "1",
        "securitytoken": token,
    }

    resp = post(SEARCH_URL, data, session)
    if not resp:
        return None

    # searchid appears in the redirect URL or page content
    for text in (resp.url, resp.text):
        match = re.search(r"searchid=(\d+)", text)
        if match:
            return match.group(1)

    return None


def results_url(searchid: str, page: int) -> str:
    offset = (page - 1) * 25  # vBulletin uses offset-based pagination
    return f"{SEARCH_URL}?searchid={searchid}&pp=25&page={page}"


# ── Parsing search results ────────────────────────────────────────────────────

def parse_search_results(soup: BeautifulSoup) -> list[dict]:
    """
    vBulletin search results live in <ol id="searchbits"> or similar.
    Each result is an <li> with thread title, date, and reply count.
    """
    results = []

    # Try <ol id="searchbits"> (vBulletin 4.x)
    container = soup.find(id="searchbits") or soup.find(id="searchresults")
    items = container.find_all("li", recursive=False) if container else []

    # Fallback: look for threadresult or searchresult class anywhere
    if not items:
        items = soup.find_all("li", class_=re.compile(r"searchresult|threadresult"))

    for item in items:
        title_tag = item.find("h3") or item.find(class_=re.compile(r"threadtitle|subject"))
        if not title_tag:
            continue
        link = title_tag.find("a")
        if not link:
            continue

        href = link.get("href", "")
        if href and not href.startswith("http"):
            href = BASE_URL + "/" + href.lstrip("/")

        # Date: look for a <span> with a time-like pattern
        date_tag = item.find("span", class_=re.compile(r"time|date|postdate"))
        if not date_tag:
            # Sometimes it's just text that looks like a date
            date_tag = item.find(string=re.compile(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d+ \w+ \d{4}"))

        date_str = date_tag.get_text(strip=True) if hasattr(date_tag, "get_text") else (str(date_tag).strip() if date_tag else "—")

        # Replies
        replies_tag = item.find(class_=re.compile(r"replycount|replies|threadstats"))
        replies_text = replies_tag.get_text(strip=True) if replies_tag else "?"
        # Extract just the number if mixed with label text
        reply_match = re.search(r"\d+", replies_text)
        replies = reply_match.group() if reply_match else "?"

        # Preview snippet
        preview_tag = item.find(class_=re.compile(r"preview|searchresult_preview|postcontent"))
        preview = preview_tag.get_text(separator=" ", strip=True)[:300] if preview_tag else ""

        results.append({
            "title": link.get_text(strip=True),
            "url": href,
            "last_post": date_str,
            "replies": replies,
            "preview": preview,
        })

    return results


def has_next_page(soup: BeautifulSoup) -> bool:
    return bool(soup.find("a", rel="next") or soup.find("a", title=re.compile(r"next page", re.I)))


def fetch_thread_posts(url: str, session: requests.Session, n: int = 3) -> list[str]:
    soup = get(url, session, cache_key=url)
    if not soup:
        return []
    posts = []
    for body in soup.find_all(class_=re.compile(r"postcontent|post-content|postbody|post_body"), limit=n):
        text = body.get_text(separator=" ", strip=True)
        posts.append(text[:600] + ("..." if len(text) > 600 else ""))
    return posts


# ── Orchestration ─────────────────────────────────────────────────────────────

def run(places: list[str], max_pages: int, show_content: bool, delay: float,
        username: Optional[str] = None, password: Optional[str] = None, days: int = 0):
    # impersonate="chrome120" gives us a real Chrome TLS fingerprint,
    # which is required to pass Cloudflare Bot Management on search.php
    session = requests.Session(impersonate="chrome120")

    console.rule("[bold]Tracks4Africa Forum — Condition Scraper[/bold]")
    console.print()
    console.print(f"  Searching  : [cyan]{', '.join(places)}[/cyan]")
    console.print(f"  Subforum   : Tracks4Africa-ONLY (ID {FORUM_ID})")
    console.print(f"  Max pages  : {max_pages} per keyword")
    console.print(f"  Date filter: {'last ' + str(days) + ' days' if days > 0 else 'any date'}")
    console.print(f"  Sort order : newest first")
    console.print(f"  Delay      : ~{delay}s between requests (±30% jitter)")
    console.print(f"  Cache      : {CACHE_DIR}  [dim](TTL {int(CACHE_TTL.total_seconds() / 3600)}h)[/dim]")
    console.print()

    # ── Credentials & Cloudflare cookie ──────────────────────────────────────
    username = username or os.environ.get("FORUM_USERNAME")
    password = password or os.environ.get("FORUM_PASSWORD")

    if not username or not password:
        console.print("  [yellow]⚠  No credentials provided.[/yellow]")
        console.print("  Set [bold]FORUM_USERNAME[/bold] and [bold]FORUM_PASSWORD[/bold] env vars,")
        console.print("  or pass [bold]--username[/bold] / [bold]--password[/bold] on the command line.")
        console.print("  (The forum requires login to use its search.)\n")
        return

    # Cloudflare issues a cf_clearance cookie to browsers that pass its JS challenge.
    # search.php requires this — without it every POST returns 403.
    cf_clearance = os.environ.get("CF_CLEARANCE")
    if not cf_clearance:
        console.print("  [bold red]✗  CF_CLEARANCE is not set.[/bold red]")
        console.print()
        console.print("  The forum's search page is behind Cloudflare and requires this cookie.")
        console.print("  To get it:")
        console.print("    1. Open the forum in your browser and log in")
        console.print("    2. Open DevTools → Application (Chrome) / Storage (Safari) → Cookies")
        console.print("    3. Find [bold]cf_clearance[/bold] under www.4x4community.co.za")
        console.print("    4. Copy its value and add it to your [bold].env[/bold] file:")
        console.print()
        console.print("       [dim]CF_CLEARANCE=paste_value_here[/dim]")
        console.print()
        return

    session.cookies.set("cf_clearance", cf_clearance, domain=".4x4community.co.za")
    console.print("  [dim]cf_clearance cookie loaded ✓[/dim]")

    polite_delay(delay)
    if not login(session, username, password):
        return

    console.print()

    # robots.txt check
    console.print("  Checking [italic]robots.txt[/italic]...", end=" ")
    rp = RobotFileParser()
    rp.set_url("https://www.4x4community.co.za/robots.txt")
    try:
        resp = session.get("https://www.4x4community.co.za/robots.txt", headers=HEADERS, timeout=10)
        rp.parse(resp.text.splitlines())
        if not rp.can_fetch(HEADERS["User-Agent"], SEARCH_URL):
            console.print("[bold red]DISALLOWED[/bold red]")
            console.print("\n  [red]robots.txt disallows this. Aborting out of respect.[/red]")
            return
        console.print("[green]allowed ✓[/green]")
    except Exception:
        console.print("[yellow]couldn't fetch (assuming allowed)[/yellow]")

    console.print()

    all_results: dict[str, list[dict]] = {}

    for keyword in places:
        console.rule(f"[bold]Searching: {keyword}[/bold]")
        console.print()

        console.print(f"  Submitting search to forum...", end=" ")
        polite_delay(delay)
        searchid = search_forum(keyword, session, days=days)

        if not searchid:
            all_results[keyword] = []
            continue

        console.print(f"[green]searchid {searchid} ✓[/green]")
        console.print()

        keyword_results = []

        for page in range(1, max_pages + 1):
            url = results_url(searchid, page)
            console.print(f"  [bold]Results page {page}[/bold] → fetching...", end=" ")
            soup = get(url, session)
            if not soup:
                console.print("[red]failed.[/red]")
                break

            results = parse_search_results(soup)
            console.print(f"[dim]{len(results)} thread(s)[/dim]")
            keyword_results.extend(results)

            if not has_next_page(soup) or not results:
                console.print(f"  [dim]No more pages.[/dim]")
                break

            if page < max_pages:
                polite_delay(delay)

        all_results[keyword] = keyword_results
        console.print()

    # ── Results ───────────────────────────────────────────────────────────────

    total = sum(len(v) for v in all_results.values())
    console.rule(f"[bold]Results — {total} thread(s) found[/bold]")
    console.print()

    if total == 0:
        console.print("  [yellow]No results. The forum search may require a login, or try different terms.[/yellow]")
        return

    for keyword, results in all_results.items():
        if not results:
            continue
        console.print(f"  [bold underline]{keyword}[/bold underline] — {len(results)} thread(s)\n")

        for i, t in enumerate(results, 1):
            console.print(f"  [bold cyan][{i}][/bold cyan] [bold]{t['title']}[/bold]")
            console.print(f"       Last post : {t['last_post']}  |  Replies: {t['replies']}")
            console.print(f"       [link={t['url']}][blue]{t['url']}[/blue][/link]")

            if t.get("preview"):
                console.print(f"       [dim]{t['preview']}[/dim]")

            if show_content:
                polite_delay(delay)
                posts = fetch_thread_posts(t["url"], session)
                if posts:
                    console.print()
                    for j, p in enumerate(posts, 1):
                        console.print(f"       [dim][Post {j}][/dim] {p}")

            console.print()

    console.rule("[dim]Done[/dim]")


def main():
    parser = argparse.ArgumentParser(
        description="Politely search the 4x4community Tracks4Africa forum for road condition reports."
    )
    parser.add_argument("places", nargs="+", help="Place names to search for")
    parser.add_argument("--pages", type=int, default=5, help="Max result pages per keyword (default: 5)")
    parser.add_argument("--content", action="store_true", help="Fetch and show first posts of each matching thread")
    parser.add_argument("--delay", type=float, default=2.5, help="Base delay between requests in seconds (default: 2.5)")
    parser.add_argument("--days", type=int, default=0,
                        help="Only show threads from the last N days, e.g. --days 90. Default: no filter")
    parser.add_argument("--username", default=None, help="Forum username (or set FORUM_USERNAME env var)")
    parser.add_argument("--password", default=None, help="Forum password (or set FORUM_PASSWORD env var)")
    args = parser.parse_args()

    run(args.places, max_pages=args.pages, show_content=args.content, delay=args.delay,
        username=args.username, password=args.password, days=args.days)


if __name__ == "__main__":
    main()
