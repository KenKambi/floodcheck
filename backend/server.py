"""Web server for the flood model: serves the map page and runs flood simulations in the background."""
import io, json, os, queue, threading, time, traceback, uuid
from collections import OrderedDict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image

import floodmodel as fm
import exposure as ex
import weather as wx
import imagery as im
import geocode as geo

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
RESULTS_DIR = os.environ.get("FLOOD_RESULTS_DIR", "./results")
os.makedirs(RESULTS_DIR, exist_ok=True)

app = FastAPI(title="FloodCheck rapid flood model")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")   # logo and other page assets

class Inflow(BaseModel):
    lat: float
    lon: float
    flood_m3s: float = Field(gt=0)
    normal_m3s: float = Field(default=0, ge=0)

class RunRequest(BaseModel):
    bbox: list[float] = Field(min_length=4, max_length=4)   # west, south, east, north
    rain_mm: float = Field(gt=0, le=1000)
    rain_hours: float = Field(gt=0, le=240)
    wetness: str = Field(default="normal", pattern="^(dry|normal|wet)$")
    inflows: list[Inflow] = []

RUNS = {}                       # run id -> state dict
TERRAIN = OrderedDict()         # cached terrain for recently used areas (the slow part)
TERRAIN_CACHE_SIZE = 2
JOBS = queue.Queue()

def terrain_key(bbox):
    return tuple(round(v, 4) for v in bbox)

def worker():
    while True:
        run_id = JOBS.get()
        run = RUNS[run_id]
        req = run["request"]
        run["status"] = "running"; run["started"] = time.time()

        def log(msg):
            run["step"] = msg
            run["log"].append(msg)

        try:
            key = terrain_key(req.bbox)
            if key in TERRAIN:
                TERRAIN.move_to_end(key)
                log("Reusing downloaded data for this area")
                terrain = TERRAIN[key]
            else:
                terrain = fm.load_terrain(req.bbox, log=log)
                terrain["exposure"] = ex.load_exposure(req.bbox, log=log)
                TERRAIN[key] = terrain
                while len(TERRAIN) > TERRAIN_CACHE_SIZE:
                    TERRAIN.popitem(last=False)
            result = fm.run_storm(terrain, req.rain_mm, req.rain_hours, req.wetness,
                                  [i.model_dump() for i in req.inflows], log=log)
            log("Checking buildings, roads and facilities")
            exp_summary, exp_layers = ex.analyse(result, terrain["exposure"], req.bbox)
            result["summary"]["exposure"] = exp_summary
            run["layers"] = {k: json.dumps(v, separators=(",", ":")) for k, v in exp_layers.items()}
            log("Drawing the map")
            buf = io.BytesIO()
            Image.fromarray(fm.depth_rgba(result["depth"], result["permanent_water"]), "RGBA").save(buf, "PNG", optimize=True)
            run["png"] = buf.getvalue()
            run["tif_path"] = os.path.join(RESULTS_DIR, f"{run_id}.tif")
            fm.save_depth_geotiff(result, run["tif_path"])
            run["result"] = result
            run["corners"] = fm.image_corners(result)
            run["summary"] = result["summary"]
            run["status"] = "done"
        except Exception as err:
            traceback.print_exc()
            run["status"] = "failed"
            run["error"] = str(err) if isinstance(err, (ValueError, RuntimeError)) else \
                f"The model stopped with an internal error: {err}"
        finally:
            run["finished"] = time.time()
            JOBS.task_done()

threading.Thread(target=worker, daemon=True).start()

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/api/config")
def config():
    out = dict(kenya_bbox=fm.KENYA_BBOX, max_side_deg=fm.MAX_FOCUS_DEG)
    tiles = os.environ.get("FLOOD_STREET_TILES", "").strip()       # optional own street map provider
    if tiles:
        out["street_map"] = dict(tiles=[t.strip() for t in tiles.split(",") if t.strip()],
                                 attribution=os.environ.get("FLOOD_STREET_ATTRIBUTION", "© OpenStreetMap contributors"))
    return out

@app.post("/api/runs")
def create_run(req: RunRequest):
    try:
        fm.validate_focus(req.bbox)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    run_id = uuid.uuid4().hex[:10]
    cached = terrain_key(req.bbox) in TERRAIN
    RUNS[run_id] = dict(status="queued", step="Waiting to start", log=[], request=req,
                        created=time.time(), cached_terrain=cached)
    JOBS.put(run_id)
    return dict(id=run_id, queue_position=JOBS.qsize(), cached_terrain=cached)

