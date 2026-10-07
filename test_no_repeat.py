"""
test_no_repeat.py - local test for the NO_REPEAT change (already-shown products must not repeat).

Put this file in  E:\\ak chat new version\\phase2-test  (next to ai_bridge_phase2.py) and run:
    python test_no_repeat.py

OFFLINE: it never calls Groq, never writes to the database, never contacts Shopify/WhatsApp.
  - Groq "understand" and "generate_reply" are replaced by scripted fakes
  - get_recent_skus / remember_skus are replaced by an in-memory store
  - it uses your REAL catalog through tools.product_search (read-only)
Because the AI step is scripted, this tests the selection / exclusion / exhausted-reply logic.
Whether the real Groq sets refers_to_shown correctly is checked separately (live_refers_check.py).

Exit code 0 = all passed, 1 = something failed.
"""
import inspect
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
HSA = os.path.join(ROOT, "human_sales_agent")
for _p in (ROOT, HSA):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sales_agent as sa  # noqa: E402

agent, ps, prod = sa.agent, sa.product_search, sa.prod

# Present only after the patch; getattr keeps the PRE-patch run readable (tests fail, not crash).
ALREADY_SEEN = getattr(sa, "ALREADY_SEEN_REPLY", "<ALREADY_SEEN_REPLY missing>")
ALREADY_SEEN_NEW = getattr(sa, "ALREADY_SEEN_NEW_REPLY", "<ALREADY_SEEN_NEW_REPLY missing>")
WIDE_POOL = getattr(sa, "WIDE_POOL", 5)

# What the real bridge function looks like (captured BEFORE we replace it with the fake store).
_ORIG_GET = prod.get_recent_skus


# ---------------------------------------------------------------- offline stubs
class Shown:
    """In-memory stand-in for coexistence.shown_products."""
    def __init__(self):
        self.rows = {}

    def get(self, customer_id, workspace_id=None, wa_number=None, limit=15):
        return list(reversed(self.rows.get(customer_id, [])))[:limit]

    def remember(self, customer_id, skus, workspace_id=None, wa_number=None):
        self.rows.setdefault(customer_id, []).extend(skus)


SHOWN = Shown()
prod.get_recent_skus = SHOWN.get
prod.remember_skus = SHOWN.remember
prod.log_customer_interest = lambda *a, **k: None
prod._pending_followup_note = lambda *a, **k: None
ps.semantic_search_products = lambda *a, **k: []
ps.get_top_product_images = lambda *a, **k: []
agent.policy.search_policy_faq = lambda *a, **k: []
agent.generate_reply = lambda message, understanding, verified_data, history: ("Here are some options \U0001F60A", True)

BASE = dict(intent="PRODUCT_DISCOVERY", referenced_product_hint=None, category_hint=None, color_hint=None,
            budget_hint=None, size_hint=None, search_query=None, wants_more=False, collection=None,
            handoff_required=False, handoff_reason=None)
SCRIPT = {}


def script(message, **kw):
    d = dict(BASE)
    d.update(kw)
    SCRIPT[message] = d


agent.understand = lambda message, customer_id, workspace_id=None, wa_number=None: (dict(SCRIPT[message]), [])

# record how gather_verified_data is called
GATHER_CALLS = []
_orig_gather = agent.gather_verified_data


def _spy_gather(*a, **k):
    GATHER_CALLS.append(dict(k))
    return _orig_gather(*a, **k)


agent.gather_verified_data = _spy_gather


# ---------------------------------------------------------------- helpers
def n(s):
    return sa._norm_sku(s)


def sku_of(text):
    return n(sa._text_sku(text))


def ask(message, customer):
    out = sa.run(message, customer, "WS1", "919999999999")
    cards = (out or {}).get("cards") or []
    return out, [n(c["sku"]) for c in cards]


def browse_until_exhausted(message, customer, limit=60):
    """Repeat the same browsing message. Returns (list of per-call SKU lists, final reply text)."""
    batches, last_reply = [], None
    for _ in range(limit):
        out, skus = ask(message, customer)
        if skus:
            batches.append(skus)
        else:
            last_reply = (out or {}).get("reply")
            break
    return batches, last_reply


def flat(batches):
    return [s for b in batches for s in b]


# ---------------------------------------------------------------- tests
def t_build_cards_drops_seen():
    texts = sa._by_category("cotton kurthi")[:8]
    skus = [sku_of(t) for t in texts]
    assert len(skus) == 8, "need at least 8 available kurthis in the catalog"
    got = [n(c["sku"]) for c in sa._build_cards(texts, exclude_skus=skus[:2])]
    assert not set(got) & set(skus[:2]), "already-shown SKUs came back: %s" % (set(got) & set(skus[:2]))
    assert got == skus[2:7], "expected the next 5 unseen SKUs, got %s" % got
    assert sa._build_cards(texts, exclude_skus=skus) == [], "everything seen must give NO cards"


