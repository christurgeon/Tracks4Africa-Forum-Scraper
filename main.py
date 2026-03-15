#!/usr/bin/env python3
"""
Scraper for https://www.4x4community.co.za/forum/forumdisplay.php/247-Tracks4Africa-ONLY
Searches threads by place name to check rainy season road/track conditions.

Usage:
    uv run main.py "Botswana" "Chobe" "Makgadikgadi"
    uv run main.py "Boulders" --pages 20 --content
"""

import re
import time
import argparse
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.4x4community.co.za/forum"
FORUM_URL = f"{BASE_URL}/forumdisplay.php/247-Tracks4Africa-ONLY"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch(url: str, session: requests.Session) -> Optional[BeautifulSoup]:
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"  [error] {url}: {e}")
        return None


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


def search(places: list[str], max_pages: int, show_content: bool, delay: float):
    session = requests.Session()
    keywords = [p.lower() for p in places]

    print(f"\nKeywords : {', '.join(places)}")
    print(f"Max pages: {max_pages}")
    print(f"Forum    : {FORUM_URL}\n")
    print("=" * 70)

    matches = []
    url: Optional[str] = FORUM_URL

    for page in range(1, max_pages + 1):
        if not url:
            break
        print(f"  Page {page}...", end=" ", flush=True)
        soup = fetch(url, session)
        if not soup:
            break

        threads = get_threads(soup)
        print(f"{len(threads)} threads")

        for t in threads:
            hits = [kw for kw in keywords if kw in t["title"].lower()]
            if hits:
                t["matched"] = hits
                matches.append(t)

        url = next_page_url(soup)
        if url:
            time.sleep(delay)

    print(f"\n{'=' * 70}")
    print(f"{len(matches)} matching thread(s) found\n")

    if not matches:
        print("No results. Try broader or alternative place names.")
        return

    for i, t in enumerate(matches, 1):
        print(f"[{i}] {t['title']}")
        print(f"    Matched  : {', '.join(t['matched'])}")
        print(f"    Last post: {t['last_post']}  |  Replies: {t['replies']}")
        print(f"    {t['url']}")

        if show_content:
            posts = fetch_posts(t["url"], session)
            if posts:
                print()
                for j, p in enumerate(posts, 1):
                    print(f"    [post {j}] {p}")
            time.sleep(delay)

        print()


def main():
    parser = argparse.ArgumentParser(
        description="Search the 4x4community Tracks4Africa forum for condition reports by place."
    )
    parser.add_argument("places", nargs="+", help="Place names to search for")
    parser.add_argument("--pages", type=int, default=10, help="Max pages to scan (default: 10)")
    parser.add_argument("--content", action="store_true", help="Fetch and preview matching thread content")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between requests (default: 1.5)")
    args = parser.parse_args()

    search(args.places, max_pages=args.pages, show_content=args.content, delay=args.delay)


if __name__ == "__main__":
    main()
