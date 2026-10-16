"""Prepared terrain: a window read from a prepared region gives the same floods as building the whole region live."""
import os, sys
import numpy as np
import pytest
import floodmodel as fm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

REGION = (39.50, -0.60, 39.70, -0.40)
FOCUS = (39.58, -0.52, 39.62, -0.48)

def region_inputs():
    grid = fm.make_model_grid(REGION, 30)
    shape = (grid["height"], grid["width"])
    rows, cols = np.indices(shape)
    rng = np.random.default_rng(1)
    # a valley draining from the top of the region to the bottom, with side slopes and some noise
    dem = (np.abs(cols - shape[1] / 2) * 0.4 + (shape[0] - rows) * 0.08 + rng.normal(0, 0.3, shape) + 500).astype("float32")
    valid = np.ones(shape, bool)
    landcover = np.full(shape, 30, "uint8"); landcover[:, : shape[1] // 3] = 10
    soil = np.full(shape, 3, "uint8")
    return grid, dem, valid, landcover, soil, None

@pytest.fixture(scope="module")
def store(tmp_path_factory):
    out = tmp_path_factory.mktemp("terrain")
    inputs = region_inputs()
    fm.precompute_region("test-region", REGION, str(out), inputs=inputs, log=lambda m: None)
    old = fm.TERRAIN_STORE
    fm.TERRAIN_STORE = str(out)
    fm.terrain_index(reload=True)
    yield out, inputs
    fm.TERRAIN_STORE = old
    fm.terrain_index(reload=True)

def live_terrain_from(inputs, focus):
    grid, dem, valid, landcover, soil, _ = inputs
    dem_cond, rec, dist = fm.priority_flood_d8(dem, valid, 30.0, 1e-6)
    order = fm.topological_order(rec, valid.ravel())
    return dict(focus_bbox=focus, domain=REGION, grid=grid, shape=dem.shape, dem=dem, valid=valid, landcover=landcover,
                soil_group=soil, soil_note=None, dem_cond=dem_cond, rec=rec, dist=dist, z_flat=dem_cond.ravel().copy(),
                valid_flat=valid.ravel(), order=order)

def test_region_is_registered(store):
    out, _ = store
    assert [r["name"] for r in fm.prepared_regions()] == ["test-region"]
    for key in fm.LAYERS:
        assert (out / "test-region" / f"{key}.tif").exists()

def test_prepared_window_matches_live_region(store):
    _, inputs = store
    T = fm.load_terrain(FOCUS, log=lambda m: None)
    assert T["source"] == "prepared:test-region"
    assert T["shape"][0] < inputs[1].shape[0] / 2           # only a small window was read
    assert T["inflow_area_flat"].sum() > 1.0                  # water arrives from upstream outside the window
    prepared = fm.run_storm(T, 120, 12, "wet", log=lambda m: None)["summary"]
    live = fm.run_storm(live_terrain_from(inputs, FOCUS), 120, 12, "wet", log=lambda m: None)["summary"]
    assert prepared["flooded_area_km2"] > 0
    assert prepared["flooded_area_km2"] == pytest.approx(live["flooded_area_km2"], rel=0.25)
    assert prepared["max_depth_m"] == pytest.approx(live["max_depth_m"], rel=0.25, abs=0.3)

def test_area_outside_prepared_regions(store, monkeypatch):
    near_edge = (39.50, -0.60, 39.54, -0.56)                  # no room for the buffer inside the region
    assert fm.find_region(near_edge) is None
    monkeypatch.setattr(fm, "LIVE_TERRAIN", False)
    with pytest.raises(ValueError, match="isn't prepared"):
        fm.load_terrain(near_edge, log=lambda m: None)

def test_split_regions_cover_any_drawable_area():
    from precompute_terrain import split
    bbox, tile = (36.0, -2.0, 38.0, 0.0), 1.0
    overlap = fm.MAX_FOCUS_DEG + 2 * fm.DEFAULTS["prepared_buffer_deg"] + 0.02
    regions = split(bbox, tile, overlap)
    rng = np.random.default_rng(0)
    for _ in range(300):
        w = rng.uniform(bbox[0], bbox[2] - fm.MAX_FOCUS_DEG - 0.06) + 0.03
        s = rng.uniform(bbox[1], bbox[3] - fm.MAX_FOCUS_DEG - 0.06) + 0.03
        focus = (w, s, w + fm.MAX_FOCUS_DEG, s + fm.MAX_FOCUS_DEG)
        b = fm.DEFAULTS["prepared_buffer_deg"]
        assert any(r[0] <= focus[0] - b and r[1] <= focus[1] - b and r[2] >= focus[2] + b and r[3] >= focus[3] + b
                   for r in regions), focus

def test_pdf_report(store):
    from types import SimpleNamespace
    import report
    T = fm.load_terrain(FOCUS, log=lambda m: None)
    res = fm.run_storm(T, 150, 24, "wet", log=lambda m: None)
    s = res["summary"]
    s["exposure"] = dict(buildings_total=100, buildings_flooded={"over_0.1m": 12, "over_0.5m": 5, "over_1.0m": 2, "over_2.0m": 0, "any": 12},
                         roads_total_km=10.0, roads_flooded_km=1.2, main_roads_flooded_km=0.0, facilities_total={"school": 1},
                         facilities_flooded=[dict(name="School A", kind="school", depth=0.6, lon=39.60, lat=-0.50)], notes=[])
    run = dict(result=res, summary=s, request=SimpleNamespace(bbox=list(FOCUS)), status="done")
    pdf = report.build_pdf(run, os.path.join(ROOT, "backend", "static", "assets", "logo-full.png"))
    assert pdf.startswith(b"%PDF") and len(pdf) > 20000
    assert report.risk_level(s) == 2                     # a school in the water makes it serious
