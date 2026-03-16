# Scraped Forum Data

This directory contains data scraped from the Tracks4Africa subforum using `uv run main.py --dump`.

## Files

- `threads.json` — Index of all threads (title, URL, date, reply count, keywords, post count)
- `threads/<thread_id>.json` — Full thread content with all posts

## Thread JSON format

```json
{
  "thread_id": "12345",
  "title": "Thread Title",
  "url": "https://www.4x4community.co.za/forum/showthread.php/12345-...",
  "last_post": "2025/01/15, 08:30 AM",
  "replies": "42",
  "keywords": ["baviaanskloof"],
  "scraped_at": "2025-01-20T10:30:00",
  "posts": [
    {
      "author": "Username",
      "date": "2025/01/10, 08:30 AM",
      "text": "Full post text content..."
    }
  ]
}
```

## How to analyze

See `analysis/prompt.md` for the system prompt that guides Claude to act as a trip-planning assistant. Focus on most recent reports, cite dates, and flag uncertainty.
