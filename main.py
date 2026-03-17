import os
import re
import json
import sqlite3
import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

DB_PATH = "/home/foottraffic/foottraffic.db"
NYC_COUNTS_API = "https://data.cityofnewyork.us/resource/cqsj-cfgu.json"
NYC_CENTERLINE_API = "https://data.cityofnewyork.us/resource/inkn-q76z.json"
MTA_RIDERSHIP_API = "https://data.ny.gov/resource/5wq4-mkjj.json"

ABBREVS = {
    "AVENUE": "AVE", "STREET": "ST", "BOULEVARD": "BLVD", "PLACE": "PL",
    "ROAD": "RD", "DRIVE": "DR", "COURT": "CT", "LANE": "LN",
    "PARKWAY": "PKY", "BROADWAY": "BROADWAY", "EXPRESSWAY": "EXPY",
}

def normalize_street(name):
    name = name.upper().strip()
    # Remove ordinal suffixes: 5TH -> 5, 3RD -> 3
    name = re.sub(r'(\d+)(ST|ND|RD|TH)\b', r'\1', name)
    tokens = name.split()
    result = []
    for t in tokens:
        result.append(ABBREVS.get(t, t))
    return set(result)

def names_match(our_name, centerline_name):
    a = normalize_street(our_name)
    b = normalize_street(centerline_name)
    if not a or not b:
        return False
    return len(a & b) >= min(len(a), 2)

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
            md INTEGER,
            geometry_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY,
            station_complex_id TEXT UNIQUE,
            name TEXT,
            lines TEXT,
            lat REAL,
            lng REAL,
            ridership INTEGER
        )
    """)
    # Add geometry_json column if upgrading from old schema
    try:
        conn.execute("ALTER TABLE locations ADD COLUMN geometry_json TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

async def fetch_stations():
    print("Fetching Brooklyn subway station ridership from MTA...")
    params = {
        "$select": "station_complex,station_complex_id,latitude,longitude,sum(ridership) as total_ridership",
        "$where": "borough='Brooklyn'",
        "$group": "station_complex,station_complex_id,latitude,longitude",
        "$order": "total_ridership DESC",
        "$limit": 50,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(MTA_RIDERSHIP_API, params=params)
        data = resp.json()

    conn = sqlite3.connect(DB_PATH)
    for row in data:
        name_raw = row.get("station_complex", "")
        lines_match = re.search(r'\(([^)]+)\)', name_raw)
        lines = lines_match.group(1) if lines_match else ""
        name = re.sub(r'\s*\([^)]+\)', '', name_raw).strip()
        conn.execute("""
            INSERT OR REPLACE INTO stations
              (station_complex_id, name, lines, lat, lng, ridership)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            row.get("station_complex_id"), name, lines,
            float(row.get("latitude", 0)),
            float(row.get("longitude", 0)),
            int(float(row.get("total_ridership", 0))),
        ))
    conn.commit()
    conn.close()
    print(f"Cached {len(data)} Brooklyn subway stations.")

async def fetch_street_geometry(client, lat, lng, street_name):
    try:
        params = {
            "$where": f"within_circle(the_geom,{lat},{lng},120) AND boroughcode='3'",
            "$limit": 10,
        }
        resp = await client.get(NYC_CENTERLINE_API, params=params, timeout=15)
        segments = resp.json()

        # Try name-matched segment first
        for seg in segments:
            full_name = seg.get("full_street_name", "")
            if names_match(street_name, full_name):
                geom = seg.get("the_geom", {})
                if geom.get("type") == "MultiLineString" and geom["coordinates"]:
                    return geom["coordinates"][0]
                elif geom.get("type") == "LineString":
                    return geom["coordinates"]

        # Fallback: return closest segment's geometry
        if segments:
            geom = segments[0].get("the_geom", {})
            if geom.get("type") == "MultiLineString" and geom["coordinates"]:
                return geom["coordinates"][0]
    except Exception as e:
        print(f"Geometry fetch failed for {street_name}: {e}")
    return None

async def fetch_and_cache():
    print("Fetching Brooklyn pedestrian data from NYC Open Data...")
    params = {
        "$where": "borough='Brooklyn'",
        "$limit": 1000,
        "$order": "objectid ASC",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(NYC_COUNTS_API, params=params)
        data = resp.json()

    def safe_int(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    conn = sqlite3.connect(DB_PATH)
    rows_to_geocode = []

    for row in data:
        geom = row.get("the_geom", {})
        coords = geom.get("coordinates", [None, None])
        lng, lat = coords[0], coords[1]
        if lat is None or lng is None:
            continue

        pm_val = row.get("may25_pm") or row.get("may25_p_m")
        street_name = row.get("street_nam", "")
        objectid = row.get("objectid")

        conn.execute("""
            INSERT OR REPLACE INTO locations
              (objectid, street_name, from_street, to_street, lat, lng, am, pm, md)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            objectid, street_name,
            row.get("from_stree", ""), row.get("to_street", ""),
            lat, lng,
            safe_int(row.get("may25_am")),
            safe_int(pm_val),
            safe_int(row.get("may25_md")),
        ))
        rows_to_geocode.append((objectid, lat, lng, street_name))

    conn.commit()

    # Fetch street geometries with bounded concurrency
    print(f"Fetching street geometries for {len(rows_to_geocode)} locations...")
    semaphore = asyncio.Semaphore(10)

    async def fetch_with_semaphore(client, lat, lng, name):
        async with semaphore:
            return await fetch_street_geometry(client, lat, lng, name)

    async with httpx.AsyncClient(timeout=30) as client:
        tasks = [
            fetch_with_semaphore(client, lat, lng, name)
            for _, lat, lng, name in rows_to_geocode
        ]
        geometries = await asyncio.gather(*tasks)

    matched = 0
    for (objectid, _, _, _), geo_coords in zip(rows_to_geocode, geometries):
        if geo_coords:
            conn.execute(
                "UPDATE locations SET geometry_json=? WHERE objectid=?",
                (json.dumps(geo_coords), objectid)
            )
            matched += 1

    conn.commit()
    conn.close()
    print(f"Cached {len(rows_to_geocode)} locations, {matched} with street geometry.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await fetch_and_cache()
    await fetch_stations()
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
        geo_coords = json.loads(r["geometry_json"]) if r["geometry_json"] else None

        if geo_coords:
            geometry = {"type": "LineString", "coordinates": geo_coords}
        else:
            geometry = {"type": "Point", "coordinates": [r["lng"], r["lat"]]}

        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "street": r["street_name"],
                "from": r["from_street"],
                "to": r["to_street"],
                "count": r[time],
                "am": r["am"],
                "pm": r["pm"],
                "md": r["md"],
                "has_geometry": geo_coords is not None,
            }
        })

    return JSONResponse({"type": "FeatureCollection", "features": features})

@app.get("/api/stations")
async def get_stations():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM stations ORDER BY ridership DESC").fetchall()
    conn.close()

    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]},
            "properties": {
                "name": r["name"],
                "lines": r["lines"],
                "ridership": r["ridership"],
            }
        })
    return JSONResponse({"type": "FeatureCollection", "features": features})

@app.get("/api/refresh")
async def refresh():
    await fetch_and_cache()
    await fetch_stations()
    return {"status": "ok"}
