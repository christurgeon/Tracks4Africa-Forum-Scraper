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
DATA_DIR = Path("data")

# cf_clearance is bound to the User-Agent of the browser that generated it.
# FORUM_USER_AGENT must match exactly, otherwise Cloudflare will reject the cookie.
# Get it from your browser: DevTools → Network → any request → User-Agent header.
_USER_AGENT = os.environ.get(
    "FORUM_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)

HEADERS = {
    "User-Agent": _USER_AGENT,
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


# ── Forum listing ─────────────────────────────────────────────────────────────

def parse_forum_listing(soup: BeautifulSoup) -> list[dict]:
    """Parse thread list from forumdisplay.php page."""
    threads = []
    for item in soup.find_all("li", id=re.compile(r"^thread_")):
        thread_id = item["id"].replace("thread_", "")

        title_link = item.find("a", class_="title")
        if not title_link:
            continue
        title = title_link.get_text(strip=True)
        href = title_link.get("href", "")
        if href and not href.startswith("http"):
            href = BASE_URL + "/" + href.lstrip("/")

        # Replies from threadstats
        replies = "?"
        stats = item.find("ul", class_=re.compile(r"threadstats"))
        if stats:
            for li in stats.find_all("li"):
                text = li.get_text(strip=True)
                if "Replies" in text:
                    match = re.search(r"\d+", text)
                    if match:
                        replies = match.group()

        # Last post date
        last_post = ""
        lastpost_dl = item.find("dl", class_=re.compile(r"lastpost"))
        if lastpost_dl:
            time_span = lastpost_dl.find("span", class_="time")
            if time_span and time_span.parent.name == "dd":
                last_post = time_span.parent.get_text(strip=True)
                last_post = last_post.replace("Go to last post", "").strip()

        # Preview from threadinfo title attribute
        threadinfo = item.find("div", class_="threadinfo")
        preview = (threadinfo.get("title", "") if threadinfo else "")[:300]

        threads.append({
            "title": title,
            "url": href,
            "thread_id": thread_id,
            "last_post": last_post,
            "replies": replies,
            "preview": preview,
        })
    return threads


def fetch_forum_listing(session: requests.Session, delay: float,
                        max_pages: int = 0) -> list[dict]:
    """Fetch all threads from the Tracks4Africa subforum listing."""
    all_threads = []
    page = 1

    while True:
        url = f"{BASE_URL}/forumdisplay.php?f={FORUM_ID}&page={page}"
        console.print(f"  [bold]Listing page {page}[/bold] → fetching...", end=" ")
        soup = get(url, session, cache_key=url)
        if not soup:
            console.print("[red]failed.[/red]")
            break

        threads = parse_forum_listing(soup)
        console.print(f"[dim]{len(threads)} thread(s)[/dim]")
        all_threads.extend(threads)

        if not threads or not has_next_page(soup):
            console.print(f"  [dim]No more pages.[/dim]")
            break

        if max_pages > 0 and page >= max_pages:
            break

        page += 1
        polite_delay(delay)

    return all_threads


# ── Thread dumping ────────────────────────────────────────────────────────────

def extract_thread_id(url: str) -> Optional[str]:
    """Extract numeric thread ID from a vBulletin thread URL."""
    match = re.search(r'[?&]t=(\d+)', url) or re.search(r'showthread\.php/(\d+)', url)
    return match.group(1) if match else None


def _thread_page_url(url: str, page: int) -> str:
    if page == 1:
        return url
    url = re.sub(r'[?&]page=\d+', '', url)
    separator = '&' if '?' in url else '?'
    return f"{url}{separator}page={page}"


def parse_thread_page(soup: BeautifulSoup) -> list[dict]:
    """Extract all posts (author, date, text) from a single thread page."""
    posts = []

    # vBulletin 4.x: posts live in <li id="post_12345"> or <div id="post_...">
    containers = soup.find_all("li", id=re.compile(r"^post_"))
    if not containers:
        containers = soup.find_all("div", id=re.compile(r"^post_"))

    if containers:
        for container in containers:
            author_el = container.find(class_="username")
            author = author_el.get_text(strip=True) if author_el else ""

            date_el = container.find(class_="date")
            date_str = date_el.get_text(separator=" ", strip=True) if date_el else ""

            body_el = container.find(class_=re.compile(r"postcontent|postbody"))
            text = body_el.get_text(separator="\n", strip=True) if body_el else ""

            if text:
                posts.append({"author": author, "date": date_str, "text": text})
    else:
        # Fallback: find post bodies without metadata
        for body in soup.find_all(class_=re.compile(r"postcontent|post-content|postbody|post_body")):
            text = body.get_text(separator="\n", strip=True)
            if text:
                posts.append({"author": "", "date": "", "text": text})

    return posts


def fetch_full_thread(url: str, session: requests.Session, delay: float) -> list[dict]:
    """Fetch ALL posts from ALL pages of a thread."""
    all_posts = []
    page = 1
    while True:
        page_url = _thread_page_url(url, page)
        soup = get(page_url, session, cache_key=page_url)
        if not soup:
            break
        posts = parse_thread_page(soup)
        if not posts:
            break
        all_posts.extend(posts)
        if not has_next_page(soup):
            break
        page += 1
        polite_delay(delay)
    return all_posts


def dump_threads(all_results: dict, session: requests.Session, delay: float):
    """Fetch full content of every thread and write structured JSON to data/."""
    # Deduplicate threads across keywords
    unique: dict[str, dict] = {}
    for keyword, results in all_results.items():
        for t in results:
            url = t["url"]
            if url in unique:
                unique[url]["keywords"].append(keyword)
            else:
                unique[url] = {**t, "keywords": [keyword]}

    threads = list(unique.values())
    total = len(threads)

    if total == 0:
        console.print("  [yellow]No threads to dump.[/yellow]")
        return

    threads_dir = DATA_DIR / "threads"
    threads_dir.mkdir(parents=True, exist_ok=True)

    console.rule(f"[bold]Dumping {total} thread(s) to {DATA_DIR}/[/bold]")
    console.print()

    index = []
    skipped = 0

    for i, t in enumerate(threads, 1):
        thread_id = t.get("thread_id") or extract_thread_id(t["url"])
        if not thread_id:
            thread_id = hashlib.md5(t["url"].encode()).hexdigest()[:12]

        thread_file = threads_dir / f"{thread_id}.json"

        # Incremental: skip if already scraped with same reply count
        if thread_file.exists():
            try:
                existing = json.loads(thread_file.read_text())
                if str(existing.get("replies")) == str(t.get("replies")):
                    console.print(f"  [dim][{i}/{total}] Skipping (unchanged): {t['title'][:60]}[/dim]")
                    index.append({
                        "thread_id": thread_id,
                        "title": existing["title"],
                        "url": existing["url"],
                        "last_post": existing.get("last_post", ""),
                        "replies": existing.get("replies", ""),
                        "keywords": list(set(existing.get("keywords", []) + t["keywords"])),
                        "post_count": len(existing.get("posts", [])),
                    })
                    skipped += 1
                    continue
            except (json.JSONDecodeError, KeyError):
                pass  # Re-fetch if file is corrupt

        console.print(f"  [bold cyan][{i}/{total}][/bold cyan] {t['title'][:70]}")
        polite_delay(delay)
        posts = fetch_full_thread(t["url"], session, delay)
        console.print(f"          → [green]{len(posts)} post(s)[/green]")

        thread_data = {
            "thread_id": thread_id,
            "title": t["title"],
            "url": t["url"],
            "last_post": t.get("last_post", ""),
            "replies": t.get("replies", ""),
            "keywords": t["keywords"],
            "scraped_at": datetime.now().isoformat(),
            "posts": posts,
        }

        thread_file.write_text(json.dumps(thread_data, indent=2, ensure_ascii=False))

        index.append({
            "thread_id": thread_id,
            "title": t["title"],
            "url": t["url"],
            "last_post": t.get("last_post", ""),
            "replies": t.get("replies", ""),
            "keywords": t["keywords"],
            "post_count": len(posts),
        })

    # Merge with existing index
    index_file = DATA_DIR / "threads.json"
    existing_by_id: dict[str, dict] = {}
    if index_file.exists():
        try:
            for e in json.loads(index_file.read_text()):
                existing_by_id[e["thread_id"]] = e
        except (json.JSONDecodeError, KeyError):
            pass

    for entry in index:
        tid = entry["thread_id"]
        if tid in existing_by_id:
            entry["keywords"] = list(set(existing_by_id[tid].get("keywords", []) + entry["keywords"]))
        existing_by_id[tid] = entry

    index_file.write_text(json.dumps(list(existing_by_id.values()), indent=2, ensure_ascii=False))

    console.print()
    fetched = total - skipped
    console.print(f"  [green]✓  Done — {fetched} fetched, {skipped} skipped (unchanged)[/green]")
    console.print(f"  [dim]Index: {index_file}  |  Threads: {threads_dir}/[/dim]")
    console.rule("[dim]Done[/dim]")


# ── Orchestration ─────────────────────────────────────────────────────────────

def run(places: list[str], max_pages: int, show_content: bool, delay: float,
        username: Optional[str] = None, password: Optional[str] = None, days: int = 0,
        dump: bool = False):
    # impersonate="chrome120" gives us a real Chrome TLS fingerprint,
    # which is required to pass Cloudflare Bot Management on search.php
    session = requests.Session(impersonate="chrome120")

    console.rule("[bold]Tracks4Africa Forum — Condition Scraper[/bold]")
    console.print()
    console.print(f"  Searching  : [cyan]{', '.join(places) if places else 'all threads (dump mode)'}[/cyan]")
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
    # The cookie is also bound to the exact User-Agent that generated it (FORUM_USER_AGENT).
    cf_clearance = os.environ.get("CF_CLEARANCE")
    if not cf_clearance:
        console.print("  [bold red]✗  CF_CLEARANCE is not set.[/bold red]")
        console.print()
        console.print("  The forum's search page is behind Cloudflare and requires this cookie.")
        console.print("  To get both values, open the forum in your browser and log in, then:")
        console.print()
        console.print("  [bold]1. Get CF_CLEARANCE[/bold]")
        console.print("     DevTools → Application (Chrome) / Storage (Safari)")
        console.print("     → Cookies → www.4x4community.co.za → [bold]cf_clearance[/bold]")
        console.print()
        console.print("  [bold]2. Get FORUM_USER_AGENT[/bold]")
        console.print("     DevTools → Network → click any request → Headers → [bold]User-Agent[/bold]")
        console.print()
        console.print("  Add both to your [bold].env[/bold] file:")
        console.print("  [dim]CF_CLEARANCE=paste_value_here[/dim]")
        console.print("  [dim]FORUM_USER_AGENT=paste_user_agent_here[/dim]")
        console.print()
        return

    if not os.environ.get("FORUM_USER_AGENT"):
        console.print("  [yellow]⚠  FORUM_USER_AGENT not set — Cloudflare may reject cf_clearance.[/yellow]")
        console.print("  [dim]Get it from DevTools → Network → any request → User-Agent header.[/dim]")
        console.print()

    session.cookies.set("cf_clearance", cf_clearance, domain=".4x4community.co.za")
    console.print(f"  [dim]cf_clearance loaded ✓  |  UA: {_USER_AGENT[:60]}...[/dim]")

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

    # ── Dump mode: browse forum listing (bypasses Cloudflare-protected search) ─
    if dump:
        console.rule("[bold]Browsing Tracks4Africa subforum listing[/bold]")
        console.print()
        listing_threads = fetch_forum_listing(session, delay, max_pages=max_pages)

        # Filter by keywords if provided
        if places:
            pattern = re.compile("|".join(re.escape(p) for p in places), re.I)
            before = len(listing_threads)
            listing_threads = [
                t for t in listing_threads
                if pattern.search(t["title"]) or pattern.search(t.get("preview", ""))
            ]
            console.print()
            console.print(f"  Filtered {before} → [bold]{len(listing_threads)}[/bold] thread(s)"
                          f" matching: [cyan]{', '.join(places)}[/cyan]")

        all_results = {"all": listing_threads}
        console.print()
        dump_threads(all_results, session, delay)
        return

    # ── Search mode: use forum search engine ──────────────────────────────────
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
    parser.add_argument("places", nargs="*", help="Place names to search for (optional with --dump)")
    parser.add_argument("--pages", type=int, default=5, help="Max result pages per keyword (default: 5)")
    parser.add_argument("--content", action="store_true", help="Fetch and show first posts of each matching thread")
    parser.add_argument("--delay", type=float, default=2.5, help="Base delay between requests in seconds (default: 2.5)")
    parser.add_argument("--days", type=int, default=0,
                        help="Only show threads from the last N days, e.g. --days 90. Default: no filter")
    parser.add_argument("--dump", action="store_true",
                        help="Fetch full thread content and write to data/ as JSON for analysis")
    parser.add_argument("--username", default=None, help="Forum username (or set FORUM_USERNAME env var)")
    parser.add_argument("--password", default=None, help="Forum password (or set FORUM_PASSWORD env var)")
    args = parser.parse_args()

    if not args.dump and not args.places:
        parser.error("place names are required (or use --dump to export all threads)")

    run(args.places, max_pages=args.pages, show_content=args.content, delay=args.delay,
        username=args.username, password=args.password, days=args.days, dump=args.dump)


if __name__ == "__main__":
    main()
