# Prepared terrain

## What it is

Every flood check needs the same land information for its area: elevation, land cover, soils, and the
flow network (which way water flows from every 30 m cell, how much land drains through it, and how long
the flow path above it is). None of this depends on the rain. Building it during each check is what made
checks slow and memory-hungry.

Prepared terrain builds it **once per region** and stores it as tiled GeoTIFFs:

| File | Contents |
|---|---|
| `dem.tif` | Elevation (Copernicus GLO-30) |
| `demcond.tif` | Elevation with pits filled so water can always flow out |
| `fdir.tif` | Flow direction of each cell (D8 code 0–7, 255 = none) |
| `facc.tif` | Upstream drainage area in km² |
| `flowlen.tif` | Length of the longest flow path above each cell (m) |
| `zhead.tif` | Elevation at the top of that flow path (m) |
| `landcover.tif` | ESA WorldCover class |
| `soil.tif` | Hydrologic soil group (1–4 = A–D) |
| `meta.json` | Region details |

`index.json` in the top folder lists all regions. A check reads only the window it needs (the user's area
plus about 3 km), so it is fast and light, and upstream water from anywhere in the region is included.

## 1. Choose regions

- One region can be up to about **1 × 1 degree** (about 110 km square) on a machine with 8–12 GB memory,
  such as free Google Colab. Larger areas are split into overlapping regions automatically with `--tile 1.0`.
- A region must be bigger than the largest area users may draw plus 0.06° of margin. With the default
  largest area (0.40°) that means at least about 0.48° per side. Lower `FLOOD_MAX_SIDE_DEG` to allow smaller regions.
- Areas near the edge of a region can't be checked from that region; overlapping regions solve this.
- Each 1 × 1 degree region takes roughly 10–25 minutes and about 100–200 MB of files.

## 2. Prepare them

**In Colab (easiest):** open `notebooks/precompute_terrain_colab.ipynb`, set the region name and box in the
first settings cell, and choose Runtime → Run all. It saves a zip to your Google Drive.

**On any computer with enough memory:**

```bash
pip install -r requirements.txt
python tools/precompute_terrain.py --name region-a --bbox WEST SOUTH EAST NORTH --out terrain
# a bigger area, split into overlapping 1-degree regions:
python tools/precompute_terrain.py --name zone --bbox WEST SOUTH EAST NORTH --tile 1.0 --out terrain
```

Running it again with the same name replaces that region. New names are added to `index.json`.

## 3. Make it available to the app

Choose one:

- **In the project (small regions):** put the `terrain/` folder next to `run.py` and commit it. The Docker
  build copies it into the image. GitHub refuses files over 100 MB, so this suits regions up to about half a degree.
- **Object storage (recommended):** upload the folder to a public bucket, for example Cloudflare R2 (10 GB
  free), Backblaze B2 or AWS S3, and set `FLOOD_TERRAIN=https://<bucket address>/terrain` on the server.
  The app reads only the parts of each file it needs, over HTTPS.

The map shows prepared regions as teal dashed outlines.

## 4. Server settings

| Situation | Settings |
|---|---|
| Small server (512 MB–1 GB) | `FLOOD_LIVE_TERRAIN=0`, `FLOOD_TERRAIN_CACHE=1`, `FLOOD_MAX_SIDE_DEG=0.1` (512 MB) or `0.2` (1 GB) |
| 2 GB or more | `FLOOD_LIVE_TERRAIN=0` or `1`, defaults otherwise |
| Any size, only prepared areas wanted | `FLOOD_LIVE_TERRAIN=0` |

With `FLOOD_LIVE_TERRAIN=0`, areas outside the prepared regions are refused with a clear message instead of
risking a crash. Buildings, roads and facilities are still downloaded during each check, so dense city areas
use more memory than rural ones.

## How accurate is it?

The tests compare a prepared window against building the whole region live: flooded area and maximum depth
match within about 1%. One approximation: rain falling on land upstream of the window is assumed to run off
like the average land inside the window. For very large rivers, use the "Big river coming in" step as before.
