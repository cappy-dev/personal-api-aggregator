#!/usr/bin/env python3
"""Personal API Aggregator for Raspberry Pi Zero 2.

A lightweight FastAPI app that fetches and caches:
  - Weather from Open-Meteo (free, no API key)
  - RSS feeds from configurable sources

All responses are cached to avoid hammering upstream APIs.
Designed to run on 512MB RAM with minimal overhead.
"""

import time
import asyncio
from functools import partial
from pathlib import Path

import httpx
import yaml
import feedparser
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

# === CONFIG ===

CONFIG_PATH = Path(__file__).parent / "config.yaml"

def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

config = load_config()

# === CACHE ===

class Cache:
    """Simple TTL cache. Stores (value, timestamp) pairs."""
    def __init__(self):
        self._store = {}

    def get(self, key, ttl):
        if key in self._store:
            value, ts = self._store[key]
            if time.time() - ts < ttl:
                return value
        return None

    def set(self, key, value):
        self._store[key] = (value, time.time())

cache = Cache()

# === API CLIENTS ===

async def fetch_json(url, params=None):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

def parse_feed(url):
    """Parse an RSS/Atom feed. Runs synchronously (feedparser is not async)."""
    return feedparser.parse(url)

# === WEATHER ===

async def get_weather():
    cached = cache.get("weather", config["cache"]["ttl"])
    if cached is not None:
        return cached

    w = config["weather"]
    params = {
        "latitude": w["latitude"],
        "longitude": w["longitude"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        "timezone": w["timezone"],
        "forecast_days": 3,
    }

    data = await fetch_json("https://api.open-meteo.com/v1/forecast", params)

    current = data.get("current", {})
    daily = data.get("daily", {})
    current_time = current.get("time", "")

    result = {
        "location": w["name"],
        "latitude": w["latitude"],
        "longitude": w["longitude"],
        "timezone": w["timezone"],
        "current": {
            "time": current_time,
            "temperature_c": current.get("temperature_2m"),
            "temperature_f": round(current.get("temperature_2m", 0) * 9/5 + 32, 1) if current.get("temperature_2m") is not None else None,
            "feels_like_c": current.get("apparent_temperature"),
            "feels_like_f": round(current.get("apparent_temperature", 0) * 9/5 + 32, 1) if current.get("apparent_temperature") is not None else None,
            "humidity_pct": current.get("relative_humidity_2m"),
            "wind_kmh": current.get("wind_speed_10m"),
            "weather_code": current.get("weather_code"),
        },
        "forecast": [
            {
                "date": daily["time"][i] if i < len(daily.get("time", [])) else None,
                "temp_max_c": daily.get("temperature_2m_max", [None])[i] if i < len(daily.get("temperature_2m_max", [])) else None,
                "temp_max_f": round(daily["temperature_2m_max"][i] * 9/5 + 32, 1) if i < len(daily.get("temperature_2m_max", [])) and daily["temperature_2m_max"][i] is not None else None,
                "temp_min_c": daily.get("temperature_2m_min", [None])[i] if i < len(daily.get("temperature_2m_min", [])) else None,
                "temp_min_f": round(daily["temperature_2m_min"][i] * 9/5 + 32, 1) if i < len(daily.get("temperature_2m_min", [])) and daily["temperature_2m_min"][i] is not None else None,
                "precip_probability_pct": daily.get("precipitation_probability_max", [None])[i] if i < len(daily.get("precipitation_probability_max", [])) else None,
                "sunrise": daily.get("sunrise", [None])[i] if i < len(daily.get("sunrise", [])) else None,
                "sunset": daily.get("sunset", [None])[i] if i < len(daily.get("sunset", [])) else None,
            }
            for i in range(min(3, len(daily.get("time", []))))
        ],
    }

    cache.set("weather", result)
    return result

# === RSS ===

def parse_all_feeds():
    """Parse all configured RSS feeds. Runs in a thread to avoid blocking."""
    results = {}
    for name, feed_cfg in config["rss_feeds"].items():
        url = feed_cfg["url"]
        limit = feed_cfg.get("limit", 5)
        try:
            parsed = feedparser.parse(url)
            items = []
            for entry in parsed.entries[:limit]:
                items.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:200] if entry.get("summary") else "",
                    "published": entry.get("published", entry.get("updated", "")),
                })
            results[name] = {
                "count": len(items),
                "items": items,
            }
        except Exception as e:
            results[name] = {"error": str(e), "items": []}
    return results

