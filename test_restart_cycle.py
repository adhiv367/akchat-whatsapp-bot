"""
test_restart_cycle.py - regression test for the repeated-salwar bug.

When a customer has already seen every eligible salwar, the restart must cycle through the
LEAST RECENTLY shown products. It must not hand back the same first five every time.

OFFLINE: reuses the stubs from test_no_repeat.py (no Groq, no database, no Shopify).
Run from this folder:  python test_restart_cycle.py      Exit code 0 = passed, 1 = failed.
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import test_no_repeat as T  # noqa: E402

sa, ps = T.sa, T.ps
RESTART = "Here are some more designs for you"


def eligible_salwar():
    out = []
    for t in ps.load_products():
        text = t["text"]
        sku = T.sku_of(text)
        if (sku and sku not in out and sa._is_available(text)
                and not sa._is_test_product(text)
                and sa._type_of(text) == "salwar suit set"):
            out.append(sku)
    return out


def t_restart_cycles_least_recently_shown_first():
    c = "restart_cycle"
    elig = eligible_salwar()
    n = sa.MAX_CARDS
    assert len(elig) > 2 * n, "need more than %d eligible salwars, found %d" % (2 * n, len(elig))
    for t in ps.load_products():  # this customer has already seen the whole catalog
        T.SHOWN.rows.setdefault(c, []).append(T.sku_of(t["text"]))
    T.script("show me salwar", category_hint="salwar", search_query="salwar")

    covered = []
    for i in range(len(elig) // n):
        out, skus = T.ask("show me salwar", c)
        reply = (out or {}).get("reply") or ""
        assert reply.startswith(RESTART), "request %d: wrong reply %r" % (i + 1, reply)
        assert len(skus) == n, "request %d: expected %d cards, got %r" % (i + 1, n, skus)
        assert set(skus) <= set(elig), "request %d: non-salwar or ineligible SKUs %r" % (
            i + 1, sorted(set(skus) - set(elig)))
        repeat = sorted(set(skus) & set(covered))
        assert not repeat, "request %d repeated %s although unseen salwars remained in this cycle" % (i + 1, repeat)
        covered += skus

    left = set(elig) - set(covered)
    out, skus = T.ask("show me salwar", c)
    assert left <= set(skus), "after the cycle the leftovers %s were not shown first (got %r)" % (
        sorted(left), skus)


TESTS = [t_restart_cycles_least_recently_shown_first]

if __name__ == "__main__":
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print("PASS  " + fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL  %s : %s" % (fn.__name__, e))
        except Exception as e:
            failed += 1
            print("ERROR %s : %r" % (fn.__name__, e))
            traceback.print_exc()
    print("\n%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    sys.exit(1 if failed else 0)
