#!/usr/bin/env python3
"""
Polite scraper for https://www.4x4community.co.za/forum/forumdisplay.php/247-Tracks4Africa-ONLY
Searches threads by place name for rainy season road/track conditions.

Usage:
    uv run main.py "Moremi" "Nxai Pan"
    uv run main.py "Sani Pass" "Lesotho" --pages 20 --content
"""

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

import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.live import Live
from rich.text import Text

console = Console(highlight=False)

BASE_URL = "https://www.4x4community.co.za/forum"
FORUM_URL = f"{BASE_URL}/forumdisplay.php/247-Tracks4Africa-ONLY"

CACHE_DIR = Path.home() / ".cache" / "forum-scraper"
CACHE_TTL = timedelta(hours=2)
MAX_RETRIES = 3

# Honest user-agent — identifies who we are rather than impersonating a browser
HEADERS = {
    "User-Agent": "forum-condition-scraper/1.0 (personal use; road condition monitoring; not commercial)",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ── Cache ────────────────────────────────────────────────────────────────────

def _cache_path(url: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / (hashlib.md5(url.encode()).hexdigest() + ".json")


def cache_get(url: str) -> Optional[str]:
    path = _cache_path(url)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    age = datetime.now() - datetime.fromisoformat(data["fetched_at"])
    if age > CACHE_TTL:
        return None
    return data["html"]


def cache_set(url: str, html: str):
    path = _cache_path(url)
    path.write_text(json.dumps({"url": url, "fetched_at": datetime.now().isoformat(), "html": html}))


# ── Polite delay ─────────────────────────────────────────────────────────────

def polite_delay(base: float):
    """Sleep for base ± 30% jitter, with a live countdown so the user can see we're waiting."""
    wait = max(1.0, base + random.uniform(-base * 0.3, base * 0.3))
    with Live(console=console, refresh_per_second=8) as live:
        start = time.monotonic()
        while True:
            remaining = wait - (time.monotonic() - start)
            if remaining <= 0:
                break
            live.update(Text(f"  ⏳  Being polite — next request in {remaining:.1f}s", style="dim"))
            time.sleep(0.05)


# ── Fetching ─────────────────────────────────────────────────────────────────

def fetch(url: str, session: requests.Session, label: str = "") -> Optional[BeautifulSoup]:
    cached = cache_get(url)
    if cached:
        console.print(f"  [dim](served from cache)[/dim]")
        return BeautifulSoup(cached, "html.parser")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)

            if resp.status_code == 429:
                wait = 30 * attempt
                console.print(f"\n  [yellow]⚠  Rate limited by server — backing off {wait}s[/yellow]")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            cache_set(url, resp.text)
            return BeautifulSoup(resp.text, "html.parser")

        except requests.RequestException as e:
            if attempt < MAX_RETRIES:
                wait = 10 * attempt
                console.print(f"\n  [yellow]⚠  Request failed: {e} — retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})[/yellow]")
                time.sleep(wait)
            else:
                console.print(f"\n  [red]✗  Gave up after {MAX_RETRIES} attempts: {e}[/red]")

    return None


# ── Parsing ───────────────────────────────────────────────────────────────────

def get_threads(soup: BeautifulSoup) -> list[dict]:
    threads = []
    for row in soup.find_all(id=re.compile(r"^thread_\d+")):
        title_tag = row.find("a", id=re.compile(r"^thread_title_"))
        if not title_tag:
            continue

        href = title_tag.get("href", "")
        if href and not href.startswith("http"):
            href = BASE_URL + "/" + href.lstrip("/")

        date_tag = row.find("span", class_="time") or row.find("span", class_="date")
        replies_tag = row.find(class_=re.compile(r"threadstats|td_replies|replycount"))

        threads.append({
            "title": title_tag.get_text(strip=True),
            "url": href,
            "last_post": date_tag.get_text(strip=True) if date_tag else "—",
            "replies": replies_tag.get_text(strip=True) if replies_tag else "?",
        })
    return threads


def next_page_url(soup: BeautifulSoup) -> Optional[str]:
    link = soup.find("a", rel="next") or soup.find("a", title=re.compile(r"next page", re.I))
    if link and link.get("href"):
        href = link["href"]
        return href if href.startswith("http") else BASE_URL + "/" + href.lstrip("/")
    return None


def fetch_posts(url: str, session: requests.Session, n: int = 3) -> list[str]:
    soup = fetch(url, session)
    if not soup:
        return []
    posts = []
    for body in soup.find_all(class_=re.compile(r"postcontent|post-content|postbody|post_body"), limit=n):
        text = body.get_text(separator=" ", strip=True)
        posts.append(text[:600] + ("..." if len(text) > 600 else ""))
    return posts


# ── Main search ───────────────────────────────────────────────────────────────

def search(places: list[str], max_pages: int, show_content: bool, delay: float):
    session = requests.Session()
    keywords = [p.lower() for p in places]

    console.rule("[bold]Tracks4Africa Forum — Condition Scraper[/bold]")
    console.print()
    console.print(f"  Keywords  : [cyan]{', '.join(places)}[/cyan]")
    console.print(f"  Max pages : {max_pages}")
    console.print(f"  Delay     : ~{delay}s between requests (±30% jitter)")
    console.print(f"  Cache     : {CACHE_DIR}  [dim](TTL {int(CACHE_TTL.total_seconds() / 3600)}h — re-runs won't hammer the server)[/dim]")
    console.print()

    # robots.txt check
    console.print("  Checking [italic]robots.txt[/italic]...", end=" ")
    robots_url = "https://www.4x4community.co.za/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        resp = session.get(robots_url, headers=HEADERS, timeout=10)
        rp.parse(resp.text.splitlines())
        if not rp.can_fetch(HEADERS["User-Agent"], FORUM_URL):
            console.print("[bold red]DISALLOWED[/bold red]")
            console.print("\n  [red]robots.txt disallows scraping this URL. Aborting out of respect.[/red]")
            return
        console.print("[green]allowed ✓[/green]")
    except Exception:
        console.print("[yellow]couldn't fetch (assuming allowed)[/yellow]")

    console.print()
    console.rule("[dim]Scanning pages[/dim]")
    console.print()

    matches = []
    url: Optional[str] = FORUM_URL
    pages_scanned = 0

    for page in range(1, max_pages + 1):
        if not url:
            console.print("  [dim]No further pages found.[/dim]")
            break

        console.print(f"  [bold]Page {page}[/bold] → fetching...", end=" ")
        soup = fetch(url, session)
        if not soup:
            console.print("[red]failed, stopping.[/red]")
            break

        threads = get_threads(soup)
        pages_scanned += 1
        page_hits = []
        for t in threads:
            hits = [kw for kw in keywords if kw in t["title"].lower()]
            if hits:
                t["matched"] = hits
                page_hits.append(t)

        matches.extend(page_hits)

        hit_note = f"  [green]{len(page_hits)} match(es) found ✓[/green]" if page_hits else ""
        console.print(f"[dim]{len(threads)} threads scanned[/dim]{hit_note}")

        url = next_page_url(soup)
        if url:
            polite_delay(delay)

    # ── Results ───────────────────────────────────────────────────────────────

    console.print()
    console.rule(f"[bold]Results — {len(matches)} match(es) across {pages_scanned} page(s)[/bold]")
    console.print()

    if not matches:
        console.print("  [yellow]No threads matched. Try broader or alternative place names.[/yellow]")
        return

    for i, t in enumerate(matches, 1):
        console.print(f"  [bold cyan][{i}][/bold cyan] [bold]{t['title']}[/bold]")
        console.print(f"       Matched  : [green]{', '.join(t['matched'])}[/green]")
        console.print(f"       Last post: {t['last_post']}  |  Replies: {t['replies']}")
        console.print(f"       [link={t['url']}][blue]{t['url']}[/blue][/link]")

        if show_content:
            posts = fetch_posts(t["url"], session)
            if posts:
                console.print()
                for j, p in enumerate(posts, 1):
                    console.print(f"       [dim][Post {j}][/dim] {p}")
            polite_delay(delay)

        console.print()

    console.rule("[dim]Done[/dim]")


def main():
    parser = argparse.ArgumentParser(
        description="Politely search the 4x4community Tracks4Africa forum for road condition reports."
    )
    parser.add_argument("places", nargs="+", help="Place names to search for")
    parser.add_argument("--pages", type=int, default=10, help="Max pages to scan (default: 10)")
    parser.add_argument("--content", action="store_true", help="Fetch and preview matching thread content")
    parser.add_argument("--delay", type=float, default=2.5, help="Base delay between requests in seconds (default: 2.5)")
    args = parser.parse_args()

    search(args.places, max_pages=args.pages, show_content=args.content, delay=args.delay)


if __name__ == "__main__":
    main()
