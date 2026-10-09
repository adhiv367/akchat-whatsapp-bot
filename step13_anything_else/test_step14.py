import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_no_repeat as T
sa = T.sa
orig = sa._fresh_fill
try:
    sa._fresh_fill = lambda *a, **k: None          # force the failure
    c = "s14"
    for t in T.ps.load_products():
        T.SHOWN.rows.setdefault(c, []).append(T.sku_of(t["text"]))
    T.script("show me kurthi", category_hint="kurthi", search_query="kurthi")
    out, skus = T.ask("show me kurthi", c)
    assert skus and len(skus) <= 5, "expected 1-5 cards, got %r" % skus
    assert "already seen" not in out["reply"].lower(), out["reply"]
    assert out["reply"].startswith("You've now seen all our designs")
    print("PASS step14: no dead end when _fresh_fill fails, cards:", skus)
finally:
    sa._fresh_fill = orig