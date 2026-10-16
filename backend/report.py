"""PDF flood report for a finished run: map, risk level, key numbers, what is in the water, advice.

Built with reportlab. The background map comes from Esri World Street Map, requested in the run's own
projection so it lines up exactly with the flood depth grid; if that fails, a hillshade of the ground is used.
"""
import io, time, urllib.parse, urllib.request
import numpy as np
from PIL import Image
from rasterio.warp import transform as warp_transform
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import floodmodel as fm

NAVY, BLUE, TEAL, GREEN, MAGENTA, GREY = (colors.HexColor(c) for c in
                                          ("#0B1650", "#1E86B8", "#4BB8C2", "#3E9A44", "#C4307A", "#4A5578"))
LEVEL_COLOURS = [colors.HexColor(c) for c in ("#3E9A44", "#D9921A", "#C4307A", "#7E1046")]
ESRI_EXPORT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/export"

# Keep in step with VERDICTS / riskLevel() in static/js/floodcheck.js
VERDICTS = [
    ("Low risk", "This rain is not expected to flood much land in the area.",
     ["Keep drains, culverts and furrows clear", "Keep watching the weather forecast"]),
    ("Some flooding likely", "Low land near rivers and streams may go under water.",
     ["Do not walk or drive through moving water", "Move animals, seed and stored food off low ground",
      "Check on neighbours who live near the river"]),
    ("Serious flooding likely", "Homes, roads or important places are in the flood water in this check.",
     ["Agree where your family would go on higher ground", "Keep documents, medicines and a phone charger ready to carry",
      "Do not cross flooded roads or bridges", "Tell local leaders and share the warning"]),
    ("Severe flooding likely", "Many homes or important places are in deep water in this check.",
     ["Be ready to leave early, before the water rises", "Move to higher ground when told to",
      "Never walk, swim or drive through flood water", "Follow instructions from local authorities"]),
]

def risk_level(s):
    e = s.get("exposure") or {}; b = e.get("buildings_flooded") or {}; fac = e.get("facilities_flooded") or []
    share = s.get("flooded_share_pct") or 0
    if not (s.get("flooded_area_km2") or 0) > 0:
        return 0
    lv = 0
    if share >= 1 or b.get("any", 0) > 0 or e.get("roads_flooded_km", 0) >= 0.5:
        lv = 1
    if share >= 5 or b.get("over_0.5m", 0) >= 20 or fac or e.get("main_roads_flooded_km", 0) >= 1:
        lv = 2
    if share >= 15 or b.get("over_1.0m", 0) >= 100 or any(f.get("depth", 0) > 1 for f in fac):
        lv = 3
    return lv

def _num(v, d=1):
    if isinstance(v, float):
        return f"{v:,.{d}f}".rstrip("0").rstrip(".") if d else f"{v:,.0f}"
    return f"{v:,}"

# ----------------------------------------------------------------------------- map image

def _bounds(result):
    t = result["transform"]; h, w = result["depth"].shape
    return t.c, t.f + t.e * h, t.c + t.a * w, t.f          # x0, y0, x1, y1 in the run's projection

