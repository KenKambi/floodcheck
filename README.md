# FloodCheck

**Atlas Geosystems — Community Centered Geosystems**

FloodCheck is a web app that shows where flood water could reach after a storm. A user searches for their village or town (or marks an area on the map), chooses how much rain (or fetches real rainfall for a date), and the app maps flood depth, then counts the buildings, roads, schools and health facilities in the water. Results come with a plain-language flood risk card so local people can act on them.

It is part of the Atlas Geosystems project *The Geo-AI in Disaster Management: A Community Perspective* (concept in `docs/concept/`).

---

## Project layout

```
floodcheck/
├── README.md                  This file: build, run and deploy
├── requirements.txt           Python packages the app needs
├── requirements-dev.txt       Extra packages for running the tests
├── run.py                     Starts the app (python run.py)
├── start-windows.bat          One-click setup and start on Windows
├── Dockerfile                 Container build
├── docker-compose.yml         One-command container run
├── .env.example               Settings you can change
├── backend/
│   ├── server.py              Web server and API (FastAPI)
│   ├── floodmodel.py          Flood model: terrain, runoff, river flow, flood depth
│   ├── exposure.py            Buildings, roads, schools and health facilities in the water
│   ├── weather.py             Real rainfall, ground wetness and river flow for any date
│   ├── imagery.py             Sentinel-1 and Sentinel-2 images as map tiles
│   ├── geocode.py             Place search (village, town, school, market or coordinates)
│   └── static/                The dashboard (served by server.py)
│       ├── index.html
│       ├── css/floodcheck.css Styles in the Atlas Geosystems colours
│       ├── js/floodcheck.js   Map, place search, inputs, model runs, results, risk card
│       └── assets/            Logo files
├── results/                   GeoTIFF outputs (created at run time, not in git)
├── notebooks/
│   └── floodcheck_colab.ipynb Builds and runs the same app in Google Colab
├── tests/                     Smoke tests (no internet needed)
└── docs/
    ├── user-guide.md          Plain-language guide for community users
    ├── api.md                 API reference for developers
    └── concept/               Project concept note
```

## How it works

```
 Browser (dashboard)                       backend/server.py
 ───────────────────                       ─────────────────
 search place ─────────── GET /api/search ──▶  geocode.search() (OpenStreetMap Nominatim)
 mark area, choose rain  ── POST /api/runs ──▶  job queue (one run at a time)
                                                   │
                                                   ▼
                                    floodmodel.load_terrain()   ← elevation, land cover, soil (cached per area)
                                    exposure.load_exposure()    ← buildings, roads, facilities
                                    floodmodel.run_storm()      ← SCS-CN runoff → river flow → HAND flood depth
                                    exposure.analyse()          ← what is in the water
                                                   │
 poll progress           ◀─ GET /api/runs/{id} ────┘
 flood map, layers, risk card, depth on click, cross-section, GeoTIFF download
```

The first run in a new area downloads data and traces water flow (a few minutes). The last two areas stay in memory, so trying another storm in the same area takes seconds.

## Run it

You need **Python 3.10–3.12** and an internet connection (the model downloads open data while it runs).

### Option A — on your computer

**Windows, easiest:** double-click `start-windows.bat`. The first time, it finds Python 3.10–3.12, creates a private environment and installs the packages (a few minutes). After that it starts straight away. Open http://localhost:8000.

**Windows, PowerShell:** if `python --version` shows anything older than 3.10, use the Python launcher to pick a newer one:

```powershell
py -0p                                  # list the Pythons on this computer
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py
```

**macOS / Linux / any terminal:**

```bash
cd floodcheck
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open http://localhost:8000.

To let other devices on the same network use it (for example during a community session):

```bash
python run.py --host 0.0.0.0 --port 8000
```

Then open `http://<this-computer's-IP>:8000` on the other devices.

### Option B — Docker

```bash
cd floodcheck
docker compose up --build
```

Open http://localhost:8000. Results are saved to `./results` on your machine.

### Option C — Google Colab (no installation)

Open `notebooks/floodcheck_colab.ipynb` in Colab and choose **Runtime → Run all**. The last cell prints a link to the app. Keep the Colab tab open while you use it.

## Settings

Set these in your shell, a `.env`-aware host, or the container environment (see `.env.example`):

| Variable | Default | What it does |
|---|---|---|
| `FLOOD_HOST` | `127.0.0.1` | Address the server listens on (`0.0.0.0` to allow other devices) |
| `FLOOD_PORT` | `8000` | Port |
| `FLOOD_RESULTS_DIR` | `./results` | Where GeoTIFF results are written |
| `FLOOD_STREET_TILES` | *(empty)* | Your own street map tile URL(s), comma-separated, e.g. a MapTiler or Stadia URL with your key. Used before the built-in providers |
| `FLOOD_STREET_ATTRIBUTION` | `© OpenStreetMap contributors` | Credit line shown for your own street map |

