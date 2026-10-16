"""Rapid flood model: rain + terrain -> flood depth, for any area in the country.

Same methodology as the original modelling notebook, reorganised into functions so a web server can call it:
  load_terrain()        gets the terrain and flow network for an area. Reads it from PREPARED TERRAIN when the
                        area is inside a prepared region (fast, light), otherwise downloads and builds it live
                        (slow, needs a lot of memory)
  run_storm()           runs runoff, river flow and flooding for one storm (fast part)
  precompute_region()   prepares the terrain for a whole region once, ahead of time (see tools/precompute_terrain.py)

Prepared terrain: everything that depends only on the land (conditioned elevation, flow directions, upstream area,
longest flow path, land cover, soils) is computed once per region and stored as tiled GeoTIFFs. A live check then
reads only the small window it needs. Set FLOOD_TERRAIN to the folder or https:// address that holds index.json.
"""
import os, json, time, heapq, urllib.request
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin, rowcol
from rasterio.vrt import WarpedVRT
from rasterio.warp import Resampling, transform_bounds
from rasterio.warp import transform as warp_transform
from rasterio.windows import from_bounds as window_from_bounds, Window
import rasterio.windows
from scipy import ndimage
from numba import njit

os.environ.update(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES",
                  GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="3",
                  CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.vrt")

NODATA = -9999.0
KENYA_BBOX = (33.8, -4.8, 42.0, 5.1)           # rough country bounds, used to reject areas outside it
MAX_FOCUS_DEG = float(os.environ.get("FLOOD_MAX_SIDE_DEG", "0.40"))   # largest area side a user can draw (0.40 = ~44 km)
# Where prepared terrain lives: a folder or an https:// address containing index.json (empty = <project>/terrain)
TERRAIN_STORE = os.environ.get("FLOOD_TERRAIN", "").strip() or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "terrain")
# Build terrain live (download + flow tracing) for areas outside the prepared regions? Needs 2-4 GB of memory.
LIVE_TERRAIN = os.environ.get("FLOOD_LIVE_TERRAIN", "1").strip().lower() not in ("0", "false", "no", "off")

DEFAULTS = dict(
    res_m=30, buffer_deg=0.15, prepared_buffer_deg=0.03,
    channel_threshold_km2=1.0, max_reach_length_m=2000, min_reach_for_curve_m=300,
    channel_manning_n=0.04, min_slope=1e-4, ia_ratio=0.2,
    max_stage_m=15.0, stage_step_m=0.2, min_depth_m=0.10, min_patch_cells=10,
    default_soil_group="C",
)

# ----------------------------------------------------------------------------- grid and data

