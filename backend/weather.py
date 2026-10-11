"""Real rainfall, ground wetness and river flow for any date, from Open-Meteo (free, no account).

  rainfall + wetness  ERA5 reanalysis for past dates (1940 onwards); weather forecast for the last
                      three months and the next 16 days
  river flow          GloFAS (Copernicus Global Flood Awareness System), 1984 onwards plus forecast
"""
import datetime as dt
import json, time, urllib.parse, urllib.request
import numpy as np

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
TZ = "Africa/Nairobi"
FORECAST_PAST_LIMIT_DAYS = 90
FORECAST_AHEAD_DAYS = 16

# SCS antecedent moisture thresholds, growing season (5-day rain before the storm, mm)
AMC_DRY_BELOW_MM = 36.0
AMC_WET_ABOVE_MM = 53.0

def _get_json(url, params, retries=3):
    q = urllib.parse.urlencode(params, safe=",")
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "atlas-floodcheck/0.2"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "ignore")
            try:
                reason = json.loads(body).get("reason", body)
            except Exception:
                reason = body
            if err.code == 429 or err.code >= 500:
                last = RuntimeError(f"The weather service is busy ({err.code}). Try again in a minute.")
                time.sleep(3 * (attempt + 1)); continue
            raise ValueError(f"The weather service rejected the request: {reason[:200]}")
        except urllib.error.URLError as err:
            last = RuntimeError(f"Can't reach the weather service: {err.reason}")
            time.sleep(3 * (attempt + 1))
    raise last

def _parse_date(s):
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise ValueError("Enter the date as YYYY-MM-DD.")

def _grid_points(bbox, n=3):
    w, s, e, nn = bbox
    lats = np.linspace(s, nn, n); lons = np.linspace(w, e, n)
    pts = [(float(la), float(lo)) for la in lats for lo in lons]
    return [p[0] for p in pts], [p[1] for p in pts]

def rainfall_for_event(bbox, date_str, days):
    """Area-average rainfall for the `days` days ending on `date_str`, plus the 5 days before for wetness."""
    end = _parse_date(date_str)
    if not 1 <= days <= 10:
        raise ValueError("Choose between 1 and 10 days of rain.")
    start = end - dt.timedelta(days=days - 1)
    ante_start = start - dt.timedelta(days=5)
    today = dt.date.today()
    if end > today + dt.timedelta(days=FORECAST_AHEAD_DAYS - 1):
        raise ValueError(f"Forecasts only reach {FORECAST_AHEAD_DAYS} days ahead. Pick an earlier date.")
    if ante_start < dt.date(1940, 1, 1):
        raise ValueError("Rainfall records start in 1940. Pick a later date.")

    lats, lons = _grid_points(bbox)
    common = dict(latitude=",".join(f"{v:.4f}" for v in lats), longitude=",".join(f"{v:.4f}" for v in lons),
                  hourly="precipitation", timezone=TZ)
    if ante_start >= today - dt.timedelta(days=FORECAST_PAST_LIMIT_DAYS):
        past = (today - ante_start).days + 1
        data = _get_json(FORECAST_URL, dict(common, past_days=min(max(past, 1), 92), forecast_days=FORECAST_AHEAD_DAYS))
        source = "Open-Meteo weather forecast and recent analysis"
    else:
        data = _get_json(ARCHIVE_URL, dict(common, start_date=ante_start.isoformat(), end_date=end.isoformat()))
        source = "ERA5 reanalysis (via Open-Meteo)"
    data = data if isinstance(data, list) else [data]

    times = np.array(data[0]["hourly"]["time"], dtype="datetime64[m]")
    vals = np.array([[np.nan if v is None else v for v in d["hourly"]["precipitation"]] for d in data], dtype="float64")
    mean = np.nanmean(vals, axis=0)
    day = times.astype("datetime64[D]")
    in_storm = (day >= np.datetime64(start)) & (day <= np.datetime64(end))
    in_ante = (day >= np.datetime64(ante_start)) & (day < np.datetime64(start))
    if not in_storm.any() or np.isnan(mean[in_storm]).all():
        raise ValueError("No rainfall data is available for those dates yet. Recent days can take up to a week to appear.")
    storm = np.nan_to_num(mean[in_storm])
    rain_mm = float(storm.sum())
    wet_hours = int((storm > 0.1).sum())
    ante_mm = float(np.nansum(mean[in_ante]))
    wetness = "dry" if ante_mm < AMC_DRY_BELOW_MM else "wet" if ante_mm > AMC_WET_ABOVE_MM else "normal"

    daily = []
    for k in range((end - ante_start).days + 1):
        d = ante_start + dt.timedelta(days=k)
        m = day == np.datetime64(d)
        daily.append(dict(date=d.isoformat(), rain_mm=round(float(np.nansum(mean[m])), 1), in_storm=bool(d >= start)))
    return dict(rain_mm=round(rain_mm, 1), rain_hours=max(wet_hours, 1), wetness=wetness,
                antecedent_mm=round(ante_mm, 1), start=start.isoformat(), end=end.isoformat(),
                is_forecast=bool(end > today), source=source, daily=daily,
                peak_hour_mm=round(float(storm.max()), 1))

