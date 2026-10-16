"""Prepare FloodCheck terrain for a region once, ahead of time.

Downloads elevation, land cover and soils for the region, traces how water flows across it, and saves the
results as tiled GeoTIFFs plus an index.json. The app then reads only the small window each check needs,
which makes checks much faster and lets the app run on a small server.

Examples
    # one region (up to about 1 x 1 degree on a machine with 8-12 GB memory, e.g. Google Colab)
    python tools/precompute_terrain.py --name region-a --bbox 36.6 -1.5 37.2 -1.0 --out terrain

    # a larger area split into overlapping regions (any area a user can draw fits fully inside one region)
    python tools/precompute_terrain.py --name zone --bbox 36.0 -2.0 38.0 0.0 --tile 1.0 --out terrain

Then point the app at the folder (or upload it to object storage and use its https:// address):
    FLOOD_TERRAIN=terrain            (default when the folder sits next to run.py)
    FLOOD_TERRAIN=https://<bucket>.r2.dev/terrain
"""
import argparse, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import floodmodel as fm  # noqa: E402

def split(bbox, tile, overlap):
    """Overlapping regions covering bbox. overlap must exceed the largest area a user can draw plus buffers."""
    w, s, e, n = bbox
    if e - w <= tile and n - s <= tile:
        return [(w, s, e, n)]
    step = tile - overlap
    out = []
    y = s
    while True:
        x = w
        y1 = min(y + tile, n)
        while True:
            x1 = min(x + tile, e)
            out.append((round(max(x1 - tile, w), 4), round(max(y1 - tile, s), 4), round(x1, 4), round(y1, 4)))
            if x1 >= e:
                break
            x += step
        if y1 >= n:
            break
        y += step
    return list(dict.fromkeys(out))

def main():
    ap = argparse.ArgumentParser(description="Prepare FloodCheck terrain for a region.")
    ap.add_argument("--name", required=True, help="short region name (letters, digits, dashes)")
    ap.add_argument("--bbox", required=True, nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    ap.add_argument("--out", default=os.path.join(ROOT, "terrain"), help="output folder (default: terrain/)")
    ap.add_argument("--tile", type=float, default=0, help="split into regions of this size in degrees (e.g. 1.0)")
    ap.add_argument("--overlap", type=float, default=None,
                    help="overlap between regions in degrees (default: largest drawable area + buffers)")
    args = ap.parse_args()

    w, s, e, n = args.bbox
    if not (w < e and s < n):
        sys.exit("The bbox is empty: give WEST SOUTH EAST NORTH.")
    need = fm.MAX_FOCUS_DEG + 2 * fm.DEFAULTS["prepared_buffer_deg"] + 0.02
    if min(e - w, n - s) < need:
        print(f"Note: regions smaller than {need:.2f} degrees can't hold the largest area a user may draw. "
              f"Set FLOOD_MAX_SIDE_DEG lower on the server, or prepare a bigger region.")
    regions = [(w, s, e, n)]
    if args.tile:
        overlap = args.overlap if args.overlap is not None else need
        if args.tile <= overlap:
            sys.exit(f"--tile must be larger than the overlap ({overlap:.2f} degrees).")
        regions = split((w, s, e, n), args.tile, overlap)
    print(f"Preparing {len(regions)} region(s) into {os.path.abspath(args.out)}")
    for i, rb in enumerate(regions, 1):
        name = args.name if len(regions) == 1 else f"{args.name}-{i:02d}"
        t0 = time.time()
        print(f"\n[{i}/{len(regions)}] {name}: {rb}")
        fm.precompute_region(name, rb, args.out, log=lambda m: print("  " + m, flush=True))
        size = sum(os.path.getsize(os.path.join(args.out, name, f)) for f in os.listdir(os.path.join(args.out, name)))
        print(f"  done in {time.time() - t0:.0f} s, {size / 1e6:.0f} MB")

if __name__ == "__main__":
    main()
