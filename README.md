# 4x4community Forum Scraper

Searches the [Tracks4Africa-ONLY subforum](https://www.4x4community.co.za/forum/forumdisplay.php/247-Tracks4Africa-ONLY) on 4x4community.co.za for road and track condition reports by place name. Useful for checking conditions at the tail end of the rainy season before a trip.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager
- A registered account on [4x4community.co.za](https://www.4x4community.co.za) (the forum search requires login)

## Setup

```bash
git clone <repo-url>
cd forum-scraper
uv sync
```

That's it. `uv sync` installs all dependencies into an isolated virtual environment automatically.

## Credentials

The forum's search requires you to be logged in. Pass your credentials either as environment variables (recommended — keeps them out of your shell history) or as command-line flags.

**Environment variables (recommended):**
```bash
export FORUM_USERNAME=your_username
export FORUM_PASSWORD=your_password
```

You can put these in your `~/.zshrc` or `~/.bash_profile` so you don't have to set them each session.

**Or inline per run:**
```bash
FORUM_USERNAME=you FORUM_PASSWORD=secret uv run main.py "Moremi"
```

## Usage

```
uv run main.py <place> [place ...] [options]
```

### Examples

```bash
# Search for two places, all time, newest first
uv run main.py "Moremi" "Nxai Pan"

# Limit to the last 6 months (good for current conditions)
uv run main.py "Moremi" "Nxai Pan" --days 180

# Also fetch and preview the first few posts of each matching thread
uv run main.py "Moremi" --days 90 --content

# Search more result pages (25 threads per page)
uv run main.py "Okavango" --pages 10

# Multiple places across the whole forum archive with content previews
uv run main.py "Chobe" "Savuti" "Linyanti" --days 365 --content
```

### All options

| Flag | Default | Description |
|------|---------|-------------|
| `--days N` | 0 (any date) | Only return threads from the last N days |
| `--content` | off | Fetch and display the first 3 posts of each matching thread |
| `--pages N` | 5 | Max pages of results to fetch per keyword (25 threads/page) |
| `--delay N` | 2.5 | Base seconds to wait between requests (see Politeness below) |
| `--username` | — | Forum username (overrides `FORUM_USERNAME` env var) |
| `--password` | — | Forum password (overrides `FORUM_PASSWORD` env var) |

## How it works

### Search approach

Rather than scraping the forum's listing pages one by one, the scraper uses the forum's **own search engine** (`search.php`) — exactly what the website does when you search in your browser. This means:

- Results cover the **entire forum archive**, not just recent pages
- Searches match keywords in **post content**, not just thread titles
- Results are returned **newest first** by default
- An optional **date filter** (`--days`) narrows results to a recent window

For each keyword you provide, the scraper:
1. Logs in to the forum (required to access search)
2. POSTs a search request, receiving a `searchid` back from the server
3. Paginates through the result pages using that `searchid`
4. Optionally fetches and previews post content from each matching thread

### Politeness

The scraper is designed not to hammer the server:

- **Honest User-Agent** — identifies itself as a scraper rather than impersonating a browser
- **`robots.txt` check** — verifies the search URL is allowed before doing anything; aborts if not
- **Randomised delay** — waits `delay ± 30%` seconds between every request (jitter prevents predictable request patterns)
- **Exponential backoff** — if a request fails or returns HTTP 429 (rate limited), it backs off and retries up to 3 times before giving up
- **Disk cache** — responses are cached for 2 hours at `~/.cache/forum-scraper/`. Re-running a search within that window replays from cache rather than hitting the server again

### Cache

Cached files live at `~/.cache/forum-scraper/` as JSON files keyed by a hash of the URL. They expire after 2 hours. To force a fresh fetch, delete the cache directory:

```bash
rm -rf ~/.cache/forum-scraper
```
