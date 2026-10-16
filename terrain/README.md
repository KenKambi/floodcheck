# Prepared terrain

This folder holds terrain prepared ahead of time with `tools/precompute_terrain.py` (or the
`notebooks/precompute_terrain_colab.ipynb` notebook): one sub-folder per region and an `index.json`.

When it contains an `index.json`, FloodCheck reads terrain from here instead of downloading and
processing it during each check. To keep large files out of git, upload the folder to object storage
(for example Cloudflare R2 or AWS S3) and set `FLOOD_TERRAIN` to its https:// address instead.
