# image_match.py - Pillow-only multi-crop matcher (no numpy/scipy/imagehash).
# Reuses the 'd' (dHash 16x16) fingerprints already in product_image_index.json.
import io, json, os
from PIL import Image

INDEX_PATH = os.path.join(os.path.dirname(__file__), "product_image_index.json")
_index = None
SIZE = 16
BITS = SIZE * SIZE

def _load():
    global _index
    if _index is None:
        with open(INDEX_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        ents = raw["entries"]
        _index = {"entries": ents, "d": [int(e["d"], 16) for e in ents]}
    return _index

def _dhash(img):
    g = img.convert("L").resize((SIZE + 1, SIZE), Image.LANCZOS)
    px = list(g.getdata())
    v = 0
    for r in range(SIZE):
        row = px[r * (SIZE + 1):(r + 1) * (SIZE + 1)]
        for c in range(SIZE):
            v = (v << 1) | (1 if row[c + 1] > row[c] else 0)
    return v

def _windows(w, h):
    seen = set()
    def add(box):
        if box[2] > box[0] and box[3] > box[1]:
            seen.add(box)
    add((0, 0, w, h))
    for ar in (3 / 4, 2 / 3, 4 / 5, 1.0):
        if w / h > ar:
            ch, cw = h, int(h * ar)
        else:
            cw, ch = w, int(w / ar)
        for s in (1.0, 0.85, 0.7, 0.55, 0.4):
            ww, hh = int(cw * s), int(ch * s)
            for fx in (0, 0.25, 0.5, 0.75, 1):
                for fy in (0, 0.25, 0.5, 0.75, 1):
                    x, y = int((w - ww) * fx), int((h - hh) * fy)
                    add((x, y, x + ww, y + hh))
    return list(seen)

def top_matches(image_bytes, k=3):
    """Best match per product over all crops. 0.0 identical, ~0.5 unrelated."""
    idx = _load()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((640, 640), Image.LANCZOS)  # speed: hash crops of a smaller copy (free-tier CPU)
    w, h = img.size
    hashes = [_dhash(img.crop(b)) for b in _windows(w, h)]
    best = {}
    for e, eh in zip(idx["entries"], idx["d"]):
        d = min(bin(eh ^ ch).count("1") for ch in hashes) / BITS
        cur = best.get(e["handle"])
        if cur is None or d < cur["distance"]:
            best[e["handle"]] = {"handle": e["handle"], "sku": e["sku"],
                                 "pos": e["pos"], "distance": round(d, 4)}
    return sorted(best.values(), key=lambda r: r["distance"])[:k]