# Hosting FloodCheck on Render

## 1. Create the service

1. Push the project to GitHub (including the `Dockerfile`).
2. In Render choose **New → Web Service**, connect the repository, and pick **Docker** as the runtime.
3. Instance type: see step 3 below.
4. Under **Advanced → Health Check Path** enter `/api/config`.

The Dockerfile installs the system libraries rasterio needs, compiles the model engine during the build,
and listens on the port Render provides (`$PORT`).

## 2. Prepare terrain first

Follow `docs/prepared-terrain.md` for the regions you want to serve. Either commit the `terrain/` folder
(small regions) or upload it to object storage and add the environment variable
`FLOOD_TERRAIN=https://<bucket address>/terrain`.

## 3. Choose the instance and settings

| Instance memory | Environment variables | What works |
|---|---|---|
| 512 MB (free) | `FLOOD_LIVE_TERRAIN=0`, `FLOOD_TERRAIN_CACHE=1`, `FLOOD_MAX_SIDE_DEG=0.1` | Small areas (about 11 km) inside prepared regions, away from dense city centres. The free instance sleeps after 15 minutes without visitors |
| 1 GB | `FLOOD_LIVE_TERRAIN=0`, `FLOOD_TERRAIN_CACHE=1`, `FLOOD_MAX_SIDE_DEG=0.2` | Areas up to about 22 km inside prepared regions |
| 2 GB | `FLOOD_LIVE_TERRAIN=0` | Full-size areas inside prepared regions, including most cities |
| 4 GB | none required | Everything, including building terrain live for areas outside prepared regions |

## 4. Check it

- Open the `onrender.com` link: the map and the teal region outlines should appear.
- Run a check inside a prepared region. The progress list should start with "Reading prepared terrain".
- If a check fails with "Lost contact with the FloodCheck server", open **Logs** and **Events** in Render.
  A line about running out of memory means the area was too big for the instance: lower
  `FLOOD_MAX_SIDE_DEG` or choose a larger instance.