def _basemap(result, size):
    """Street map for the run's extent, in its own projection. None if it can't be fetched."""
    x0, y0, x1, y1 = _bounds(result)
    epsg = result["crs"].to_epsg()
    params = dict(bbox=f"{x0},{y0},{x1},{y1}", bboxSR=epsg, imageSR=epsg, size=f"{size[0]},{size[1]}",
                  format="png", transparent="false", f="image", dpi=110)
    try:
        req = urllib.request.Request(f"{ESRI_EXPORT}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": "atlas-floodcheck/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            img = Image.open(io.BytesIO(r.read())).convert("RGB")
        return img.resize(size) if img.size != size else img
    except Exception as err:
        print(f"Report basemap unavailable: {err}")
        return None

def _hillshade(ground, res, size):
    g = np.where(ground == fm.NODATA, np.nan, ground.astype("float64"))
    g = np.where(np.isnan(g), np.nanmean(g) if np.isfinite(np.nanmean(g)) else 0, g)
    gy, gx = np.gradient(g, res)
    slope = np.arctan(np.hypot(gx, gy)); aspect = np.arctan2(-gx, gy)
    az, alt = np.radians(315), np.radians(45)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    hs = np.clip(150 + 90 * hs, 0, 255).astype("uint8")
    return Image.fromarray(np.dstack([hs, hs, hs + 0])).resize(size)

def map_image(result, facilities=(), max_px=1500):
    h, w = result["depth"].shape
    scale = max_px / max(h, w)
    size = (max(int(w * scale), 1), max(int(h * scale), 1))
    base = _basemap(result, size)
    credit = "Background: Esri, HERE, Garmin, © OpenStreetMap contributors"
    if base is None:
        base = _hillshade(result["ground"], abs(result["transform"].a), size)
        credit = "Background: shaded relief from Copernicus DEM"
    flood = Image.fromarray(fm.depth_rgba(result["depth"], result["permanent_water"]), "RGBA").resize(size, Image.NEAREST)
    img = base.convert("RGBA"); img.alpha_composite(flood)
    if facilities:
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        t = result["transform"]
        xs, ys = warp_transform("EPSG:4326", result["crs"], [f["lon"] for f in facilities], [f["lat"] for f in facilities])
        for f, x, y in zip(facilities, xs, ys):
            px, py = (x - t.c) / t.a * scale, (y - t.f) / t.e * scale
            r = 14
            col = (62, 154, 68, 255) if f.get("kind") == "health" else (30, 134, 184, 255)
            d.ellipse([px - r, py - r, px + r, py + r], fill=col, outline=(255, 255, 255, 255), width=3)
    return img.convert("RGB"), credit, scale

# ----------------------------------------------------------------------------- PDF

class _Doc:
    def __init__(self, buf, logo_path):
        self.c = canvas.Canvas(buf, pagesize=A4, pageCompression=1)
        self.W, self.H = A4
        self.M = 16 * mm
        self.logo = logo_path
        self.page = 0
        self.y = 0

    def new_page(self, title):
        if self.page:
            self.footer(); self.c.showPage()
        self.page += 1
        c, W, H, M = self.c, self.W, self.H, self.M
        try:
            c.drawImage(ImageReader(self.logo), M, H - M - 13 * mm, width=36 * mm, height=13 * mm, mask="auto",
                        preserveAspectRatio=True, anchor="w")
        except Exception:
            pass
        c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 17)
        c.drawRightString(W - M, H - M - 6 * mm, title)
        c.setFont("Helvetica", 9); c.setFillColor(GREY)
        c.drawRightString(W - M, H - M - 11 * mm, time.strftime("Generated %d %b %Y, %H:%M UTC", time.gmtime()))
        x = M
        for col in (TEAL, BLUE, NAVY, GREEN, MAGENTA):          # the five figures from the logo
            c.setFillColor(col); c.rect(x, H - M - 16 * mm, (W - 2 * M) / 5, 1.6 * mm, stroke=0, fill=1)
            x += (W - 2 * M) / 5
        self.y = H - M - 22 * mm

    def footer(self):
        c = self.c
        c.setFont("Helvetica", 8); c.setFillColor(GREY)
        c.drawString(self.M, 10 * mm, "FloodCheck by Atlas Geosystems · rapid flood screening from global satellite data")
        c.drawRightString(self.W - self.M, 10 * mm, f"Page {self.page}")

    def heading(self, text):
        self.y -= 3 * mm
        self.c.setFillColor(NAVY); self.c.setFont("Helvetica-Bold", 12.5)
        self.c.drawString(self.M, self.y, text)
        self.c.setStrokeColor(TEAL); self.c.setLineWidth(1)
        self.c.line(self.M, self.y - 2 * mm, self.W - self.M, self.y - 2 * mm)
        self.y -= 8 * mm

    def para(self, text, size=10, colour=colors.black, bold=False, indent=0, gap=1.5 * mm, width=None):
        c = self.c
        font = "Helvetica-Bold" if bold else "Helvetica"
        maxw = (width or (self.W - 2 * self.M)) - indent
        words, line = text.split(), ""
        c.setFont(font, size); c.setFillColor(colour)
        for wd in words:
            test = (line + " " + wd).strip()
            if c.stringWidth(test, font, size) > maxw and line:
                c.drawString(self.M + indent, self.y, line); self.y -= size * 1.35; line = wd
            else:
                line = test
        if line:
            c.drawString(self.M + indent, self.y, line); self.y -= size * 1.35
        self.y -= gap

    def bullets(self, items, colour=NAVY, size=10):
        for it in items:
            self.c.setFillColor(colour); self.c.circle(self.M + 2 * mm, self.y + 1.2 * mm, 1.1 * mm, stroke=0, fill=1)
            self.para(it, size=size, indent=6 * mm, gap=0.5 * mm)
        self.y -= 1.5 * mm

    def table(self, rows, widths, header=True, size=9.5):
        c, x0 = self.c, self.M
        rh = size * 1.9
        for i, row in enumerate(rows):
            if header and i == 0:
                c.setFillColor(NAVY); c.rect(x0, self.y - rh + 3, sum(widths), rh, stroke=0, fill=1)
                c.setFillColor(colors.white); c.setFont("Helvetica-Bold", size)
            else:
                if i % 2 == 0:
                    c.setFillColor(colors.HexColor("#F3F6F9")); c.rect(x0, self.y - rh + 3, sum(widths), rh, stroke=0, fill=1)
                c.setFillColor(colors.black); c.setFont("Helvetica", size)
            x = x0
            for cell, w in zip(row, widths):
                txt = str(cell)
                while c.stringWidth(txt, "Helvetica", size) > w - 8 and len(txt) > 4:
                    txt = txt[:-2]
                if txt != str(cell):
                    txt = txt[:-1] + "…"
                c.drawString(x + 4, self.y - rh / 2 + 1.5, txt)
                x += w
            self.y -= rh
        self.y -= 3 * mm

