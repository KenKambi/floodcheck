# FloodCheck web app — Docker image for Render (also works on any Docker host)
FROM python:3.11-slim

# System libraries the Python packages need but the slim image leaves out:
#   libexpat1  -> rasterio's built-in GDAL (fixes "libexpat.so.1: cannot open shared object file")
#   libgomp1   -> OpenMP, used by numba and scipy
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLOOD_RESULTS_DIR=/app/results \
    NUMBA_CACHE_DIR=/app/.numba_cache

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend ./backend
COPY run.py .
RUN mkdir -p results .numba_cache

# Render tells the app which port to use through $PORT (10000 by default).
# The app must listen on 0.0.0.0, not localhost, or Render can't reach it.
EXPOSE 10000
CMD ["sh", "-c", "python run.py --host 0.0.0.0 --port ${PORT:-10000}"]
