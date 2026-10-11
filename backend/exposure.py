"""Exposure: which buildings, roads and facilities sit in flood water.

Data sources (no account needed):
  buildings  Overture Maps (includes OpenStreetMap, Microsoft and Google open building footprints)
  roads      Overture Maps transportation segments
  schools and health facilities  OpenStreetMap via the Overpass API, falling back to Overture places
"""
import json, time, urllib.parse, urllib.request
import numpy as np
import shapely
from rasterio.transform import rowcol
from rasterio.warp import transform as warp_transform

ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
                "residential", "living_street", "service", "track"}
MAIN_ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary"}
MAX_BUILDINGS_RETURNED = 25000
DEPTH_BANDS = [0.1, 0.5, 1.0, 2.0]

# ----------------------------------------------------------------------------- download

def _overture_table(kind, bbox):
    from overturemaps import core
    last_err = None
    for use_stac in (True, False):
        try:
            reader = core.record_batch_reader(kind, bbox=tuple(bbox), stac=use_stac)
            if reader is not None:
                return reader.read_all()
        except Exception as err:          # try the other access method
            last_err = err
    raise RuntimeError(f"Overture {kind} download failed: {last_err}")

def _geometries(table):
    col = table.column("geometry").combine_chunks()
    if hasattr(col, "storage"):
        col = col.storage
    return shapely.from_wkb(np.asarray(col.to_pylist(), dtype=object))

def _names(table):
    if "names" not in table.column_names:
        return [None] * table.num_rows
    return [(n or {}).get("primary") if isinstance(n, dict) else None for n in table.column("names").to_pylist()]

def fetch_buildings(bbox, log=print):
    log("Downloading building footprints")
    t = _overture_table("building", bbox)
    geoms = _geometries(t)
    keep = ~shapely.is_empty(geoms)
    return dict(geoms=geoms[keep])

def fetch_roads(bbox, log=print):
    log("Downloading roads")
    t = _overture_table("segment", bbox)
    subtype = t.column("subtype").to_pylist() if "subtype" in t.column_names else ["road"] * t.num_rows
    klass = t.column("class").to_pylist() if "class" in t.column_names else ["unclassified"] * t.num_rows
    names = _names(t)
    geoms = _geometries(t)
    keep = [i for i in range(t.num_rows) if subtype[i] == "road" and (klass[i] or "unclassified") in ROAD_CLASSES]
    return dict(geoms=geoms[keep], cls=[klass[i] or "unclassified" for i in keep], names=[names[i] for i in keep])

def _facility_kind(tags_or_category):
    s = " ".join(str(v) for v in tags_or_category).lower()
    if any(k in s for k in ("hospital", "clinic", "health", "medical", "doctor", "dispensary")):
        return "health"
    if any(k in s for k in ("school", "college", "university", "kindergarten")):
        return "school"
    return None

def fetch_facilities_osm(bbox, timeout=90):
    w, s, e, n = bbox
    q = f"""[out:json][timeout:{timeout}];
(
  nwr["amenity"~"^(school|college|university|kindergarten|hospital|clinic|doctors)$"]({s},{w},{n},{e});
  nwr["healthcare"]({s},{w},{n},{e});
);
out center tags;"""
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                 data=urllib.parse.urlencode({"data": q}).encode(),
                                 headers={"User-Agent": "atlas-floodcheck/0.1"})
    with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
        data = json.load(resp)
    out = []
    for el in data.get("elements", []):
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        tags = el.get("tags", {})
        kind = _facility_kind([tags.get("amenity", ""), tags.get("healthcare", "")])
        if lat is None or kind is None:
            continue
        out.append(dict(lat=lat, lon=lon, kind=kind, name=tags.get("name") or ("Health facility" if kind == "health" else "School"),
                        source="OpenStreetMap"))
    return out

def fetch_facilities_overture(bbox):
    t = _overture_table("place", bbox)
    cats = t.column("categories").to_pylist() if "categories" in t.column_names else [None] * t.num_rows
    names = _names(t)
    geoms = _geometries(t)
    out = []
    for i in range(t.num_rows):
        c = cats[i] or {}
        kind = _facility_kind([c.get("primary") or ""] + list(c.get("alternate") or []))
        if kind is None:
            continue
        p = geoms[i]
        out.append(dict(lat=shapely.get_y(p), lon=shapely.get_x(p), kind=kind,
                        name=names[i] or ("Health facility" if kind == "health" else "School"), source="Overture"))
    return out

def fetch_facilities(bbox, log=print):
    log("Downloading schools and health facilities")
    try:
        return fetch_facilities_osm(bbox)
    except Exception as err:
        log(f"OpenStreetMap facilities unavailable ({str(err)[:60]}); trying Overture")
        return fetch_facilities_overture(bbox)

def load_exposure(bbox, log=print):
    """Download everything for an area. Each part fails independently, so a missing source doesn't stop the run."""
    data, notes = {}, []
    for key, fn in (("buildings", fetch_buildings), ("roads", fetch_roads), ("facilities", fetch_facilities)):
        try:
            data[key] = fn(bbox, log=log)
        except Exception as err:
            data[key] = None
            notes.append(f"{key.capitalize()} couldn't be downloaded, so they're left out of this run.")
            log(f"{key} unavailable: {str(err)[:100]}")
    data["notes"] = notes
    return data

# ----------------------------------------------------------------------------- analysis