def get_run(run_id):
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="This run no longer exists. Run the model again.")
    return run

@app.get("/api/runs/{run_id}")
def run_status(run_id: str):
    run = get_run(run_id)
    out = dict(id=run_id, status=run["status"], step=run["step"], log=run["log"],
               elapsed_s=round(time.time() - run.get("started", time.time()), 1),
               cached_terrain=run["cached_terrain"])
    if run["status"] == "done":
        out.update(summary=run["summary"], corners=run["corners"],
                   image_url=f"api/runs/{run_id}/depth.png", geotiff_url=f"api/runs/{run_id}/depth.tif",
                   layers={k: f"api/runs/{run_id}/layers/{k}.geojson" for k in run.get("layers", {})})
    if run["status"] == "failed":
        out["error"] = run["error"]
    return out

@app.get("/api/runs/{run_id}/depth.png")
def run_png(run_id: str):
    run = get_run(run_id)
    if "png" not in run:
        raise HTTPException(status_code=409, detail="This run hasn't finished yet.")
    return Response(run["png"], media_type="image/png", headers={"Cache-Control": "max-age=3600"})

@app.get("/api/runs/{run_id}/depth.tif")
def run_tif(run_id: str):
    run = get_run(run_id)
    if "tif_path" not in run:
        raise HTTPException(status_code=409, detail="This run hasn't finished yet.")
    return FileResponse(run["tif_path"], media_type="image/tiff", filename=f"flood_depth_{run_id}.tif")

@app.get("/api/runs/{run_id}/depth")
def run_depth_at(run_id: str, lat: float, lon: float):
    run = get_run(run_id)
    if "result" not in run:
        raise HTTPException(status_code=409, detail="This run hasn't finished yet.")
    depth, water = fm.depth_at(run["result"], lat, lon)
    return dict(depth_m=depth, permanent_water=water)

@app.get("/api/runs/{run_id}/layers/{name}.geojson")
def run_layer(run_id: str, name: str):
    run = get_run(run_id)
    if name not in run.get("layers", {}):
        raise HTTPException(status_code=404, detail="This layer isn't available for this run.")
    return Response(run["layers"][name], media_type="application/geo+json")

class ProfileRequest(BaseModel):
    coords: list[list[float]] = Field(min_length=2, max_length=50)

@app.post("/api/runs/{run_id}/profile")
def run_profile(run_id: str, req: ProfileRequest):
    run = get_run(run_id)
    if "result" not in run:
        raise HTTPException(status_code=409, detail="This run hasn't finished yet.")
    return fm.profile(run["result"], req.coords)

# ----------------------------------------------------------------------------- real weather, river flow, imagery

def _bbox_param(bbox: str):
    try:
        b = [float(v) for v in bbox.split(",")]
        assert len(b) == 4 and b[0] < b[2] and b[1] < b[3]
        return b
    except Exception:
        raise HTTPException(status_code=422, detail="The area is invalid. Draw it again.")

def _friendly(fn, *args):
    try:
        return fn(*args)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err))
    except RuntimeError as err:
        raise HTTPException(status_code=503, detail=str(err))

@app.get("/api/weather")
def weather(bbox: str, date: str, days: int = 1):
    return _friendly(wx.rainfall_for_event, _bbox_param(bbox), date, days)

@app.get("/api/river-flow")
def river_flow(lat: float, lon: float, date: str, days: int = 1):
    return _friendly(wx.river_flow_for_event, lat, lon, date, days)

@app.get("/api/imagery/search")
def imagery_search(source: str, bbox: str, date: str):
    return _friendly(im.find_images, source, _bbox_param(bbox), date)

@app.get("/api/imagery/{sel_id}/{z}/{x}/{y}.png")
def imagery_tile(sel_id: str, z: int, x: int, y: int, water: bool = False):
    try:
        png = im.render_tile(sel_id, z, x, y, water)
    except KeyError:
        raise HTTPException(status_code=404, detail="Image selection expired. Choose the date again.")
    except Exception as err:
        print("tile error", sel_id, z, x, y, err)
        png = im._empty_tile()
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=86400"})

# ----------------------------------------------------------------------------- place search

@app.get("/api/search")
def place_search(q: str):
    """Find a village, town, school or market by name (or typed coordinates) inside the covered area."""
    return dict(results=_friendly(geo.search, q, fm.KENYA_BBOX), attribution="Search © OpenStreetMap contributors (Nominatim)")