def build_pdf(run, logo_path):
    """Return the PDF report for a finished run as bytes."""
    result, s, req = run["result"], run["summary"], run["request"]
    e = s.get("exposure") or {}
    lv = risk_level(s); title, text, todo = VERDICTS[lv]
    w, so, ea, n = req.bbox
    mid = (so + n) / 2
    wk, hk = (ea - w) * 111.32 * np.cos(np.radians(mid)), (n - so) * 110.57

    buf = io.BytesIO()
    d = _Doc(buf, logo_path); c = d.c
    c.setTitle("FloodCheck flood report"); c.setAuthor("Atlas Geosystems FloodCheck")
    d.new_page("Flood report")

    # area name and box
    name = getattr(req, "place_name", None) or "Area marked on the map"
    d.para(name, size=15, colour=NAVY, bold=True, gap=1 * mm)
    d.para(f"Area: {w:.4f}, {so:.4f} to {ea:.4f}, {n:.4f} (west, south, east, north; WGS84)  ·  "
           f"about {wk:.0f} × {hk:.0f} km", size=9, colour=GREY, gap=2 * mm)
    d.para(f"Storm: {_num(float(s['rain_mm']), 0)} mm of rain over {_num(float(s['rain_hours']))} hours on "
           f"{s['wetness']} ground" + (f", plus {len(s.get('inflows') or [])} river inflow(s)" if s.get("inflows") else ""),
           size=9, colour=GREY, gap=3 * mm)

    # verdict box
    box_h = 20 * mm
    c.setFillColor(LEVEL_COLOURS[lv]); c.roundRect(d.M, d.y - box_h + 4 * mm, d.W - 2 * d.M, box_h, 3 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white); c.setFont("Helvetica", 9)
    c.drawString(d.M + 5 * mm, d.y - 1 * mm, "FLOOD RISK FOR THIS RAIN")
    c.setFont("Helvetica-Bold", 18); c.drawString(d.M + 5 * mm, d.y - 9 * mm, title)
    c.setFont("Helvetica", 9.5); c.drawString(d.M + 5 * mm, d.y - 14.5 * mm, text)
    for i, lab in enumerate(("Low", "Some", "Serious", "Severe")):         # 4-step meter
        bx = d.W - d.M - 4 * 21 * mm - 4 * mm + i * 21 * mm
        c.setFillColor(colors.white if i <= lv else colors.Color(1, 1, 1, alpha=0.35))
        c.roundRect(bx, d.y - 6 * mm, 19 * mm, 2.2 * mm, 1 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white); c.setFont("Helvetica", 7.5); c.drawString(bx, d.y - 10.5 * mm, lab)
    d.y -= box_h + 2 * mm

    # map
    fac = (e.get("facilities_flooded") or [])[:40]
    img, credit, _ = map_image(result, fac)
    max_w, max_h = d.W - 2 * d.M, 112 * mm
    sc = min(max_w / img.width, max_h / img.height)
    mw, mh = img.width * sc, img.height * sc
    mx = d.M + (max_w - mw) / 2
    my = d.y - mh
    c.drawImage(ImageReader(img), mx, my, width=mw, height=mh)
    c.setStrokeColor(MAGENTA); c.setLineWidth(1.5); c.setDash(4, 3); c.rect(mx, my, mw, mh, stroke=1, fill=0); c.setDash()
    # north arrow
    ax, ay = mx + mw - 9 * mm, my + mh - 14 * mm
    c.setFillColor(colors.white); c.circle(ax, ay + 3 * mm, 5.5 * mm, stroke=0, fill=1)
    c.setFillColor(NAVY); p = c.beginPath(); p.moveTo(ax, ay + 7 * mm); p.lineTo(ax - 2.5 * mm, ay); p.lineTo(ax + 2.5 * mm, ay); p.close()
    c.drawPath(p, stroke=0, fill=1); c.setFont("Helvetica-Bold", 8); c.drawCentredString(ax, ay - 3.2 * mm, "N")
    # scale bar
    width_m = result["depth"].shape[1] * abs(result["transform"].a)
    target = width_m / 4
    nice = max(v for v in (100, 200, 500, 1000, 2000, 5000, 10000, 20000) if v <= max(target, 100))
    bar = nice / width_m * mw
    c.setFillColor(colors.white); c.rect(mx + 3 * mm, my + 3 * mm, bar + 6 * mm, 9 * mm, stroke=0, fill=1)
    c.setFillColor(NAVY); c.rect(mx + 6 * mm, my + 6 * mm, bar, 1.6 * mm, stroke=0, fill=1)
    c.setFont("Helvetica", 7.5); c.drawString(mx + 6 * mm, my + 8.6 * mm, f"{nice / 1000:g} km" if nice >= 1000 else f"{nice} m")
    d.y = my - 4 * mm
    # legend + credit
    c.setFont("Helvetica-Bold", 8.5); c.setFillColor(NAVY); c.drawString(d.M, d.y, "Water depth")
    stops = [(0.1, "#BDE4F4"), (0.5, "#6BAED6"), (1.0, "#3182BD"), (2.0, "#08519C"), (3.0, "#08306B")]
    lx = d.M + 22 * mm
    for i, (v, col) in enumerate(stops):
        c.setFillColor(colors.HexColor(col)); c.rect(lx + i * 15 * mm, d.y - 1 * mm, 15 * mm, 3.5 * mm, stroke=0, fill=1)
        c.setFillColor(GREY); c.setFont("Helvetica", 7); c.drawString(lx + i * 15 * mm, d.y - 4.5 * mm, f"{v:g} m" + ("+" if i == 4 else ""))
    c.setFillColor(colors.HexColor("#5A6E82")); c.rect(lx + 80 * mm, d.y - 1 * mm, 6 * mm, 3.5 * mm, stroke=0, fill=1)
    c.setFillColor(GREY); c.setFont("Helvetica", 7.5); c.drawString(lx + 88 * mm, d.y, "River or lake (always wet)")
    if fac:
        c.setFillColor(BLUE); c.circle(lx + 128 * mm, d.y + 0.7 * mm, 1.5 * mm, stroke=0, fill=1)
        c.setFillColor(GREY); c.drawString(lx + 131 * mm, d.y, "School")
        c.setFillColor(GREEN); c.circle(lx + 143 * mm, d.y + 0.7 * mm, 1.5 * mm, stroke=0, fill=1)
        c.setFillColor(GREY); c.drawString(lx + 146 * mm, d.y, "Health")
    c.setFont("Helvetica", 6.5); c.drawRightString(d.W - d.M, d.y - 8 * mm, credit)
    d.y -= 14 * mm

    # key numbers
    facts = [("Under water", f"{_num(float(s['flooded_area_km2']))} km²", f"{_num(float(s['flooded_share_pct']))}% of the area"),
             ("Deepest water", f"{_num(float(s['max_depth_m']))} m", ""),
             ("Average depth", f"{_num(float(s['mean_depth_m']))} m", ""),
             ("Deeper than 1 m", f"{_num(float(s['area_over_1m_km2']))} km²", "")]
    bw = (d.W - 2 * d.M - 3 * 4 * mm) / 4
    for i, (lab, val, sub) in enumerate(facts):
        bx = d.M + i * (bw + 4 * mm)
        c.setFillColor(colors.HexColor("#F3F6F9")); c.rect(bx, d.y - 16 * mm, bw, 18 * mm, stroke=0, fill=1)
        c.setFillColor((BLUE, TEAL, NAVY, GREEN)[i]); c.rect(bx, d.y - 16 * mm, 1.4 * mm, 18 * mm, stroke=0, fill=1)
        c.setFillColor(GREY); c.setFont("Helvetica", 8); c.drawString(bx + 4 * mm, d.y - 3 * mm, lab)
        c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 14); c.drawString(bx + 4 * mm, d.y - 9.5 * mm, val)
        if sub:
            c.setFillColor(GREY); c.setFont("Helvetica", 7.5); c.drawString(bx + 4 * mm, d.y - 13.8 * mm, sub)
    d.y -= 22 * mm

    # ---- page 2
    d.new_page("Flood report")
    d.heading("What to do")
    d.para(text, size=10)
    d.bullets(todo, colour=LEVEL_COLOURS[lv])
    d.para("This is a simple guide from this model run. Always follow official weather and flood warnings.",
           size=8.5, colour=GREY, gap=3 * mm)

    d.heading("Homes, roads and services in the water")
    b = e.get("buildings_flooded")
    if b is not None:
        d.para(f"{_num(b.get('any', 0))} of {_num(e.get('buildings_total', 0))} buildings have flood water around them.", size=10)
        rows = [["Water depth around the building", "Buildings"]] + [
            [f"More than {k.split('_')[1].rstrip('m')} m", _num(b.get(k, 0))] for k in ("over_0.1m", "over_0.5m", "over_1.0m", "over_2.0m") if k in b]
        d.table(rows, [110 * mm, 40 * mm])
    if "roads_flooded_km" in e:
        d.para(f"Roads under water: {_num(float(e['roads_flooded_km']))} km of {_num(float(e.get('roads_total_km', 0)), 0)} km"
               f" (main roads: {_num(float(e.get('main_roads_flooded_km', 0)))} km).", size=10, gap=3 * mm)
    allfac = e.get("facilities_flooded") or []
    tot = e.get("facilities_total") or {}
    if tot or allfac:
        d.para(f"Schools and health facilities in the water: {len(allfac)}"
               + (f" (of {sum(tot.values())} in the area)" if tot else ""), size=10, gap=2 * mm)
    if allfac:
        rows = [["Name", "Type", "Water depth"]] + [[f.get("name") or "Unnamed", f.get("kind", "").capitalize(), f"{f['depth']:.1f} m"]
                                                    for f in allfac[:15]]
        d.table(rows, [100 * mm, 30 * mm, 30 * mm])
        if len(allfac) > 15:
            d.para(f"… and {len(allfac) - 15} more.", size=9, colour=GREY)
    if not e:
        d.para("Buildings, roads and facilities were not available for this run.", size=10, colour=GREY)

    d.heading("Inputs and method")
    rows = [["Input", "Value"],
            ["Total rain", f"{_num(float(s['rain_mm']), 0)} mm"], ["Falling over", f"{_num(float(s['rain_hours']))} hours"],
            ["Ground before the rain", s["wetness"].capitalize()],
            ["Rain that ran off", f"{_num(float(s['mean_runoff_pct']))}%"],
            ["Terrain", "Prepared ahead of time" if str(s.get("terrain", "")).startswith("prepared") else "Built for this check"]]
    for inf in s.get("inflows") or []:
        rows.append(["River inflow", f"{_num(float(inf['added_m3s']), 0)} m³/s added, followed for {_num(float(inf['river_km']))} km"])
    d.table(rows, [70 * mm, 100 * mm])
    notes = list(e.get("notes") or []) + ([s["soil_note"]] if s.get("soil_note") else [])
    if s.get("capped_segments"):
        notes.append(f"{s['capped_segments']} river stretches carried more water than the model's highest level; depths there may be underestimated.")
    if notes:
        d.bullets(notes, colour=GREY, size=9)
    d.para("Method: rainfall runoff from land cover and soils (SCS curve number), river flow from upstream area and "
           "travel time, and flood depth from height above the nearest river (HAND), on a 30 m grid.", size=8.5, colour=GREY)
    d.para("Data: Copernicus DEM GLO-30, ESA WorldCover 2021, ISRIC SoilGrids, Overture Maps and OpenStreetMap "
           "(buildings, roads, facilities), Open-Meteo / GloFAS (rain and river flow).", size=8.5, colour=GREY)
    d.para("A quick estimate from global satellite data. It shows where flooding is likely, not an engineering flood study, "
           "and it can miss walls, culverts and drains. Check the map against what the community has seen in past floods.",
           size=8.5, colour=GREY)
    d.footer(); c.save()
    return buf.getvalue()
