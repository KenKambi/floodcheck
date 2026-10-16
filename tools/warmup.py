"""Compile the flood model's Numba engine ahead of time (run during the Docker build).

Without this, the first flood check after every server start spends extra time and memory compiling.
The compiled code is cached in NUMBA_CACHE_DIR.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import numpy as np  # noqa: E402
import floodmodel as fm  # noqa: E402

bbox = (39.60, -0.50, 39.62, -0.48)
grid = fm.make_model_grid(bbox, 30)
shape = (grid["height"], grid["width"])
rows, cols = np.indices(shape)
dem = (np.abs(cols - shape[1] / 2) * 0.6 + (shape[0] - rows) * 0.05 + 100).astype("float32")
valid = np.ones(shape, bool)
dem_cond, rec, dist = fm.priority_flood_d8(dem, valid, 30.0, 1e-6)
order = fm.topological_order(rec, valid.ravel())
fm.accumulate(order, rec, dist, 0.0009, np.ones(rec.size), dem_cond.ravel())
fm.accumulate_volume(order, rec, 0.0009, np.ones(rec.size), np.zeros(rec.size))
is_ch = np.zeros(rec.size, np.bool_); is_ch[order[-1]] = True
fm.build_reaches(order, rec, dist, is_ch, 2000.0)
fm.compute_hand(order, rec, is_ch, dem_cond.ravel())
print("Flood model engine compiled and cached.")