def river_flow_for_event(lat, lon, date_str, days, search_deg=0.05):
    """GloFAS river flow near a point: peak around the event and the typical flow over the year before.

    GloFAS works on a ~5 km grid, so the nearest grid cell may sit on a smaller stream. We check the
    cells around the point and use the one carrying the most water, which is almost always the main river."""
    end = _parse_date(date_str)
    start = end - dt.timedelta(days=days - 1)
    win_start, win_end = start - dt.timedelta(days=1), end + dt.timedelta(days=4)   # flood waves arrive late
    base_start = start - dt.timedelta(days=365)
    if base_start < dt.date(1984, 1, 1):
        raise ValueError("River flow records start in 1984. Pick a later date.")
    offs = [-search_deg, 0.0, search_deg]
    lats = [lat + a for a in offs for _ in offs]; lons = [lon + b for _ in offs for b in offs]
    data = _get_json(FLOOD_URL, dict(latitude=",".join(f"{v:.4f}" for v in lats),
                                     longitude=",".join(f"{v:.4f}" for v in lons), daily="river_discharge",
                                     start_date=base_start.isoformat(), end_date=win_end.isoformat()))
    data = data if isinstance(data, list) else [data]
    best, best_mean = None, -1.0
    for d in data:
        q = np.array([np.nan if v is None else v for v in d["daily"]["river_discharge"]], dtype="float64")
        if np.isfinite(q).any() and np.nanmean(q) > best_mean:
            best, best_mean = (d, q), float(np.nanmean(q))
    if best is None:
        raise ValueError("No river flow data was found near this point. Place the inflow on a larger river.")
    d, q = best
    t = np.array(d["daily"]["time"], dtype="datetime64[D]")
    base = q[(t >= np.datetime64(base_start)) & (t < np.datetime64(start))]
    win = (t >= np.datetime64(win_start)) & (t <= np.datetime64(win_end))
    if not np.isfinite(q[win]).any():
        raise ValueError("No river flow data for those dates yet.")
    qwin = np.where(np.isfinite(q[win]), q[win], -1)
    k = int(np.argmax(qwin))
    return dict(flood_m3s=round(float(qwin[k]), 0), peak_date=str(t[win][k]),
                normal_m3s=round(float(np.nanmedian(base)), 0) if np.isfinite(base).any() else 0.0,
                grid_lat=d.get("latitude"), grid_lon=d.get("longitude"),
                series=[dict(date=str(tt), q=None if not np.isfinite(v) else round(float(v), 1))
                        for tt, v in zip(t[win], q[win])],
                source="GloFAS v4 (via Open-Meteo)")