async def get_rss():
    cached = cache.get("rss", config["cache"]["ttl"])
    if cached is not None:
        return cached

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, parse_all_feeds)
    cache.set("rss", result)
    return result

# === COMBINED ENDPOINT ===

async def get_all():
    cached = cache.get("all", config["cache"]["ttl"])
    if cached is not None:
        return cached

    weather_task = asyncio.create_task(get_weather())
    rss_task = asyncio.create_task(get_rss())

    weather, rss = await asyncio.gather(weather_task, rss_task, return_exceptions=True)

    result = {}
    if isinstance(weather, Exception):
        result["weather"] = {"error": str(weather)}
    else:
        result["weather"] = weather

    if isinstance(rss, Exception):
        result["rss"] = {"error": str(rss)}
    else:
        result["rss"] = rss

    result["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    cache.set("all", result)
    return result

# === FASTAPI APP ===

app = FastAPI(
    title="Personal API Aggregator",
    description="Weather + RSS feeds in one JSON endpoint. Built for Raspberry Pi Zero 2.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve a simple dashboard page."""
    return DASHBOARD_HTML


@app.get("/api/all")
async def api_all():
    """Get everything: weather + all RSS feeds in one response."""
    return await get_all()


@app.get("/api/weather")
async def api_weather():
    """Get current weather and 3-day forecast."""
    return await get_weather()


@app.get("/rss")
async def api_rss():
    """Get all RSS feeds."""
    return await get_rss()


@app.get("/rss/{feed_name}")
async def api_rss_single(feed_name: str):
    """Get a single RSS feed by name (as configured in config.yaml)."""
    feeds = config["rss_feeds"]
    if feed_name not in feeds:
        return {"error": f"Feed '{feed_name}' not found", "available": list(feeds.keys())}

    feed_cfg = feeds[feed_name]
    loop = asyncio.get_event_loop()

    def parse_single():
        try:
            parsed = feedparser.parse(feed_cfg["url"])
            items = []
            for entry in parsed.entries[:feed_cfg.get("limit", 5)]:
                items.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:200] if entry.get("summary") else "",
                    "published": entry.get("published", entry.get("updated", "")),
                })
            return {"feed": feed_name, "count": len(items), "items": items}
        except Exception as e:
            return {"feed": feed_name, "error": str(e)}

    return await loop.run_in_executor(None, parse_single)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/cache/clear")
async def clear_cache():
    """Clear the cache to force a refresh."""
    cache._store.clear()
    return {"status": "cache cleared"}


# === DASHBOARD HTML ===

DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Personal API Aggregator</title>
<style>
:root {
  --bg: #08090a; --panel: #0f1011; --surface: #191a1b; --fg: #f7f8f8;
  --fg2: #d0d6e0; --fg3: #8a8f98; --accent: #5e6ad2; --accent-b: #7170ff;
  --green: #10b981; --border: rgba(255,255,255,0.08); --radius: 12px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font-family:-apple-system,sans-serif; line-height:1.6; }
.container { max-width:680px; margin:0 auto; padding:1.5rem; }
h1 { font-size:1.5rem; margin-bottom:0.25rem; }
h2 { font-size:1.2rem; margin:1.5rem 0 0.5rem; }
.sub { color:var(--fg3); font-size:0.9rem; margin-bottom:1.5rem; }
.card { background:var(--panel); border:1px solid var(--border); border-radius:var(--radius); padding:1rem 1.2rem; margin-bottom:0.75rem; }
.weather-current { display:flex; align-items:center; gap:1rem; }
.temp { font-size:2.5rem; font-weight:700; }
.meta { color:var(--fg3); font-size:0.85rem; }
.forecast-row { display:flex; gap:0.5rem; margin-top:0.75rem; }
.forecast-day { flex:1; background:var(--surface); border-radius:8px; padding:0.6rem; text-align:center; }
.forecast-day .day { font-size:0.75rem; color:var(--fg3); }
.forecast-day .temps { font-size:0.9rem; margin-top:0.25rem; }
.forecast-day .hi { color:var(--accent-b); }
.forecast-day .lo { color:var(--fg3); }
.rss-item { padding:0.6rem 0; border-bottom:1px solid var(--border); }
.rss-item:last-child { border-bottom:none; }
.rss-item a { color:var(--accent-b); text-decoration:none; font-weight:500; }
.rss-item a:hover { text-decoration:underline; }
.rss-item .date { color:var(--fg3); font-size:0.75rem; }
.endpoint { background:var(--surface); border-radius:8px; padding:0.6rem 0.8rem; margin:0.3rem 0; font-family:monospace; font-size:0.8rem; }
.endpoint strong { color:var(--green); }
.loading { color:var(--fg3); text-align:center; padding:2rem; }
</style>
</head>
<body>
<div class="container">
  <h1>Personal API Aggregator</h1>
  <p class="sub">Weather + RSS feeds. Running on a Pi Zero 2 near you.</p>

  <div id="content" class="loading">Loading...</div>

  <h2>API Endpoints</h2>
  <div class="endpoint"><strong>GET</strong> /api/all &mdash; everything in one response</div>
  <div class="endpoint"><strong>GET</strong> /api/weather &mdash; weather only</div>
  <div class="endpoint"><strong>GET</strong> /rss &mdash; all RSS feeds</div>
  <div class="endpoint"><strong>GET</strong> /rss/{feed_name} &mdash; single feed</div>
  <div class="endpoint"><strong>GET</strong> /health &mdash; health check</div>
  <div class="endpoint"><strong>POST</strong> /cache/clear &mdash; force refresh</div>
</div>

<script>
async function load() {
  try {
    const res = await fetch('/api/all');
    const data = await res.json();
    const el = document.getElementById('content');
    let html = '';

    // Weather
    if (data.weather && !data.weather.error) {
      const w = data.weather;
      const c = w.current;
      html += '<h2>Weather: ' + w.location + '</h2>';
      html += '<div class="card"><div class="weather-current">';
      html += '<div class="temp">' + (c.temperature_f || '--') + '&deg;F</div>';
      html += '<div class="meta">';
      html += 'Feels like ' + (c.feels_like_f || '--') + '&deg;F<br>';
      html += 'Humidity ' + (c.humidity_pct || '--') + '%<br>';
      html += 'Wind ' + (c.wind_kmh || '--') + ' km/h<br>';
      html += c.time || '';
      html += '</div></div>';

      if (w.forecast && w.forecast.length) {
        html += '<div class="forecast-row">';
        w.forecast.forEach(f => {
          const d = f.date ? new Date(f.date).toLocaleDateString('en-US', {weekday:'short'}) : '';
          html += '<div class="forecast-day">';
          html += '<div class="day">' + d + '</div>';
          html += '<div class="temps"><span class="hi">' + (f.temp_max_f || '--') + '&deg;</span> <span class="lo">' + (f.temp_min_f || '--') + '&deg;</span></div>';
          if (f.precip_probability_pct != null) html += '<div style="font-size:0.7rem;color:var(--fg3)">☔ ' + f.precip_probability_pct + '%</div>';
          html += '</div>';
        });
        html += '</div>';
      }
      html += '</div>';
    }

    // RSS
    if (data.rss && !data.rss.error) {
      html += '<h2>RSS Feeds</h2>';
      for (const [name, feed] of Object.entries(data.rss)) {
        if (feed.error) continue;
        html += '<div class="card"><strong>' + name + '</strong> (' + feed.count + ' articles)';
        if (feed.items) {
          feed.items.forEach(item => {
            html += '<div class="rss-item"><a href="' + item.link + '" target="_blank">' + item.title + '</a><div class="date">' + (item.published || '') + '</div></div>';
          });
        }
        html += '</div>';
      }
    }

    html += '<p class="sub" style="margin-top:1rem">Generated: ' + (data.generated_at || '') + '</p>';
    el.innerHTML = html;
  } catch(e) {
    document.getElementById('content').innerHTML = '<p style="color:#ef4444">Error loading: ' + e.message + '</p>';
  }
}
load();
setInterval(load, 300000);
</script>
</body>
</html>
"""

# === ENTRY POINT ===

if __name__ == "__main__":
    import uvicorn
    print("Starting Personal API Aggregator on http://0.0.0.0:8000")
    print("Dashboard: http://localhost:8000/")
    print("API: http://localhost:8000/api/all")
    print("Press Ctrl+C to stop")
    uvicorn.run(app, host="0.0.0.0", port=8000)
