"""Start the FloodCheck web app.

    python run.py                    # http://127.0.0.1:8000
    python run.py --host 0.0.0.0     # reachable from other devices on the network
    python run.py --port 8080

Needs Python 3.10, 3.11 or 3.12.

Runs as a single process on purpose: model runs, the job queue and cached terrain
are kept in memory, so more than one worker would split them apart.
"""
# Keep this file free of newer syntax so an old Python shows the message below instead of a SyntaxError.
import sys

if sys.version_info < (3, 10):
    sys.stderr.write(
        "\nFloodCheck needs Python 3.10, 3.11 or 3.12, but this is Python %d.%d.\n"
        "On Windows, check which versions you have with:   py -0p\n"
        "then start FloodCheck with a newer one, for example:   py -3.11 run.py\n"
        "(or install Python 3.11 from python.org and tick 'Add python.exe to PATH').\n\n"
        % sys.version_info[:2])
    sys.exit(1)

import argparse, os

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")


def main():
    ap = argparse.ArgumentParser(description="Start the FloodCheck web app.")
    ap.add_argument("--host", default=os.environ.get("FLOOD_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT") or os.environ.get("FLOOD_PORT", "8000")))  # PORT: set by hosts such as Render
    ap.add_argument("--reload", action="store_true", help="Restart when backend code changes (development only)")
    args = ap.parse_args()

    os.environ.setdefault("FLOOD_RESULTS_DIR", os.path.join(ROOT, "results"))
    os.environ.setdefault("NUMBA_CACHE_DIR", os.path.join(ROOT, ".numba_cache"))
    sys.path.insert(0, BACKEND)

    try:
        import uvicorn
    except ImportError:
        sys.stderr.write("\nThe required packages are not installed for this Python.\n"
                         "Run:   python -m pip install -r requirements.txt\n\n")
        sys.exit(1)

    shown_host = "localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host
    print("FloodCheck is starting on http://{}:{}".format(shown_host, args.port))
    print("The first model run in a new area takes a few minutes while it downloads data.")
    print("Press Ctrl+C to stop.")
    uvicorn.run("server:app", host=args.host, port=args.port, workers=1,
                reload=args.reload, reload_dirs=[BACKEND] if args.reload else None,
                app_dir=BACKEND, log_level="warning")


if __name__ == "__main__":
    main()
