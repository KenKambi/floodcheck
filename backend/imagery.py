"""Satellite images for any date, served as map tiles.

  Sentinel-2  10 m colour photos (2017 onwards), from the Element 84 Earth Search catalogue on AWS
  Sentinel-1  10 m radar (2014 onwards), terrain-corrected, from Microsoft Planetary Computer.
              Radar sees through cloud; calm water shows up black.
"""
import datetime as dt
import hashlib, io, threading, time
from collections import OrderedDict
import numpy as np
from PIL import Image

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
PLANETARY_COMPUTER = "https://planetarycomputer.microsoft.com/api/stac/v1"
S2_WINDOW_DAYS = 15
S1_WINDOW_DAYS = 12
WATER_DB = -18.0              # radar backscatter below this is treated as open water

SELECTIONS = {}
TILE_CACHE = OrderedDict()
TILE_CACHE_MAX = 3000
_lock = threading.Lock()
EMPTY_TILE = None

def _empty_tile():
    global EMPTY_TILE
    if EMPTY_TILE is None:
        buf = io.BytesIO(); Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(buf, "PNG"); EMPTY_TILE = buf.getvalue()
    return EMPTY_TILE

def _search(url, collection, bbox, start, end, sign=False, query=None):
    import pystac_client
    last = None
    for attempt in range(4):
        try:
            client = pystac_client.Client.open(url)
            kw = dict(collections=[collection], bbox=bbox, datetime=f"{start.isoformat()}/{end.isoformat()}", max_items=200)
            if query:
                kw["query"] = query
            return list(client.search(**kw).items())
        except Exception as err:
            last = err
            if "rate limit" in str(err).lower():
                time.sleep(5 * (attempt + 1)); continue
            time.sleep(2)
    raise RuntimeError(f"The satellite image catalogue didn't respond: {str(last)[:150]}")

def _pick_date(items, target, score):
    by_day = {}
    for it in items:
        by_day.setdefault(it.datetime.date(), []).append(it)
    best = min(by_day, key=lambda d: score(d, by_day[d], target))
    return best, by_day[best]

def find_images(source, bbox, date_str):
    target = dt.date.fromisoformat(date_str)
    if source == "s2":
        items = _search(EARTH_SEARCH, "sentinel-2-l2a", bbox, target - dt.timedelta(days=S2_WINDOW_DAYS),
                        target + dt.timedelta(days=S2_WINDOW_DAYS), query={"eo:cloud_cover": {"lt": 90}})
        items = [it for it in items if "visual" in it.assets]
        if not items:
            raise ValueError(f"No Sentinel-2 photo within {S2_WINDOW_DAYS} days of that date. Try another date.")
        # prefer clear images, then closeness to the chosen date
        score = lambda d, its, t: np.mean([it.properties.get("eo:cloud_cover", 100) for it in its]) + 2.0 * abs((d - t).days)
        day, chosen = _pick_date(items, target, score)
        hrefs = [it.assets["visual"].href for it in chosen]
        info = dict(cloud_pct=round(float(np.mean([it.properties.get("eo:cloud_cover", 0) for it in chosen])), 0))
    elif source == "s1":
        items = _search(PLANETARY_COMPUTER, "sentinel-1-rtc", bbox, target - dt.timedelta(days=S1_WINDOW_DAYS),
                        target + dt.timedelta(days=S1_WINDOW_DAYS))
        items = [it for it in items if "vv" in it.assets]
        if not items:
            raise ValueError(f"No Sentinel-1 radar image within {S1_WINDOW_DAYS} days of that date. Try another date.")
        score = lambda d, its, t: abs((d - t).days)
        day, chosen = _pick_date(items, target, score)
        hrefs = [it.assets["vv"].href for it in chosen]
        info = dict(orbit=chosen[0].properties.get("sat:orbit_state"))
    else:
        raise ValueError("Unknown image source.")
    sel_id = hashlib.sha1(f"{source}|{day}|{'|'.join(sorted(hrefs))}".encode()).hexdigest()[:12]
    SELECTIONS[sel_id] = dict(source=source, hrefs=hrefs, date=day.isoformat())
    return dict(id=sel_id, source=source, date=day.isoformat(), days_from_target=(day - target).days,
                scenes=len(hrefs), tiles=f"api/imagery/{sel_id}/{{z}}/{{x}}/{{y}}.png", **info)

def _read_tile(href, x, y, z, **kw):
    from rio_tiler.io import Reader
    with Reader(href) as src:
        return src.tile(x, y, z, tilesize=256, **kw)

def render_tile(sel_id, z, x, y, water=False):
    key = (sel_id, z, x, y, water)
    with _lock:
        if key in TILE_CACHE:
            TILE_CACHE.move_to_end(key); return TILE_CACHE[key]
    sel = SELECTIONS.get(sel_id)
    if sel is None:
        raise KeyError(sel_id)
    from rio_tiler.mosaic import mosaic_reader
    from rio_tiler.errors import TileOutsideBounds, EmptyMosaicError
    hrefs = sel["hrefs"]
    if sel["source"] == "s1":
        import planetary_computer
        hrefs = [planetary_computer.sign(h) for h in hrefs]
    try:
        img, _ = mosaic_reader(hrefs, _read_tile, x, y, z, allowed_exceptions=(TileOutsideBounds,))
    except (EmptyMosaicError, TileOutsideBounds):
        return _empty_tile()
    mask = np.asarray(img.mask) > 0
    if sel["source"] == "s2":
        rgb = np.clip(np.asarray(img.data[:3]), 0, 255).astype("uint8").transpose(1, 2, 0)
        rgba = np.dstack([rgb, np.where(mask, 255, 0).astype("uint8")])
    else:
        vv = np.asarray(img.data[0], dtype="float32")
        db = 10 * np.log10(np.clip(vv, 1e-5, None))
        g = (np.clip((db + 25.0) / 25.0, 0, 1) * 255).astype("uint8")
        rgba = np.dstack([g, g, g, np.where(mask, 255, 0).astype("uint8")])
        if water:
            wm = mask & (db < WATER_DB)
            rgba[wm] = (0, 170, 255, 255)
    buf = io.BytesIO(); Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=False)
    out = buf.getvalue()
    with _lock:
        TILE_CACHE[key] = out
        while len(TILE_CACHE) > TILE_CACHE_MAX:
            TILE_CACHE.popitem(last=False)
    return out
