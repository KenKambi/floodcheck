"""Model smoke test on a made-up valley, so it runs without downloading any data."""
import numpy as np
import floodmodel as fm

def synthetic_terrain(bbox=(39.60, -0.50, 39.66, -0.44)):
    p = fm.DEFAULTS
    grid = fm.make_model_grid(bbox, p["res_m"])
    shape = (grid["height"], grid["width"])
    rows, cols = np.indices(shape)
    # a V-shaped valley sloping gently from top to bottom, with a little noise
    dem = (np.abs(cols - shape[1] / 2) * 0.6 + (shape[0] - rows) * 0.05
           + np.random.default_rng(0).normal(0, 0.05, shape) + 100).astype("float32")
    valid = np.ones(shape, bool)
    dem_cond, rec, dist = fm.priority_flood_d8(dem, valid, float(p["res_m"]), 1e-6)
    order = fm.topological_order(rec, valid.ravel())
    return dict(focus_bbox=bbox, domain=bbox, grid=grid, shape=shape, dem=dem, valid=valid,
                landcover=np.full(shape, 30, "uint8"), soil_group=np.full(shape, 3, "uint8"), soil_note=None,
                dem_cond=dem_cond, rec=rec, dist=dist, z_flat=dem_cond.ravel().copy(),
                valid_flat=valid.ravel(), order=order)

def test_storm_floods_the_valley_and_more_rain_floods_more():
    T = synthetic_terrain()
    small = fm.run_storm(T, 40, 24, "normal", log=lambda m: None)["summary"]
    big = fm.run_storm(T, 250, 24, "wet", log=lambda m: None)["summary"]
    assert big["flooded_area_km2"] > 0
    assert big["flooded_area_km2"] >= small["flooded_area_km2"]
    assert big["max_depth_m"] <= fm.DEFAULTS["max_stage_m"]

def test_depth_image_and_lookup():
    T = synthetic_terrain()
    res = fm.run_storm(T, 200, 12, "wet", log=lambda m: None)
    rgba = fm.depth_rgba(res["depth"], res["permanent_water"])
    assert rgba.shape == res["depth"].shape + (4,)
    assert len(fm.image_corners(res)) == 4
    depth, water = fm.depth_at(res, -0.47, 39.63)
    assert depth is not None and depth >= 0

def test_area_checks():
    fm.validate_focus((39.5, -0.6, 39.8, -0.3))
    for bad in [(39.8, -0.6, 39.5, -0.3), (36.0, 0.0, 37.0, 1.0), (10.0, 0.0, 10.2, 0.2)]:
        try:
            fm.validate_focus(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} should be rejected")
