"""Place search: turns a village, town, school or market name into a map location.

Uses OpenStreetMap Nominatim (free, no account), limited to the country extent the model covers.
Nominatim's usage policy asks for at most one request per second and a real User-Agent, so
requests go through the server (not the browser), are spaced out, and repeated searches are cached.
Typing coordinates ("-1.2921, 36.8219") skips the online search.
"""
import json, re, threading, time, urllib.error, urllib.parse, urllib.request
from collections import OrderedDict

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "atlas-floodcheck/1.0 (community flood screening tool)"
MIN_INTERVAL_S = 1.1
CACHE_MAX = 500

_cache = OrderedDict()
_lock = threading.Lock()
_last_call = [0.0]
_COORDS = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

def _inside(lat, lon, bbox):
    w, s, e, n = bbox
    return w <= lon <= e and s <= lat <= n

def parse_coordinates(q):
    """'lat, lon' typed by the user, or None."""
    m = _COORDS.match(q or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None

def search(q, bbox, limit=6):
    """Places matching q inside bbox (west, south, east, north), best match first."""
    q = (q or "").strip()
    if len(q) < 2:
        raise ValueError("Type at least two letters to search.")
    if len(q) > 120:
        raise ValueError("The search is too long. Use a shorter place name.")

    coords = parse_coordinates(q)
    if coords:
        lat, lon = coords
        if not _inside(lat, lon, bbox):
            raise ValueError("Those coordinates are outside the area the model covers.")
        return [dict(name=f"{lat:.5f}, {lon:.5f}", label="Coordinates you typed", lat=lat, lon=lon,
                     bbox=None, kind="coordinates")]

    key = (q.lower(), tuple(bbox), limit)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]

    w, s, e, n = bbox
    params = dict(q=q, format="jsonv2", limit=limit, viewbox=f"{w},{n},{e},{s}", bounded=1, addressdetails=0)
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    with _lock:                                   # one request at a time, spaced out
        wait = MIN_INTERVAL_S - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                rows = json.load(resp)
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"The place search service is busy ({err.code}). Try again in a moment.")
        except (urllib.error.URLError, TimeoutError) as err:
            raise RuntimeError("Can't reach the place search service. Check the internet connection.")
        finally:
            _last_call[0] = time.time()

    out = []
    for r in rows:
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        bb = r.get("boundingbox")                 # [south, north, west, east] as strings
        box = [float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])] if bb and len(bb) == 4 else None
        label = r.get("display_name", "")
        out.append(dict(name=r.get("name") or label.split(",")[0], label=label, lat=lat, lon=lon,
                        bbox=box, kind=(r.get("type") or r.get("category") or "place").replace("_", " ")))
    with _lock:
        _cache[key] = out
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return out