def utm_epsg_for(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone

def make_model_grid(bbox, res):
    lon_c, lat_c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    crs = CRS.from_epsg(utm_epsg_for(lon_c, lat_c))
    x0, y0, x1, y1 = transform_bounds("EPSG:4326", crs, *bbox, densify_pts=21)
    x0, y0 = np.floor(x0 / res) * res, np.floor(y0 / res) * res
    x1, y1 = np.ceil(x1 / res) * res, np.ceil(y1 / res) * res
    return dict(crs=crs, transform=from_origin(x0, y1, res, res), res=res,
                width=int(round((x1 - x0) / res)), height=int(round((y1 - y0) / res)))

def read_to_grid(hrefs, grid, resampling, dtype, dst_nodata, retries=3, log=print):
    out = np.full((grid["height"], grid["width"]), dst_nodata, dtype=dtype)
    n_read = 0
    for href in hrefs:
        data = None
        for attempt in range(retries):
            try:
                with rasterio.open(href) as src:
                    src_nodata = src.nodata if src.nodata is not None else dst_nodata
                    with WarpedVRT(src, crs=grid["crs"], transform=grid["transform"],
                                   width=grid["width"], height=grid["height"],
                                   resampling=resampling, src_nodata=src_nodata, nodata=dst_nodata) as vrt:
                        data = vrt.read(1).astype(dtype)
                break
            except rasterio.errors.RasterioIOError as err:
                if any(k in str(err) for k in ("404", "does not exist", "No such file")) or attempt == retries - 1:
                    log(f"skipped {href.split('/')[-1]}")
                    break
                time.sleep(5 * (attempt + 1))
        if data is None:
            continue
        n_read += 1
        fill = (out == dst_nodata) & (data != dst_nodata)
        out[fill] = data[fill]
    if n_read == 0:
        raise RuntimeError("None of the data tiles could be read. Check the internet connection and the area.")
    return out

def copernicus_dem_urls(bbox):
    w, s, e, n = bbox
    urls = []
    for lat in range(int(np.floor(s)), int(np.ceil(n))):
        for lon in range(int(np.floor(w)), int(np.ceil(e))):
            name = (f"Copernicus_DSM_COG_10_{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00_"
                    f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00_DEM")
            urls.append(f"https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif")
    return urls

def worldcover_urls(bbox):
    w, s, e, n = bbox
    urls = []
    for lat in range(int(np.floor(s / 3) * 3), int(np.ceil(n / 3) * 3), 3):
        for lon in range(int(np.floor(w / 3) * 3), int(np.ceil(e / 3) * 3), 3):
            tile = f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"
            urls.append("https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
                        f"v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")
    return urls

SOILGRIDS = "/vsicurl/https://files.isric.org/soilgrids/latest/data/{v}/{v}_5-15cm_mean.vrt"
GROUP_CODE = {"A": 1, "B": 2, "C": 3, "D": 4}

def soil_groups_from_texture(clay_pct, sand_pct):
    hsg = np.full(clay_pct.shape, 3, dtype="uint8")
    hsg[clay_pct < 20] = 2
    hsg[(clay_pct < 10) & (sand_pct > 90)] = 1
    hsg[(clay_pct >= 20) & (clay_pct < 40)] = 3
    hsg[clay_pct >= 40] = 4
    return hsg

# ----------------------------------------------------------------------------- numba engine

D8_DR = np.array([-1, -1, 0, 1, 1, 1, 0, -1])
D8_DC = np.array([0, 1, 1, 1, 0, -1, -1, -1])

@njit(cache=True)
def priority_flood_d8(dem, valid, res, eps):
    nr, nc = dem.shape
    n = nr * nc
    filled = dem.astype(np.float64).ravel().copy()
    vflat = valid.ravel()
    visited = np.zeros(n, np.bool_)
    heap = [(0.0, np.int64(0), np.int64(0))]
    heap.pop()
    cnt = np.int64(0)
    for r in range(nr):
        for c in range(nc):
            i = r * nc + c
            if not vflat[i]:
                continue
            boundary = r == 0 or c == 0 or r == nr - 1 or c == nc - 1
            if not boundary:
                for k in range(8):
                    if not vflat[(r + D8_DR[k]) * nc + c + D8_DC[k]]:
                        boundary = True
                        break
            if boundary:
                visited[i] = True
                heapq.heappush(heap, (filled[i], cnt, np.int64(i))); cnt += 1
    while len(heap) > 0:
        z, _, i = heapq.heappop(heap)
        r, c = i // nc, i % nc
        for k in range(8):
            rr, cc = r + D8_DR[k], c + D8_DC[k]
            if rr < 0 or cc < 0 or rr >= nr or cc >= nc:
                continue
            j = rr * nc + cc
            if not vflat[j] or visited[j]:
                continue
            visited[j] = True
            if filled[j] <= z:
                filled[j] = z + eps
            heapq.heappush(heap, (filled[j], cnt, np.int64(j))); cnt += 1
    rec = np.full(n, -1, np.int64)
    dist = np.full(n, res)
    for r in range(nr):
        for c in range(nc):
            i = r * nc + c
            if not vflat[i]:
                continue
            best, best_drop, best_d = -1, 0.0, res
            for k in range(8):
                rr, cc = r + D8_DR[k], c + D8_DC[k]
                if rr < 0 or cc < 0 or rr >= nr or cc >= nc:
                    continue
                j = rr * nc + cc
                if not vflat[j]:
                    continue
                d = res * 1.4142135623730951 if (D8_DR[k] != 0 and D8_DC[k] != 0) else res
                drop = (filled[i] - filled[j]) / d
                if drop > best_drop:
                    best, best_drop, best_d = j, drop, d
            rec[i] = best
            dist[i] = best_d
    return filled.reshape(nr, nc), rec, dist

@njit(cache=True)
def topological_order(rec, valid_flat):
    n = rec.size
    indeg = np.zeros(n, np.int32)
    for i in range(n):
        if rec[i] >= 0:
            indeg[rec[i]] += 1
    order = np.empty(n, np.int64)
    head, tail = 0, 0
    for i in range(n):
        if valid_flat[i] and indeg[i] == 0:
            order[tail] = i; tail += 1
    while head < tail:
        i = order[head]; head += 1
        r = rec[i]
        if r >= 0:
            indeg[r] -= 1
            if indeg[r] == 0:
                order[tail] = r; tail += 1
    return order[:tail]

@njit(cache=True)
def accumulate(order, rec, dist, cell_area_km2, runoff_mm, z):
    n = rec.size
    area = np.zeros(n); vol = np.zeros(n); L = np.zeros(n); zhead = z.copy()
    for i in order:
        area[i] += cell_area_km2
        vol[i] += runoff_mm[i] * cell_area_km2
    for i in order:
        r = rec[i]
        if r >= 0:
            area[r] += area[i]; vol[r] += vol[i]
            cand = L[i] + dist[i]
            if cand > L[r]:
                L[r] = cand; zhead[r] = zhead[i]
    return area, vol, L, zhead

@njit(cache=True)
def accumulate_volume(order, rec, cell_area_km2, runoff_mm, extra_vol):
    """Runoff volume flowing through each cell (mm x km2); extra_vol is water entering from outside the window."""
    n = rec.size
    vol = extra_vol.copy()
    for i in order:
        vol[i] += runoff_mm[i] * cell_area_km2
    for i in order:
        r = rec[i]
        if r >= 0:
            vol[r] += vol[i]
    return vol

@njit(cache=True)
def build_reaches(order, rec, dist, is_ch, max_len):
    n = rec.size
    n_ch_donors = np.zeros(n, np.int32); ch_donor = np.full(n, -1, np.int64)
    for i in order:
        if is_ch[i]:
            r = rec[i]
            if r >= 0 and is_ch[r]:
                n_ch_donors[r] += 1; ch_donor[r] = i
    reach = np.full(n, -1, np.int64); rlen = np.zeros(n); nreach = 0
    for i in order:
        if not is_ch[i]:
            continue
        if n_ch_donors[i] == 1:
            d = ch_donor[i]
            if rlen[d] + dist[d] < max_len:
                reach[i] = reach[d]; rlen[i] = rlen[d] + dist[d]
                continue
        reach[i] = nreach; nreach += 1
    return reach, nreach

@njit(cache=True)
def compute_hand(order, rec, is_ch, z):
    n = rec.size
    drain = np.full(n, -1, np.int64)
    for k in range(order.size - 1, -1, -1):
        i = order[k]
        if is_ch[i]:
            drain[i] = i
        elif rec[i] >= 0:
            drain[i] = drain[rec[i]]
    hand = np.full(n, np.nan)
    for i in range(n):
        if drain[i] >= 0:
            hand[i] = max(z[i] - z[drain[i]], 0.0)
    return drain, hand

# ----------------------------------------------------------------------------- runoff tables

CN_TABLE = {10: (30, 55, 70, 77), 20: (35, 56, 70, 77), 30: (39, 61, 74, 80), 40: (67, 78, 85, 89),
            50: (89, 92, 94, 95), 60: (77, 86, 91, 94), 70: (98, 98, 98, 98), 80: (100, 100, 100, 100),
            90: (90, 90, 90, 90), 95: (90, 90, 90, 90), 100: (49, 69, 79, 84)}

def curve_numbers(landcover, soil_group, wetness):
    lut = np.zeros((256, 5)); lut[:, 1:] = CN_TABLE[30]
    for cls, row in CN_TABLE.items():
        lut[cls, 1:] = row
    cn = lut[landcover, np.clip(soil_group, 1, 4)]
    if wetness == "dry":
        cn = cn / (2.281 - 0.01281 * cn)
    elif wetness == "wet":
        cn = cn / (0.427 + 0.00573 * cn)
    return np.clip(cn, 1, 100)

def scs_runoff(rain_mm, cn, ia_ratio):
    S = 25400.0 / cn - 254.0
    Ia = ia_ratio * S
    return np.where(rain_mm > Ia, (rain_mm - Ia) ** 2 / np.maximum(rain_mm - Ia + S, 1e-9), 0.0)

# ----------------------------------------------------------------------------- pipeline

def validate_focus(focus_bbox):
    w, s, e, n = focus_bbox
    if not (w < e and s < n):
        raise ValueError("The area is empty. Draw a rectangle on the map.")
    if e - w > MAX_FOCUS_DEG or n - s > MAX_FOCUS_DEG:
        raise ValueError(f"The area is too large. Keep each side under {MAX_FOCUS_DEG}° (about 44 km).")
    kw, ks, ke, kn = KENYA_BBOX
    if w < kw or e > ke or s < ks or n > kn:
        raise ValueError("The area is outside the area the model covers. Draw it inside the country.")

def download_inputs(domain, p=DEFAULTS, log=print):
    """Elevation, land cover and soil groups for a lon/lat box, on a UTM grid."""
    grid = make_model_grid(domain, p["res_m"])
    shape = (grid["height"], grid["width"])

    log("Downloading ground heights")
    dem = read_to_grid(copernicus_dem_urls(domain), grid, Resampling.bilinear, "float32", NODATA, log=log)
    valid = dem != NODATA
    log("Downloading land cover")
    landcover = read_to_grid(worldcover_urls(domain), grid, Resampling.mode, "uint8", 0, log=log)
    log("Downloading soil data")
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3"):
            clay = read_to_grid([SOILGRIDS.format(v="clay")], grid, Resampling.nearest, "int16", -32768, log=log)
            sand = read_to_grid([SOILGRIDS.format(v="sand")], grid, Resampling.nearest, "int16", -32768, log=log)
        missing = (clay == -32768) | (sand == -32768)
        soil_group = soil_groups_from_texture(clay / 10.0, sand / 10.0)
        soil_group[missing] = GROUP_CODE[p["default_soil_group"]]
        soil_note = None
    except Exception as err:
        soil_group = np.full(shape, GROUP_CODE[p["default_soil_group"]], dtype="uint8")
        soil_note = f"Soil data unavailable; soil group {p['default_soil_group']} used everywhere."
        log(soil_note)
    return grid, dem, valid, landcover, soil_group, soil_note

def load_terrain(focus_bbox, p=DEFAULTS, log=print):
    """Terrain and flow network for the focus area: from prepared terrain when possible, otherwise built live."""
    region = find_region(focus_bbox, p)
    if region is not None:
        return load_prepared(focus_bbox, region, p, log)
    if not LIVE_TERRAIN:
        raise ValueError("This area isn't prepared yet. Draw your area inside the prepared regions outlined on the map.")
    b = p["buffer_deg"]
    w, s, e, n = focus_bbox
    domain = (w - b, s - b, e + b, n + b)
    grid, dem, valid, landcover, soil_group, soil_note = download_inputs(domain, p, log)
    shape = (grid["height"], grid["width"])

    log("Tracing how water flows across the land")
    res = float(p["res_m"])
    dem_cond, rec, dist = priority_flood_d8(dem, valid, res, 1e-6)
    z_flat = dem_cond.ravel().copy()
    valid_flat = valid.ravel()
    order = topological_order(rec, valid_flat)
    return dict(focus_bbox=tuple(focus_bbox), domain=domain, grid=grid, shape=shape, dem=dem, valid=valid,
                landcover=landcover, soil_group=soil_group, soil_note=soil_note,
                dem_cond=dem_cond, rec=rec, dist=dist, z_flat=z_flat, valid_flat=valid_flat, order=order,
                source="live")

# ----------------------------------------------------------------------------- prepared terrain

LAYERS = dict(dem=("float32", NODATA), demcond=("float32", NODATA), fdir=("uint8", 255), facc=("float32", 0.0),
              flowlen=("float32", 0.0), zhead=("float32", NODATA), landcover=("uint8", 0), soil=("uint8", 0))
_INDEX = {"store": None, "data": None}

def _is_url(path):
    return path.startswith("http://") or path.startswith("https://")

def _join(base, *parts):
    return "/".join([base.rstrip("/")] + [p.strip("/") for p in parts]) if _is_url(base) else os.path.join(base, *parts)

def _gdal_path(path):
    return "/vsicurl/" + path if _is_url(path) else path

def terrain_index(reload=False):
    """index.json of the prepared terrain store, or None when there is none."""
    if _INDEX["store"] != TERRAIN_STORE or reload:
        _INDEX.update(store=TERRAIN_STORE, data=None)
        path = _join(TERRAIN_STORE, "index.json")
        try:
            if _is_url(path):
                with urllib.request.urlopen(path, timeout=20) as r:
                    _INDEX["data"] = json.load(r)
            elif os.path.exists(path):
                with open(path) as f:
                    _INDEX["data"] = json.load(f)
        except Exception as err:
            print(f"Prepared terrain index could not be read ({path}): {err}")
    return _INDEX["data"]

def prepared_regions():
    idx = terrain_index()
    return [dict(name=r["name"], bbox=r["bbox"]) for r in (idx or {}).get("regions", [])]

def find_region(focus_bbox, p=DEFAULTS):
    """The prepared region that holds the focus area plus its buffer with the widest margin, or None."""
    b = p["prepared_buffer_deg"]
    w, s, e, n = focus_bbox[0] - b, focus_bbox[1] - b, focus_bbox[2] + b, focus_bbox[3] + b
    best, best_margin = None, -1.0
    for r in (terrain_index() or {}).get("regions", []):
        rw, rs, re_, rn = r["bbox"]
        margin = min(w - rw, s - rs, re_ - e, rn - n)
        if margin >= 0 and margin > best_margin:
            best, best_margin = r, margin
    return best

def _codes_from_receivers(rec, nc):
    """D8 direction code (0-7, 255 = none) for each cell from its receiver index."""
    n = rec.size
    code = np.full(n, 255, dtype="uint8")
    idx = np.nonzero(rec >= 0)[0]
    dr = rec[idx] // nc - idx // nc
    dc = rec[idx] % nc - idx % nc
    lut = np.full((3, 3), 255, dtype="uint8")
    for k in range(8):
        lut[D8_DR[k] + 1, D8_DC[k] + 1] = k
    code[idx] = lut[dr + 1, dc + 1]
    return code

def precompute_region(name, region_bbox, out_dir, p=DEFAULTS, log=print, inputs=None):
    """Prepare the terrain for one region and register it in out_dir/index.json.

    inputs: optional (grid, dem, valid, landcover, soil_group, soil_note) instead of downloading (used by tests)."""
    os.makedirs(os.path.join(out_dir, name), exist_ok=True)
    grid, dem, valid, landcover, soil_group, soil_note = inputs or download_inputs(tuple(region_bbox), p, log)
    nr, nc = dem.shape
    log(f"Tracing how water flows across {nr} x {nc} cells")
    res = float(grid["res"])
    dem_cond, rec, dist = priority_flood_d8(dem, valid, res, 1e-6)
    order = topological_order(rec, valid.ravel())
    log("Measuring upstream areas and flow paths")
    area, _, flow_len, zhead = accumulate(order, rec, dist, res * res / 1e6, np.zeros(rec.size), dem_cond.ravel())
    layers = dict(dem=dem, demcond=dem_cond, fdir=_codes_from_receivers(rec, nc).reshape(nr, nc),
                  facc=area.reshape(nr, nc), flowlen=flow_len.reshape(nr, nc), zhead=zhead.reshape(nr, nc),
                  landcover=landcover, soil=soil_group)
    del rec, dist, order, area, flow_len, zhead
    for key, (dtype, nodata) in LAYERS.items():
        log(f"Writing {key}.tif")
        arr = layers[key].astype(dtype)
        if dtype == "float32":
            arr[~valid] = nodata
        with rasterio.open(os.path.join(out_dir, name, f"{key}.tif"), "w", driver="GTiff", width=nc, height=nr,
                           count=1, dtype=dtype, crs=grid["crs"], transform=grid["transform"], nodata=nodata,
                           tiled=True, blockxsize=512, blockysize=512, compress="deflate",
                           predictor=3 if dtype == "float32" else 2, BIGTIFF="IF_SAFER") as dst:
            dst.write(arr, 1)
    meta = dict(name=name, bbox=[round(float(v), 6) for v in region_bbox], res_m=res, width=nc, height=nr,
                crs=grid["crs"].to_string(), soil_note=soil_note, created=time.strftime("%Y-%m-%d"),
                channel_threshold_km2=p["channel_threshold_km2"])
    with open(os.path.join(out_dir, name, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    idx_path = os.path.join(out_dir, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else dict(version=1, regions=[])
    idx["regions"] = [r for r in idx["regions"] if r["name"] != name] + [
        dict(name=name, bbox=meta["bbox"], path=name, res_m=res)]
    with open(idx_path, "w") as f:
        json.dump(idx, f, indent=1)
    log(f"Region '{name}' ready")
    return meta

def load_prepared(focus_bbox, region, p=DEFAULTS, log=print):
    """Read the window of a prepared region that covers the focus area plus a small buffer."""
    log("Reading prepared terrain for this area")
    b = p["prepared_buffer_deg"]
    w, s, e, n = focus_bbox
    domain = (w - b, s - b, e + b, n + b)
    base = _join(TERRAIN_STORE, region.get("path", region["name"]))
    src_path = lambda key: _gdal_path(_join(base, f"{key}.tif"))
    with rasterio.open(src_path("demcond")) as src:
        crs, tr, H, W = src.crs, src.transform, src.height, src.width
        res = float(abs(tr.a))
    x0, y0, x1, y1 = transform_bounds("EPSG:4326", crs, *domain, densify_pts=21)
    win = window_from_bounds(x0, y0, x1, y1, transform=tr).round_offsets().round_lengths()
    c0, r0 = max(int(win.col_off), 0), max(int(win.row_off), 0)
    c1, r1 = min(int(win.col_off + win.width), W), min(int(win.row_off + win.height), H)
    win = Window(c0, r0, c1 - c0, r1 - r0)
    pad = Window(c0 - 1, r0 - 1, c1 - c0 + 2, r1 - r0 + 2)

    def read(key, window):
        dtype, nodata = LAYERS[key]
        with rasterio.open(src_path(key)) as src:
            return src.read(1, window=window, boundless=True, fill_value=nodata).astype(dtype)

    dem = read("dem", win)
    dem_cond = read("demcond", win).astype("float64")
    fdir_p = read("fdir", pad)
    facc_p = read("facc", pad).astype("float64")
    flow_len = read("flowlen", win).astype("float64")
    zhead = read("zhead", win).astype("float64")
    landcover = read("landcover", win)
    soil_group = read("soil", win)
    nr, nc = dem.shape
    valid = dem != NODATA
    soil_group[soil_group == 0] = GROUP_CODE[p["default_soil_group"]]

    # receivers inside the window (flow leaving the window ends there)
    code = fdir_p[1:-1, 1:-1].ravel()
    rows, cols = np.divmod(np.arange(nr * nc), nc)
    has = (code < 8) & valid.ravel()
    k = np.where(has, code, 0)
    rr, cc = rows + D8_DR[k], cols + D8_DC[k]
    inside = has & (rr >= 0) & (rr < nr) & (cc >= 0) & (cc < nc)
    rec = np.where(inside, rr * nc + cc, -1).astype(np.int64)
    rec[inside & ~valid.ravel()[np.clip(rr * nc + cc, 0, nr * nc - 1)]] = -1
    diag = (D8_DR[k] != 0) & (D8_DC[k] != 0)
    dist = np.where(diag, res * 1.4142135623730951, res)

    # upstream area entering from the ring of cells just outside the window
    inflow = np.zeros(nr * nc)
    ring = np.zeros(fdir_p.shape, bool); ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
    pr, pc = np.nonzero(ring & (fdir_p < 8))
    kk = fdir_p[pr, pc]
    tr_, tc_ = pr + D8_DR[kk] - 1, pc + D8_DC[kk] - 1
    ok = (tr_ >= 0) & (tr_ < nr) & (tc_ >= 0) & (tc_ < nc)
    np.add.at(inflow, tr_[ok] * nc + tc_[ok], facc_p[pr[ok], pc[ok]])

    valid_flat = valid.ravel()
    order = topological_order(rec, valid_flat)
    grid = dict(crs=crs, transform=rasterio.windows.transform(win, tr), res=res, width=nc, height=nr)
    facc = facc_p[1:-1, 1:-1].ravel()
    return dict(focus_bbox=tuple(focus_bbox), domain=domain, grid=grid, shape=(nr, nc), dem=dem, valid=valid,
                landcover=landcover, soil_group=soil_group, soil_note=None,
                dem_cond=dem_cond, rec=rec, dist=dist, z_flat=dem_cond.ravel().copy(), valid_flat=valid_flat,
                order=order, facc_flat=facc, flowlen_flat=flow_len.ravel(), zhead_flat=zhead.ravel(),
                inflow_area_flat=inflow, source=f"prepared:{region['name']}")

def snap_to_channel(T, area_km2, lat, lon, radius_m=1000):
    grid = T["grid"]
    xs, ys = warp_transform("EPSG:4326", grid["crs"], [lon], [lat])
    r0, c0 = rowcol(grid["transform"], xs[0], ys[0])
    nr, nc = T["shape"]
    if not (0 <= r0 < nr and 0 <= c0 < nc):
        raise ValueError("The river inflow point is outside the modelled area. Place it inside the rectangle.")
    k = int(radius_m // grid["res"])
    r_lo, c_lo = max(r0 - k, 0), max(c0 - k, 0)
    win = area_km2.reshape(T["shape"])[r_lo:r0 + k + 1, c_lo:c0 + k + 1]
    dr, dc = np.unravel_index(np.argmax(win), win.shape)
    return (r_lo + dr) * nc + (c_lo + dc)

def run_storm(T, rain_mm, rain_hours, wetness="normal", inflows=(), p=DEFAULTS, log=print):
    """Runoff, river flow and flooding for one storm. inflows: list of dicts {lat, lon, flood_m3s, normal_m3s}."""
    grid, shape = T["grid"], T["shape"]
    res = float(grid.get("res", p["res_m"])); cell_m2 = res * res; cell_km2 = cell_m2 / 1e6
    rec, dist, order, z_flat, valid, valid_flat = (T[k] for k in ("rec", "dist", "order", "z_flat", "valid", "valid_flat"))
    nr, nc = shape

    log("Working out how much rain runs off")
    cn = curve_numbers(T["landcover"], T["soil_group"], wetness)
    runoff_mm = np.where(valid, scs_runoff(rain_mm, cn, p["ia_ratio"]), 0.0)

    log("Working out river flows")
    if "facc_flat" in T:
        # prepared terrain: upstream area and flow paths are known for the whole region; water from upstream
        # land outside the window is assumed to run off like the average land inside it
        area_km2, flow_len, zhead = T["facc_flat"], T["flowlen_flat"], T["zhead_flat"]
        mean_mm = float(runoff_mm[valid].mean()) if valid.any() else 0.0
        vol = accumulate_volume(order, rec, cell_km2, runoff_mm.ravel(), T["inflow_area_flat"] * mean_mm)
    else:
        area_km2, vol, flow_len, zhead = accumulate(order, rec, dist, cell_km2, runoff_mm.ravel(), z_flat)
    mean_runoff = np.where(area_km2 > 0, vol / np.maximum(area_km2, 1e-12), 0.0)
    L = np.maximum(flow_len, res)
    slope = np.maximum((zhead - z_flat) / L, 0.001)
    tc_h = 0.0195 * L ** 0.77 * slope ** -0.385 / 60.0
    q_peak = 0.208 * area_km2 * mean_runoff / (rain_hours / 2.0 + 0.6 * tc_h)
    q_peak[~valid_flat] = 0.0
    is_ch = (area_km2 >= p["channel_threshold_km2"]) & valid_flat

    inflow_info = []
    for inf in inflows or []:
        cell = snap_to_channel(T, area_km2, inf["lat"], inf["lon"])
        extra = max(float(inf["flood_m3s"]) - float(inf.get("normal_m3s", 0) or 0), 0.0)
        i, n_steps = cell, 0
        while i >= 0 and n_steps < rec.size:
            q_peak[i] += extra; is_ch[i] = True; i = rec[i]; n_steps += 1
        inflow_info.append(dict(lat=inf["lat"], lon=inf["lon"], added_m3s=extra, river_km=n_steps * res / 1000))

    log("Measuring height above the rivers")
    reach, n_reach = build_reaches(order, rec, dist, is_ch, float(p["max_reach_length_m"]))
    drain, hand = compute_hand(order, rec, is_ch, z_flat)

    log("Working out how high each river rises")
    ch_idx = np.nonzero(is_ch & (reach >= 0))[0]
    rid_ch = reach[ch_idx]
    reach_len = np.maximum(np.bincount(rid_ch, weights=dist[ch_idx], minlength=n_reach), res)
    z_hi = np.full(n_reach, -np.inf); z_lo = np.full(n_reach, np.inf)
    np.maximum.at(z_hi, rid_ch, z_flat[ch_idx]); np.minimum.at(z_lo, rid_ch, z_flat[ch_idx])
    reach_slope = np.maximum((z_hi - z_lo) / reach_len, p["min_slope"])
    q_design = np.zeros(n_reach); np.maximum.at(q_design, rid_ch, q_peak[ch_idx])

    cand = np.nonzero((drain >= 0) & (hand < p["max_stage_m"]))[0]
    cand_rid = reach[drain[cand]]; cand_hand = hand[cand]
    gy, gx = np.gradient(T["dem_cond"], res)
    bed_factor = cell_m2 * np.sqrt(1.0 + (gx ** 2 + gy ** 2).ravel()[cand])
    stages = np.arange(p["stage_step_m"], p["max_stage_m"] + 1e-9, p["stage_step_m"])
    Q_curve = np.zeros((stages.size, n_reach))
    for k, h in enumerate(stages):
        m = cand_hand < h
        V = np.bincount(cand_rid[m], weights=(h - cand_hand[m]) * cell_m2, minlength=n_reach)
        Ab = np.bincount(cand_rid[m], weights=bed_factor[m], minlength=n_reach)
        A = V / reach_len; P = Ab / reach_len
        R = np.where(P > 0, A / np.maximum(P, 1e-9), 0.0)
        Q_curve[k] = A * R ** (2 / 3) * np.sqrt(reach_slope) / p["channel_manning_n"]
    Q_curve = np.maximum.accumulate(Q_curve, axis=0)
    stage = np.zeros(n_reach)
    xq = np.concatenate([[0.0], stages])
    for j in np.nonzero(q_design > 0)[0]:
        stage[j] = np.interp(q_design[j], np.concatenate([[0.0], Q_curve[:, j]]), xq)
    ch_rec = rec[ch_idx]
    leaves = (ch_rec >= 0) & (reach[np.maximum(ch_rec, 0)] != rid_ch)
    reach_down = np.full(n_reach, -1, np.int64); reach_down[rid_ch[leaves]] = reach[ch_rec[leaves]]
    short = reach_len < p["min_reach_for_curve_m"]
    for j in range(n_reach - 1, -1, -1):
        if short[j] and reach_down[j] >= 0:
            stage[j] = stage[reach_down[j]]
    capped = int((q_design > Q_curve[-1]).sum())

    log("Mapping flood depth")
    depth = np.zeros(rec.size)
    has = drain >= 0
    depth[has] = stage[reach[drain[has]]] - hand[has]
    depth = np.clip(depth, 0, None).reshape(shape); depth[~valid] = 0
    depth[depth < p["min_depth_m"]] = 0
    labels, n_lab = ndimage.label(depth > 0)
    if n_lab:
        sizes = ndimage.sum(np.ones_like(depth), labels, index=np.arange(1, n_lab + 1))
        depth[np.isin(labels, np.nonzero(sizes < p["min_patch_cells"])[0] + 1)] = 0
    permanent_water = T["landcover"] == 80
    flood = np.where(permanent_water, 0, depth).astype("float32")

    # Clip to the area the user drew
    fx0, fy0, fx1, fy1 = transform_bounds("EPSG:4326", grid["crs"], *T["focus_bbox"])
    win = window_from_bounds(fx0, fy0, fx1, fy1, transform=grid["transform"]).round_offsets().round_lengths()
    r0, c0 = max(win.row_off, 0), max(win.col_off, 0)
    r1, c1 = min(r0 + win.height, nr), min(c0 + win.width, nc)
    fwin = Window(c0, r0, c1 - c0, r1 - r0)
    f_depth = flood[r0:r1, c0:c1]
    f_water = permanent_water[r0:r1, c0:c1]
    flooded = f_depth > 0
    summary = dict(
        rain_mm=rain_mm, rain_hours=rain_hours, wetness=wetness,
        flooded_area_km2=round(float(flooded.sum() * cell_km2), 2),
        flooded_share_pct=round(float(100 * flooded.mean()), 2),
        max_depth_m=round(float(f_depth.max()), 2),
        mean_depth_m=round(float(f_depth[flooded].mean()), 2) if flooded.any() else 0.0,
        area_over_1m_km2=round(float((f_depth > 1).sum() * cell_km2), 2),
        mean_runoff_pct=round(float(100 * runoff_mm[valid].mean() / max(rain_mm, 1e-9)), 1),
        inflows=inflow_info, capped_segments=capped, soil_note=T["soil_note"], terrain=T.get("source", "live"),
    )
    return dict(depth=f_depth, permanent_water=f_water, ground=T["dem"][r0:r1, c0:c1], crs=grid["crs"],
                transform=rasterio.windows.transform(fwin, grid["transform"]), summary=summary)

# ----------------------------------------------------------------------------- outputs for the web map

DEPTH_STOPS = [(0.1, (189, 228, 244)), (0.5, (107, 174, 214)), (1.0, (49, 130, 189)),
               (2.0, (8, 81, 156)), (3.0, (8, 48, 107))]

def depth_rgba(depth, permanent_water):
    """Colour flood depth for display. Permanent water gets a flat muted colour."""
    d = depth.astype("float64")
    xs = [s[0] for s in DEPTH_STOPS]
    rgba = np.zeros(d.shape + (4,), dtype="uint8")
    for ch in range(3):
        rgba[..., ch] = np.interp(d, xs, [s[1][ch] for s in DEPTH_STOPS]).astype("uint8")
    rgba[..., 3] = np.where(d > 0, 215, 0)
    rgba[permanent_water] = (90, 110, 130, 150)
    return rgba

def image_corners(result):
    """Lon/lat of the result image's four corners (top-left, top-right, bottom-right, bottom-left)."""
    t = result["transform"]; h, w = result["depth"].shape
    xs = [t.c, t.c + t.a * w, t.c + t.a * w, t.c]
    ys = [t.f, t.f, t.f + t.e * h, t.f + t.e * h]
    lons, lats = warp_transform(result["crs"], "EPSG:4326", xs, ys)
    return [[float(lo), float(la)] for lo, la in zip(lons, lats)]

def depth_at(result, lat, lon):
    xs, ys = warp_transform("EPSG:4326", result["crs"], [lon], [lat])
    r, c = rowcol(result["transform"], xs[0], ys[0])
    h, w = result["depth"].shape
    if 0 <= r < h and 0 <= c < w:
        return float(result["depth"][r, c]), bool(result["permanent_water"][r, c])
    return None, False

def save_depth_geotiff(result, path):
    d = result["depth"]
    with rasterio.open(path, "w", driver="GTiff", height=d.shape[0], width=d.shape[1], count=1,
                       dtype="float32", crs=result["crs"], transform=result["transform"],
                       nodata=0, compress="deflate") as dst:
        dst.write(d, 1)


def profile(result, coords, step_m=30.0, max_points=4000):
    """Ground and water level along a line drawn on the map. coords: [[lon, lat], ...]."""
    lons = [c[0] for c in coords]; lats = [c[1] for c in coords]
    xs, ys = warp_transform("EPSG:4326", result["crs"], lons, lats)
    px, py, dist = [], [], []
    total = 0.0
    for i in range(len(xs) - 1):
        seg = float(np.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]))
        n = max(int(seg / step_m), 1)
        for k in range(n):
            t = k / n
            px.append(xs[i] + t * (xs[i + 1] - xs[i])); py.append(ys[i] + t * (ys[i + 1] - ys[i]))
            dist.append(total + t * seg)
        total += seg
    px.append(xs[-1]); py.append(ys[-1]); dist.append(total)
    if len(px) > max_points:
        keep = np.linspace(0, len(px) - 1, max_points).astype(int)
        px = [px[i] for i in keep]; py = [py[i] for i in keep]; dist = [dist[i] for i in keep]
    rows, cols = rowcol(result["transform"], px, py)
    h, w = result["depth"].shape
    ground, water, wet = [], [], []
    for r, c in zip(rows, cols):
        if 0 <= r < h and 0 <= c < w and result["ground"][r, c] != NODATA:
            g = float(result["ground"][r, c]); d = float(result["depth"][r, c])
            ground.append(round(g, 2)); water.append(round(g + d, 2) if d > 0 else None)
            wet.append(bool(result["permanent_water"][r, c]))
        else:
            ground.append(None); water.append(None); wet.append(False)
    return dict(distance_m=[round(d, 1) for d in dist], ground_m=ground, water_m=water, permanent_water=wet,
                length_m=round(total, 1))
