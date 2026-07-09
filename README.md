# Personal API Aggregator

A lightweight FastAPI app that aggregates weather and RSS feeds into a single JSON API. Built for Raspberry Pi Zero 2 (512MB RAM). No API keys needed.

## Features

- Weather from Open-Meteo (free, no API key, global coverage)
- RSS feeds from configurable sources (defaults: Hacker News, Ars Technica, The Verge, Krebs on Security)
- 5-minute cache to avoid hammering upstream APIs
- Simple dashboard at `/` with live weather and feed updates
- All endpoints return JSON, CORS enabled for frontend use
- Total RAM usage: ~25-40MB

## Quick Start

```bash
# Install dependencies (use a venv on Pi)
pip install -r requirements.txt

# Run the server
python app.py
```

Then visit:
- Dashboard: http://localhost:8000/
- Full API: http://localhost:8000/api/all
- Weather only: http://localhost:8000/api/weather
- RSS only: http://localhost:8000/rss

## Configuration

Edit `config.yaml` to change:

- Weather location (lat/lon, timezone, display name)
- RSS feeds (add/remove feeds, set per-feed article limits)
- Cache TTL (how long to cache responses before refreshing)

Find your coordinates at https://open-meteo.com/en/docs

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard (HTML) |
| GET | `/api/all` | Weather + all RSS feeds in one response |
| GET | `/api/weather` | Current weather + 3-day forecast |
| GET | `/rss` | All RSS feeds |
| GET | `/rss/{feed_name}` | Single feed by name |
| GET | `/health` | Health check |
| POST | `/cache/clear` | Clear cache to force refresh |

## Default Weather

Chicago, IL (41.88, -87.63). Change in `config.yaml` to your location.

## Default RSS Feeds

- Hacker News (top 10)
- Ars Technica (top 5)
- The Verge (top 5)
- Krebs on Security (top 5)

## Running on Pi Zero 2

```bash
# Create a virtual env (saves RAM vs system Python)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run with uvicorn (1 worker for 512MB RAM)
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

Optional: set up as a systemd service for auto-start on boot. See `pi-zero-2-selfhost` repo for service file examples.

## Tech Stack

- FastAPI (ASGI web framework)
- uvicorn (ASGI server)
- httpx (async HTTP client for Open-Meteo)
- feedparser (RSS/Atom parser)
- PyYAML (config file)

## License

AGPL-3.0