Model settings (resolution, buffer, channel threshold, Manning's n and others) are in `DEFAULTS` at the top of `backend/floodmodel.py`. The largest area a user can draw is `MAX_FOCUS_DEG`; the allowed country extent is the bounding-box constant next to it.

## Customising the dashboard

| To change | Edit |
|---|---|
| Colours, fonts, spacing | `backend/static/css/floodcheck.css` (`:root` variables hold the logo palette) |
| Wording, steps, buttons | `backend/static/index.html` |
| Flood risk levels and advice | `backend/static/js/floodcheck.js` → `VERDICTS` and `riskLevel()` |
| Quick rain choices | `backend/static/index.html` → the `.presets` buttons (`data-mm`, `data-h`) |
| Example area | `backend/static/js/floodcheck.js` → `EXAMPLE_BOX` |
| Size of the area set by a place search | `backend/static/js/floodcheck.js` → `areaAround()` (default about 17 km a side) |
| Logo | replace the files in `backend/static/assets/` (keep the names) |

Page changes only need a browser refresh. Changes to `.py` files need a restart (or run `python run.py --reload` while developing).

**Current risk levels** (a starting point — align them with your CRVA thresholds):

| Level | Shown when any of these is true |
|---|---|
| Low | Little or no flooding |
| Some flooding | ≥ 1% of the area under water, any building in water, or ≥ 0.5 km of road under water |
| Serious | ≥ 5% of the area, ≥ 20 buildings in over 0.5 m, any school or health facility in water, or ≥ 1 km of main road |
| Severe | ≥ 15% of the area, ≥ 100 buildings in over 1 m, or a facility in over 1 m |

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The tests run the model on a made-up valley and check the server, dashboard files and input checks. They need no internet, so they are a quick way to confirm a new machine or container is set up correctly.

## Deploying to a server

- Run **one** process only (`run.py` already does). Runs, the job queue and cached terrain live in memory; several workers would split them.
- Put it behind a reverse proxy (nginx, Caddy) with HTTPS if it will be public.
- Give it at least **4 GB RAM** and 2 CPUs; a full-size area at 30 m uses a few hundred MB per cached area.
- Results in `results/` are not cleaned up automatically; clear old GeoTIFFs from time to time.
- The server needs outbound internet access to the data sources below.
- Place search uses the public Nominatim service, which allows light use only. For heavy public traffic, run your own Nominatim or switch `NOMINATIM_URL` in `backend/geocode.py` to a paid geocoder.

## Data sources (all open, no account needed)

| Data | Source |
|---|---|
| Elevation | Copernicus DEM GLO-30 (AWS open data) |
| Land cover | ESA WorldCover 2021, 10 m |
| Soil texture | ISRIC SoilGrids |
| Buildings, roads | Overture Maps (incl. OpenStreetMap, Microsoft and Google footprints) |
| Schools, health facilities | OpenStreetMap (Overpass API), Overture places as fallback |
| Rainfall | Open-Meteo: ERA5 reanalysis (from 1940) and forecast (next 16 days) |
| River flow | GloFAS v4 via Open-Meteo (from 1984, plus forecast) |
| Satellite images | Sentinel-2 (Element 84 Earth Search), Sentinel-1 RTC (Microsoft Planetary Computer), MODIS (NASA GIBS) |
| Place search | OpenStreetMap Nominatim (limited to the covered country, one request per second, results cached) |
| Street map | Esri World Street Map, falling back to Esri World Topographic Map |
| Satellite background | Esri World Imagery |
| 3D terrain | Terrarium elevation tiles (AWS open data) |

## Street map tiles

The street map comes from Esri World Street Map and switches automatically to Esri World Topographic Map if it fails. Two services are deliberately **not** used:

- the volunteer-run `tile.openstreetmap.org` servers, whose usage policy blocks apps like this one (they answer with a 403 "Access blocked" image);
- CARTO basemaps, which now draw "API KEY REQUIRED" over every tile when no key is given. These tiles load successfully, so the automatic fallback cannot detect them.

The Esri services suit pilots, training and light use; Esri expects an ArcGIS account for production use. Before a public or commercial launch, get a key from a tile provider (MapTiler, Stadia Maps, Thunderforest or CARTO) and set `FLOOD_STREET_TILES`; the dashboard then uses that first, with the free providers as back-up.

## Limitations

FloodCheck is a rapid screening tool built on global satellite data. It shows where flooding is likely; it is not an engineering flood study. It can miss walls, levees, culverts and drains, and rainfall is averaged over the area, so local downpours can be heavier. Check results against what communities have seen in past floods, and always follow official warnings.
