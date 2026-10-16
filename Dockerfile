# FloodCheck web app
# Works on Render, Hugging Face Spaces and any other Docker host.
FROM python:3.11-slim

# System libraries the Python packages need but the slim image leaves out:
#   libexpat1 -> rasterio's built-in GDAL   libgomp1 -> OpenMP for numba and scipy
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# run as a normal user with ID 1000 (required by Hugging Face Spaces, safer everywhere)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLOOD_HOST=0.0.0.0 \
    FLOOD_PORT=7860 \
    FLOOD_RESULTS_DIR=/home/user/app/results \
    NUMBA_CACHE_DIR=/home/user/app/.numba_cache

WORKDIR $HOME/app
COPY --chown=user requirements.txt .
RUN pip install --user -r requirements.txt

COPY --chown=user backend ./backend
COPY --chown=user tools ./tools
COPY --chown=user run.py .
# prepared terrain (if any) - see terrain/README.md; large regions are better served from object storage
COPY --chown=user terrain ./terrain
RUN mkdir -p results .numba_cache && python tools/warmup.py

# Render sets $PORT (10000); Hugging Face uses 7860; elsewhere FLOOD_PORT decides
EXPOSE 7860 10000
CMD ["sh", "-c", "python run.py --host 0.0.0.0 --port ${PORT:-$FLOOD_PORT}"]
