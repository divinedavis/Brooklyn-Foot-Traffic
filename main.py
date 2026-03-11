import os
import json
import sqlite3
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

DB_PATH = "/home/foottraffic/foottraffic.db"
NYC_API = "https://data.cityofnewyork.us/resource/cqsj-cfgu.json"

# Most recent survey period field names
TIME_FIELDS = {
    "am": "may25_am",
    "pm": "may25_pm",
    "md": "may25_md",
}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY,
            objectid TEXT UNIQUE,
            street_name TEXT,
            from_street TEXT,
            to_street TEXT,
            lat REAL,
            lng REAL,
            am INTEGER,
            pm INTEGER,
            md INTEGER
        )
    """)
    conn.commit()
    conn.close()

async def fetch_and_cache():
    print("Fetching Brooklyn pedestrian data from NYC Open Data...")
    params = {
        "$where": "borough='Brooklyn'",
        "$limit": 1000,
        "$order": "objectid ASC",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(NYC_API, params=params)
        data = resp.json()

    conn = sqlite3.connect(DB_PATH)
    inserted = 0
    for row in data:
        geom = row.get("the_geom", {})
        coords = geom.get("coordinates", [None, None])
        lng, lat = coords[0], coords[1]
        if lat is None or lng is None:
            continue

        def safe_int(val):
            try:
                return int(val)
            except (TypeError, ValueError):
                return 0

        # Handle the may_23_p_m style inconsistency too
        pm_val = row.get("may25_pm") or row.get("may25_p_m")

        conn.execute("""
            INSERT OR REPLACE INTO locations
              (objectid, street_name, from_street, to_street, lat, lng, am, pm, md)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row.get("objectid"),
            row.get("street_nam", ""),
            row.get("from_stree", ""),
            row.get("to_street", ""),
            lat, lng,
            safe_int(row.get("may25_am")),
            safe_int(pm_val),
            safe_int(row.get("may25_md")),
        ))
        inserted += 1

    conn.commit()
    conn.close()
    print(f"Cached {inserted} Brooklyn locations.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await fetch_and_cache()
    yield

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/data")
async def get_data(time: str = "pm"):
    if time not in ("am", "pm", "md"):
        time = "pm"

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(f"SELECT * FROM locations WHERE {time} > 0").fetchall()
    conn.close()

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]},
            "properties": {
                "street": r["street_name"],
                "from": r["from_street"],
                "to": r["to_street"],
                "count": r[time],
                "am": r["am"],
                "pm": r["pm"],
                "md": r["md"],
            }
        })

    return JSONResponse({
        "type": "FeatureCollection",
        "features": features
    })

@app.get("/api/refresh")
async def refresh():
    await fetch_and_cache()
    return {"status": "ok"}
