# Tracks4Africa Forum Scraper

Scrapes the Tracks4Africa subforum (forum ID 247) on 4x4community.co.za for road/track condition reports.

## Running

```bash
uv run main.py "Place Name" --content    # search + display
uv run main.py --dump                     # dump all threads to data/
uv run main.py "Sani Pass" --dump         # dump filtered by keyword
```

## Architecture

Single-file scraper (`main.py`) using curl_cffi for Cloudflare bypass. Two modes:

- **Search mode** (default): uses forum search.php — requires working CF_CLEARANCE
- **Dump mode** (`--dump`): browses forum listing (forumdisplay.php) — more reliable, bypasses CF challenge on search.php

Credentials in `.env` (FORUM_USERNAME, FORUM_PASSWORD, CF_CLEARANCE, FORUM_USER_AGENT).

## Analysis

- `analysis/prompt.md` — System prompt for Claude to analyze scraped data as a trip-planning assistant
- `data/threads.json` — Index of scraped threads
- `data/threads/<id>.json` — Full thread content (all posts with author, date, text)

When the user asks about road conditions or routes, read `analysis/prompt.md` for guidelines, then read the relevant data files.
