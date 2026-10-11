# FloodCheck web app
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLOOD_HOST=0.0.0.0 \
    FLOOD_PORT=8000 \
    FLOOD_RESULTS_DIR=/app/results \
    NUMBA_CACHE_DIR=/app/.numba_cache

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend ./backend
COPY run.py .
RUN mkdir -p results .numba_cache

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"FLOOD_PORT\"]}/api/config', timeout=4)"
CMD ["python", "run.py"]
