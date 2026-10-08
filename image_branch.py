# image_branch.py - NEW file. Decides whether a customer image is confidently
# one of our products. Any error or doubt -> None (existing flow continues).
import base64
from image_match import top_matches

MAX_DISTANCE = 0.10   # best match must be at least this close
MIN_LEAD = 0.05       # and this far ahead of the second-best product

def identify_product(image_b64):
    try:
        raw = base64.b64decode(image_b64)
        res = top_matches(raw, k=2)
        if not res:
            return None
        best = res[0]
        lead = (res[1]["distance"] - best["distance"]) if len(res) > 1 else 1.0
        print(f"[IMAGE] best={best['sku']} dist={best['distance']} lead={round(lead, 4)}")
        if best["distance"] <= MAX_DISTANCE and lead >= MIN_LEAD:
            return best
        print("[IMAGE] not confident -> falling through")
        return None
    except Exception as e:
        print(f"[IMAGE] matcher error, falling through: {e}")
        return None
