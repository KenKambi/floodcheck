# FloodCheck API

All endpoints are served by `backend/server.py`. Paths are relative, so the app also works behind a proxy (for example Colab).

## Model runs

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | Country extent and largest allowed area side (degrees) |
| `POST` | `/api/runs` | Start a run. Returns `{id, queue_position, cached_terrain}` |
| `GET` | `/api/runs/{id}` | Status: `queued`, `running` (with `step`, `log`), `done` or `failed` (with `error`) |
| `GET` | `/api/runs/{id}/depth.png` | Coloured flood depth image (place it with `corners` from the status) |
| `GET` | `/api/runs/{id}/depth.tif` | Flood depth GeoTIFF (metres, UTM) |
| `GET` | `/api/runs/{id}/depth?lat=&lon=` | Depth at one point: `{depth_m, permanent_water}` |
| `GET` | `/api/runs/{id}/layers/{name}.geojson` | `buildings`, `roads` or `facilities` in the flood water |
| `POST` | `/api/runs/{id}/profile` | Ground and water level along a line. Body: `{"coords": [[lon, lat], ...]}` |

**Start a run — request body**

```json
{
  "bbox": [west, south, east, north],
  "rain_mm": 150,
  "rain_hours": 24,
  "wetness": "dry | normal | wet",
  "inflows": [{"lat": 0.0, "lon": 0.0, "flood_m3s": 1500, "normal_m3s": 150}]
}
```

**Finished run — `summary` fields**

`flooded_area_km2`, `flooded_share_pct`, `max_depth_m`, `mean_depth_m`, `area_over_1m_km2`, `mean_runoff_pct`, `inflows`, `capped_segments`, `soil_note`, and `exposure` with `buildings_total`, `buildings_flooded` (`any`, `over_0.1m` … `over_2.0m`), `roads_total_km`, `roads_flooded_km`, `main_roads_flooded_km`, `facilities_total`, `facilities_flooded`, `notes`.

## Place search

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/search?q=...` | Find a village, town, school or market (or typed `lat, lon`) inside the covered area |

Returns `{"results": [{name, label, lat, lon, bbox, kind}], "attribution": "..."}`. `bbox` is `[west, south, east, north]` or `null`. Uses OpenStreetMap Nominatim, spaced to one request per second and cached.

## Weather, river flow and imagery

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/weather?bbox=w,s,e,n&date=YYYY-MM-DD&days=3` | Area rainfall, wet hours, ground wetness, daily series |
| `GET` | `/api/river-flow?lat=&lon=&date=&days=` | GloFAS peak and typical flow near a point |
| `GET` | `/api/imagery/search?source=s2\|s1&bbox=&date=` | Nearest satellite image; returns a tile URL template |
| `GET` | `/api/imagery/{id}/{z}/{x}/{y}.png` | Image tiles (`?water=true` colours radar water blue) |

Errors return JSON `{"detail": "..."}` with status 422 (bad input), 404 (unknown run), 409 (run not finished) or 503 (a data service is busy).

Interactive docs are also available while the server runs at `/docs`.