def t_test1_collection_then_salwar():
    c = "t1"
    script("show me the collection", category_hint="kurthi", search_query="kurthi")
    script("show me the salwar", category_hint="salwar", search_query="salwar")
    _, first = ask("show me the collection", c)
    _, second = ask("show me the salwar", c)
    assert len(first) == 5, "first browse should show 5 cards, got %d" % len(first)
    assert second, "salwar request showed nothing"
    assert not set(first) & set(second), "repeat across requests: %s" % (set(first) & set(second))
    assert sorted(SHOWN.rows[c]) == sorted(first + second), "shown-memory does not match what was sent"


def t_test3_same_category_never_repeats_then_exhausts():
    c = "t3"
    script("show me kurthis", category_hint="kurthi", search_query="kurthi")
    batches, last = browse_until_exhausted("show me kurthis", c)
    seen = flat(batches)
    assert len(seen) == len(set(seen)), "a product was shown twice: %s" % [s for s in seen if seen.count(s) > 1][:5]
    all_kurthi = {sku_of(t) for t in sa._by_category("cotton kurthi")}
    assert set(seen) == all_kurthi, "should walk through the WHOLE category (%d of %d shown)" % (len(set(seen)), len(all_kurthi))
    assert len(batches) >= 3, "need >=3 replies to prove memory is longer than 15 SKUs (got %d)" % len(batches)
    assert last == ALREADY_SEEN, "exhausted reply wrong: %r" % last


def t_test3b_colour_request_never_repeats():
    c = "t3b"
    script("show me red kurthi", category_hint="kurthi", color_hint="red", search_query="red kurthi")
    batches, last = browse_until_exhausted("show me red kurthi", c)
    seen = flat(batches)
    assert seen, "red kurthi request showed nothing at all"
    assert len(seen) == len(set(seen)), "a product repeated: %s" % [s for s in seen if seen.count(s) > 1][:5]
    assert last == ALREADY_SEEN, "after the matches run out the reply must be the already-seen message, got %r" % last
    if len(batches) > 1:
        assert not set(batches[0]) & set(batches[1]), "2nd 'red kurthi' repeated cards from the 1st"


def t_test2_reference_shows_same_product_again():
    c = "t2"
    script("show me kurthis", category_hint="kurthi", search_query="kurthi")
    _, first = ask("show me kurthis", c)
    target = first[0]
    ttext = next(t for t in sa._by_category("cotton kurthi") if sku_of(t) == target)
    name = ps.parse_product_details({"text": ttext})["Product"]
    script("show me that one again", search_query=name, refers_to_shown=True)
    _, again = ask("show me that one again", c)
    assert target in again, "referenced product %s was not shown again (got %s)" % (target, again)
    assert set(again) <= set(first), "reference request must only re-show products seen before, got %s" % again
    # same query WITHOUT the reference flag = ordinary browsing -> the seen product must NOT come back
    script("kurthi by that name", search_query=name, refers_to_shown=False)
    _, browse = ask("kurthi by that name", c)
    assert target not in browse, "plain browsing re-showed %s" % target


def t_reference_without_matching_seen_product_falls_back():
    c = "t2b"
    script("show me kurthis b", category_hint="kurthi", search_query="kurthi")
    ask("show me kurthis b", c)
    script("show me that maxi again", category_hint="maxi", search_query="maxi dress", refers_to_shown=True)
    out, cards = ask("show me that maxi again", c)
    assert cards, "reference to nothing seen should fall back to normal results, got no cards"


def t_reference_regex_fallback():
    is_ref = getattr(sa, "_is_reference", None)
    assert is_ref, "_is_reference missing"
    yes = ["show me that red kurthi again", "I like this one", "send me the dress you showed earlier",
           "show me the same one", "can you send that dress again?", "the one you sent yesterday"]
    no = ["show me red kurthi", "show me the salwar", "suggest a dress for my friend", "show me party wear",
          "show me something for a function", "show me more kurthis", "show me the collection"]
    for m in yes:
        assert is_ref({}, m), "should be a reference: %r" % m
    for m in no:
        assert not is_ref({}, m), "should be plain browsing: %r" % m
    assert is_ref({"refers_to_shown": True}, "show me red kurthi"), "AI flag True must win over text"
    assert not is_ref({"refers_to_shown": False}, "show me that again"), "AI flag False must win over text"


