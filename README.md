# Brooklyn Foot Traffic

An interactive map that helps small businesses identify high foot-traffic streets in Brooklyn, giving them data-driven insight into where to open a new location.

**Live site:** Coming soon — domain not yet assigned

## Features

- **Interactive street map** — actual street segments highlighted directly on the map (not pins or bubbles)
- **Color-coded intensity** — streets colored on a blue → red gradient based on pedestrian count
- **Glow effect** — high-traffic streets visually stand out on the dark map
- **Time-of-day filter** — toggle between Morning, Midday, and Evening counts
- **Click any street** — popup shows pedestrian counts for all three time periods
- **Auto-refresh** — data synced daily from NYC Open Data at 7am ET

## Data Sources

| Dataset | Provider | Details |
|---------|----------|---------|
| [NYC DOT Bi-Annual Pedestrian Counts](https://data.cityofnewyork.us/resource/cqsj-cfgu.json) | NYC Open Data | Pedestrian counts at Brooklyn intersections (AM / Midday / PM), updated twice yearly |
| [NYC Street Centerline (CSCL)](https://data.cityofnewyork.us/resource/inkn-q76z.json) | NYC Open Data / OTI | Street segment geometries used to draw highlighted lines on the map |

Currently using **May 2025** survey data — the most recent available.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python) |
| Database | SQLite via Python stdlib |
| Templates | Jinja2 |
| Server | Uvicorn (ASGI) |
| Reverse proxy | Nginx |
| Map | Leaflet.js |
| Map tiles | CartoDB Dark Matter (OpenStreetMap) |
| Data API | NYC Open Data (Socrata REST API) |
| HTTP client | httpx (async) |
| Process manager | systemd |
| Hosting | DigitalOcean Droplet |

## How It Works

1. On startup, the backend fetches all Brooklyn pedestrian count locations from the NYC DOT dataset and caches them in SQLite
2. For each location, it queries the NYC Street Centerline dataset using a spatial radius search to find the matching street segment geometry
3. Street names are normalized (e.g. "5th Avenue" to "5 AVE") and matched against the centerline to ensure the right segment is highlighted
4. The frontend fetches GeoJSON from the API and renders each segment as a glowing polyline on the Leaflet map, filtered by time of day
5. A daily cron job at 7am ET refreshes the data and pushes any changes to this repository