def sample_depth(result, lons, lats):
    lons = np.asarray(lons, dtype="float64"); lats = np.asarray(lats, dtype="float64")
    if lons.size == 0:
        return np.zeros(0)
    xs, ys = warp_transform("EPSG:4326", result["crs"], lons.tolist(), lats.tolist())
    rows, cols = rowcol(result["transform"], xs, ys)
    rows, cols = np.asarray(rows), np.asarray(cols)
    h, w = result["depth"].shape
    inside = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
    out = np.zeros(lons.size)
    out[inside] = result["depth"][rows[inside], cols[inside]]
    return out

def _feature(geom, props, ndigits=6):
    g = json.loads(shapely.to_geojson(shapely.set_precision(geom, 10 ** -ndigits)))
    return {"type": "Feature", "properties": props, "geometry": g}

def analyse(result, exposure, bbox):
    """Intersect exposure data with a flood result. Returns summary counts and GeoJSON layers."""
    area = shapely.box(*bbox)
    summary, layers = {}, {}

    b = exposure.get("buildings")
    if b is not None:
        geoms = b["geoms"]
        cent = shapely.centroid(geoms)
        inside = shapely.contains(area, cent)
        geoms, cent = geoms[inside], cent[inside]
        depth = sample_depth(result, shapely.get_x(cent), shapely.get_y(cent))
        flooded = depth > 0
        summary["buildings_total"] = int(geoms.size)
        summary["buildings_flooded"] = {f"over_{d}m": int((depth > d).sum()) for d in DEPTH_BANDS}
        summary["buildings_flooded"]["any"] = int(flooded.sum())
        idx = np.nonzero(flooded)[0]
        idx = idx[np.argsort(-depth[idx])][:MAX_BUILDINGS_RETURNED]
        # polygons for close-up views, plus centre points so buildings stay visible when zoomed out
        feats = [_feature(geoms[i], {"depth": round(float(depth[i]), 2)}) for i in idx]
        cx, cy = shapely.get_x(cent), shapely.get_y(cent)
        feats += [{"type": "Feature", "properties": {"depth": round(float(depth[i]), 2)},
                   "geometry": {"type": "Point", "coordinates": [round(float(cx[i]), 6), round(float(cy[i]), 6)]}} for i in idx]
        layers["buildings"] = {"type": "FeatureCollection", "features": feats}
        summary["buildings_truncated"] = bool(flooded.sum() > MAX_BUILDINGS_RETURNED)

    r = exposure.get("roads")
    if r is not None and len(r["geoms"]):
        geoms = shapely.intersection(r["geoms"], area)
        feats, flooded_km, main_km, total_km = [], 0.0, 0.0, 0.0
        for g, cls, name in zip(geoms, r["cls"], r["names"]):
            if shapely.is_empty(g):
                continue
            # sample every ~30 m (0.00027°) along the road
            length_deg = shapely.length(g)
            n = max(int(length_deg / 0.00027), 2)
            pts = shapely.line_interpolate_point(g, np.linspace(0, 1, n), normalized=True)
            d = sample_depth(result, shapely.get_x(pts), shapely.get_y(pts))
            km = length_deg * 111.0          # close enough near the equator for a summary figure
            total_km += km
            wet = d > 0.3                    # 30 cm of water makes most roads impassable to cars
            frac = float(wet.mean())
            if frac > 0:
                flooded_km += km * frac
                if cls in MAIN_ROADS:
                    main_km += km * frac
                # return only the flooded stretches, as runs of consecutive wet sample points
                xs, ys = shapely.get_x(pts), shapely.get_y(pts)
                edges = np.diff(np.concatenate([[0], wet.astype(int), [0]]))
                for a, b_ in zip(np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]):
                    a0, b0 = max(a - 1, 0), min(b_, len(xs) - 1)      # extend half a step each side
                    if b0 - a0 < 1:
                        continue
                    seg = shapely.LineString(np.column_stack([xs[a0:b0 + 1], ys[a0:b0 + 1]]))
                    feats.append(_feature(seg, {"class": cls, "name": name or "",
                                                "max_depth": round(float(d[a:b_].max()), 2)}))
        summary["roads_total_km"] = round(total_km, 1)
        summary["roads_flooded_km"] = round(flooded_km, 1)
        summary["main_roads_flooded_km"] = round(main_km, 1)
        layers["roads"] = {"type": "FeatureCollection", "features": feats}

    f = exposure.get("facilities")
    if f is not None:
        f = [x for x in f if bbox[0] <= x["lon"] <= bbox[2] and bbox[1] <= x["lat"] <= bbox[3]]
        depth = sample_depth(result, [x["lon"] for x in f], [x["lat"] for x in f])
        feats = []
        for x, dd in zip(f, depth):
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [x["lon"], x["lat"]]},
                          "properties": {"name": x["name"], "kind": x["kind"], "depth": round(float(dd), 2),
                                         "flooded": bool(dd > 0), "source": x["source"]}})
        summary["facilities_total"] = {"school": sum(x["kind"] == "school" for x in f),
                                       "health": sum(x["kind"] == "health" for x in f)}
        flooded = [ft["properties"] | {"lon": ft["geometry"]["coordinates"][0], "lat": ft["geometry"]["coordinates"][1]}
                   for ft in feats if ft["properties"]["flooded"]]
        summary["facilities_flooded"] = sorted(flooded, key=lambda p: -p["depth"])
        layers["facilities"] = {"type": "FeatureCollection", "features": feats}

    summary["notes"] = exposure.get("notes", [])
    return summary, layers