def t_test5_mixed_requests_all_excluded():
    c = "t5"
    script("collection", category_hint="kurthi", search_query="kurthi")
    script("salwar please", category_hint="salwar", search_query="salwar")
    script("gift for my friend", search_query="dress")
    script("maxi now", category_hint="maxi", search_query="maxi")
    seen = []
    for m in ("collection", "salwar please", "gift for my friend", "maxi now", "collection", "gift for my friend"):
        _, skus = ask(m, c)
        assert not set(skus) & set(seen), "%r repeated %s" % (m, sorted(set(skus) & set(seen)))
        seen += skus
    assert len(seen) >= 15, "expected at least 15 distinct products across the run, got %d" % len(seen)


def t_test4_all_seen_gives_natural_reply_not_repeat():
    c = "t4"
    for t in ps.load_products():
        SHOWN.rows.setdefault(c, []).append(sku_of(t["text"]))
    script("show me kurthis all", category_hint="kurthi", search_query="kurthi")
    out, cards = ask("show me kurthis all", c)
    assert not cards, "must not repeat when everything was seen"
    assert out and out["reply"] == ALREADY_SEEN, "wrong reply: %r" % (out and out["reply"])


def t_new_arrivals_never_repeat_then_message():
    c = "tn"
    script("new arrivals", intent="NEW_ARRIVALS")
    total = len(sa._new_arrival_products(limit=10 ** 6))
    if total == 0:
        print("      (no temp-new products in catalog - skipped)")
        return
    batches, last = browse_until_exhausted("new arrivals", c)
    seen = flat(batches)
    assert len(seen) == len(set(seen)) and len(seen) == total, "shown %d unique of %d" % (len(set(seen)), total)
    assert last == ALREADY_SEEN_NEW, "wrong exhausted reply for new arrivals: %r" % last


def t_groq_failed_discovery_still_does_not_repeat():
    c = "tr"
    for t in ps.load_products():
        SHOWN.rows.setdefault(c, []).append(sku_of(t["text"]))
    script("show me kurthi", intent="OTHER", _error="groq down")
    out, cards = ask("show me kurthi", c)
    assert out is not None, "recovered path handed over to the old code (which could repeat)"
    assert not cards and out["reply"] == ALREADY_SEEN, "got %r" % (out and out["reply"])


def t_fresh_customer_unchanged_and_wide_pool_only_after_first_reply():
    c = "tf"
    del GATHER_CALLS[:]
    script("show me red kurthi f", category_hint="kurthi", color_hint="red", search_query="red kurthi")
    _, first = ask("show me red kurthi f", c)
    assert first and len(first) <= 5
    assert "max_products" not in GATHER_CALLS[0], "fresh customer: gather call must be unchanged, got %s" % GATHER_CALLS[0]
    stored = sorted(n(s) for s in SHOWN.rows[c])
    assert stored == sorted(first), "memory must hold what was sent: stored=%s sent=%s raw=%s" % (stored, sorted(first), SHOWN.rows[c])
    if sorted(SHOWN.rows[c]) != stored:
        print("      NOTE: remembered SKUs differ from their normalised form (case / trailing '*'): %s"
              % [s for s in SHOWN.rows[c] if s != n(s)])
    ask("show me red kurthi f", c)
    assert GATHER_CALLS[1].get("max_products") == WIDE_POOL, "2nd request should use the wide pool, got %s" % GATHER_CALLS[1]


def t_shown_memory_not_limited_to_15():
    c = "t15"
    script("show me kurthis 15", category_hint="kurthi", search_query="kurthi")
    seen = []
    for _ in range(5):                      # 5 replies x 5 cards = 25 SKUs > 15
        _, skus = ask("show me kurthis 15", c)
        assert not set(skus) & set(seen), "forgot older products: %s" % sorted(set(skus) & set(seen))
        seen += skus
    assert len(seen) == 25


def t_get_shown_falls_back_for_old_bridge():
    saved = prod.get_recent_skus
    try:
        prod.get_recent_skus = lambda customer_id, workspace_id=None, wa_number=None: ["X1"]  # no limit arg
        assert sa._get_shown("c", "w", "n") == ["X1"]
    finally:
        prod.get_recent_skus = saved


def t_bridge_signature_has_limit_default_15():
    p = inspect.signature(_ORIG_GET).parameters
    assert "limit" in p, "ai_bridge_phase2.get_recent_skus has no `limit` argument yet"
    assert p["limit"].default == 15, "default must stay 15 for the old callers"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("t_")]


def info():
    """Not a pass/fail test: shows how tight the search results are for a colour request."""
    try:
        res = ps.search("red kurthi", ps.load_products())
        red = sum(1 for t in res if "red" in t.lower())
        print("INFO  search('red kurthi') returns %d products, %d of them contain the word 'red'; "
              "pool used after the first reply = first %d" % (len(res), red, WIDE_POOL))
    except Exception as e:
        print("INFO  could not inspect search():", e)


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
    info()
    print("\n%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    sys.exit(1 if failed else 0)
