"""
test_new_product_priority.py - local test for NEW PRODUCTS FIRST (added_at stored in Postgres).

Put this file in  E:\\ak chat new version\\phase2-test  (next to ai_bridge_phase2.py) and run:
    python test_new_product_priority.py

OFFLINE: no Groq, no real database, no Shopify, no WhatsApp, no Render.
  - the AI "understand" and "generate_reply" steps are scripted fakes
  - the catalog is a small made-up one (old products first, new ones appended at the END like the webhook does)
  - the added_at lookup and the database calls are replaced by fakes
  - "already shown" is an in-memory store

Names the patch must provide (so these tests fail BEFORE the patch and pass after it):
  ai_bridge_phase2.record_added_at(handle)   insert a row, ON CONFLICT DO NOTHING
  ai_bridge_phase2.clear_added_at(handle)    delete the row
  ai_bridge_phase2.get_added_at_map()        {handle: iso timestamp}, {} on any failure
  sales_agent._added_map()                   {SKU: iso timestamp}
  sales_agent._newest_first(texts, color)    reorder only, never drop

Exit code 0 = all passed, 1 = something failed.
"""
import contextlib
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
HSA = os.path.join(ROOT, "human_sales_agent")
for _p in (ROOT, HSA):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sales_agent as sa  # noqa: E402
import ai_bridge_phase2 as br  # noqa: E402

agent, ps, prod = sa.agent, sa.product_search, sa.prod

_MISSING = object()


@contextlib.contextmanager
def patched(obj, **attrs):
    """Temporarily set attributes (also ones that do not exist yet); always restore."""
    old = {k: getattr(obj, k, _MISSING) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is _MISSING:
                try:
                    delattr(obj, k)
                except AttributeError:
                    pass
            else:
                setattr(obj, k, v)


# ---------------------------------------------------------------- offline stubs (same style as test_no_repeat.py)
class Shown:
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

GATHER_CALLS = []
_orig_gather = agent.gather_verified_data


def _spy_gather(*a, **k):
    GATHER_CALLS.append(dict(k))
    return _orig_gather(*a, **k)


agent.gather_verified_data = _spy_gather


# ---------------------------------------------------------------- fake catalog
def n(s):
    return sa._norm_sku(s)


def mk(sku, name, typ="cotton kurthi", tags=""):
    return ("Product: %s\nHandle: %s\nSKU: %s\nAll SKUs: %s\nType: %s\nPrice: Rs.999\nFabric: cotton\n"
            "Available Sizes: XS,S,M,L,XL\nTags: %s\nImage: https://example.com/%s.jpg\nStatus: Available"
            % (name, sku.lower(), sku, sku, typ, tags, sku.lower()))


SAL = "salwar suit set"
# (sku, name, type, tags)  -- OLD products first, in the old file order
_OLD = [
    ("OK1", "Green Cotton Kurthi", "cotton kurthi", ""),
    ("OK2", "Yellow Cotton Kurthi", "cotton kurthi", ""),
    ("OK3", "Pink Cotton Kurthi", "cotton kurthi", ""),
    ("OK4", "Orange Cotton Kurthi", "cotton kurthi", "temp-new"),
    ("OK5", "Grey Cotton Kurthi", "cotton kurthi", ""),
    ("OK6", "Purple Cotton Kurthi", "cotton kurthi", ""),
    ("ORED1", "Red Floral Kurthi", "cotton kurthi", ""),
    ("ORED2", "Red Stripe Kurthi", "cotton kurthi", ""),
    ("ORED3", "Red Dot Kurthi", "cotton kurthi", ""),
    ("OS1", "Cotton Salwar Set One", SAL, ""),
    ("OS2", "Cotton Salwar Set Two", SAL, "temp-new"),
    ("OS3", "Cotton Salwar Set Three", SAL, ""),
    ("OS4", "Cotton Salwar Set Four", SAL, ""),
]
# NEW products, appended at the END of the file (that is what the Shopify webhook does)
_NEW = [
    ("NRED2", "Red Leaf Kurthi", "cotton kurthi", ""),
    ("NRED1", "Red Block Kurthi", "cotton kurthi", ""),
    ("NK1", "Teal Cotton Kurthi", "cotton kurthi", ""),
    ("NK2", "Maroon Cotton Kurthi", "cotton kurthi", ""),
    ("NK3", "White Cotton Kurthi", "cotton kurthi", ""),
    ("NBLUE", "Blue Tailored Kurthi", "cotton kurthi", ""),   # newest of all; "tailored" contains "red"
    ("NS1", "Mint Salwar Set", SAL, ""),
    ("NS2", "Rose Salwar Set", SAL, ""),
]
CATALOG = [{"id": s.lower(), "text": mk(s, nm, t, tg)} for s, nm, t, tg in _OLD + _NEW]

ADDED = {   # SKU -> added_at (ISO strings sort correctly)
    "NRED2": "2026-10-02T09:00:00Z",
    "NRED1": "2026-10-04T09:00:00Z",
    "NK1": "2026-10-05T09:00:00Z",
    "NK2": "2026-10-06T09:00:00Z",
    "NK3": "2026-10-07T10:00:00Z",
    "NBLUE": "2026-10-07T12:00:00Z",
    "NS1": "2026-10-05T11:00:00Z",
    "NS2": "2026-10-06T11:00:00Z",
}


def ask(message, customer, **script_kw):
    script(message, **script_kw)
    with patched(ps, load_products=lambda: CATALOG), patched(sa, _added_map=lambda: dict(ADDED)):
        out = sa.run(message, customer, "WS1", "919999999999")
    cards = (out or {}).get("cards") or []
    return out, [n(c["sku"]) for c in cards]


# ---------------------------------------------------------------- fake database for the bridge functions
class FakeCursor:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows=None):
        self.log, self.rows, self.commits = [], rows or [], 0

    def cursor(self):
        return FakeCursor(self.log, self.rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def call_webhook(route, payload, catalog):
    rec, clr = [], []
    with patched(br,
                 verify_shopify_webhook=lambda req: json.dumps(payload).encode("utf-8"),
                 _load_products_raw=lambda: catalog,
                 save_products=lambda p: None,
                 stock_overlay_clear=lambda h: None,
                 notify_matching_interests=lambda *a, **k: None,
                 embed_product_chunk=lambda *a, **k: None,
                 _stock_overlay_doc_text=lambda h, t: t,
                 record_added_at=lambda h, a=None: (rec.append(h), REC_AT.append(a)),
                 clear_added_at=lambda h: clr.append(h)):
        resp = br.app.test_client().post(route, data=b"{}")
    return resp, rec, clr


def payload(handle="new-kurthi", published=True):
    return {"handle": handle, "title": "Blue Kurthi PI : N1", "product_type": "Cotton Kurthi", "tags": "",
            "status": "active" if published else "draft",
            "published_at": "2026-10-07T10:00:00Z" if published else None,
            "variants": [{"sku": "N1", "price": "999", "inventory_quantity": 5}],
            "images": [{"src": "https://example.com/n1.jpg"}]}


# ---------------------------------------------------------------- tests
def t01_created_records_added_at():
    resp, rec, clr = call_webhook("/shopify/product-created", payload(), [])
    assert rec == ["new-kurthi"], "product-created must record added_at once, got %s" % rec


def t01b_created_existing_product_not_restamped():
    resp, rec, clr = call_webhook("/shopify/product-created", payload(), [{"id": "new-kurthi", "text": "Product: old"}])
    assert rec == [], "an already-existing product must not be stamped again, got %s" % rec


def t02_update_existing_preserves_added_at():
    resp, rec, clr = call_webhook("/shopify/product-updated", payload(), [{"id": "new-kurthi", "text": "Product: old"}])
    assert rec == [] and clr == [], "a plain edit must not touch added_at (record=%s clear=%s)" % (rec, clr)


def t03_update_newly_published_gets_added_at():
    resp, rec, clr = call_webhook("/shopify/product-updated", payload(), [])
    assert rec == ["new-kurthi"], "a newly published product must get added_at, got %s" % rec


def t03b_update_unpublished_clears_added_at():
    resp, rec, clr = call_webhook("/shopify/product-updated", payload(published=False),
                                  [{"id": "new-kurthi", "text": "Product: old"}])
    assert clr == ["new-kurthi"] and rec == [], "unpublish must clear the stamp (record=%s clear=%s)" % (rec, clr)


def t03c_record_added_at_is_insert_do_nothing():
    conn = FakeConn()
    with patched(br, get_db_conn=lambda: conn, resolve_sole_active_workspace=lambda: 1):
        br.record_added_at("new-kurthi")
    sql = " ".join(s for s, _ in conn.log).upper()
    assert "PRODUCT_ADDED_AT" in sql and "INSERT" in sql, "no insert into product_added_at: %s" % conn.log
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql, "insert must never overwrite an existing stamp"
    assert conn.commits >= 1, "insert was not committed"


def t03d_db_failure_never_breaks():
    with patched(br, get_db_conn=lambda: None, resolve_sole_active_workspace=lambda: 1):
        br.record_added_at("x")
        br.clear_added_at("x")
        assert br.get_added_at_map(use_cache=False) == {} if "use_cache" in br.get_added_at_map.__code__.co_varnames \
            else br.get_added_at_map() == {}, "no DB must give an empty map"


REC_AT = []   # the added_at values the webhooks passed to record_added_at


def t03e_record_added_at_uses_given_time():
    # an old product stamped late must get its OWN old date, so it can never look new
    conn = FakeConn()
    with patched(br, get_db_conn=lambda: conn, resolve_sole_active_workspace=lambda: 1):
        br.record_added_at("old-kurthi", "2025-01-05T09:00:00Z")
    assert any("2025-01-05T09:00:00Z" in str(p) for _, p in conn.log), "given added_at was not used: %s" % conn.log


def t03f_webhooks_pass_shopify_published_at():
    for route in ("/shopify/product-created", "/shopify/product-updated"):
        REC_AT[:] = []
        call_webhook(route, payload(), [])
        assert REC_AT == ["2026-10-07T10:00:00Z"], "%s must stamp with Shopify's published_at, got %s" % (route, REC_AT)


def t12_added_map_translates_handle_to_sku():
    with patched(ps, load_products=lambda: CATALOG), patched(prod, get_added_at_map=lambda: {"nk1": "2026-10-05T09:00:00Z"}):
        got = sa._added_map()
    assert got == {"NK1": "2026-10-05T09:00:00Z"}, "handle must be translated to SKU: %s" % got


REC_AT = []   # the added_at values the webhooks passed to record_added_at


def t03e_record_added_at_uses_given_time():
    # an old product stamped late must get its OWN old date, so it can never look new
    conn = FakeConn()
    with patched(br, get_db_conn=lambda: conn, resolve_sole_active_workspace=lambda: 1):
        br.record_added_at("old-kurthi", "2025-01-05T09:00:00Z")
    assert any("2025-01-05T09:00:00Z" in str(p) for _, p in conn.log), "given added_at was not used: %s" % conn.log


def t03f_webhooks_pass_shopify_published_at():
    for route in ("/shopify/product-created", "/shopify/product-updated"):
        REC_AT[:] = []
        call_webhook(route, payload(), [])
        assert REC_AT == ["2026-10-07T10:00:00Z"], "%s must stamp with Shopify's published_at, got %s" % (route, REC_AT)


def t12_added_map_translates_handle_to_sku():
    with patched(ps, load_products=lambda: CATALOG), patched(prod, get_added_at_map=lambda: {"nk1": "2026-10-05T09:00:00Z"}):
        got = sa._added_map()
    assert got == {"NK1": "2026-10-05T09:00:00Z"}, "handle must be translated to SKU: %s" % got


def t04a_search_top_k_widens_when_asked():
    docs = [{"text": "Product: Kurthi %d" % i} for i in range(12)]
    assert len(br.search("kurthi", docs)) == 3, "default search must stay at 3"
    assert len(br.search("kurthi", docs, top_k=10)) == 10, "top_k=10 must give 10"
    with patched(ps, load_products=lambda: CATALOG):
        und = dict(BASE, search_query="kurthi", category_hint="kurthi")
        data = agent.gather_verified_data(und, "t4", workspace_id="WS1", wa_number="919999999999", max_products=12)
    assert len(data["products"]) == 12, "gather_verified_data(max_products=12) returned %d, want 12" % len(data["products"])


def t04b_fresh_customer_category_request_uses_wide_pool():
    GATHER_CALLS[:] = []
    ask("show me kurthi", "t4b", category_hint="kurthi", search_query="kurthi")
    assert GATHER_CALLS and GATHER_CALLS[-1].get("max_products") == sa.WIDE_POOL, \
        "fresh customer category request did not ask for the wide pool: %s" % GATHER_CALLS


def t05_newest_kurthi_first():
    _, got = ask("show me kurthi", "t5", category_hint="kurthi", search_query="kurthi")
    assert got == ["NBLUE", "NK3", "NK2", "NK1", "NRED1"], "kurthis not newest-first: %s" % got


def t06_newest_salwar_first():
    _, got = ask("show me salwar", "t6", category_hint="salwar", search_query="salwar")
    assert len(got) == 5, "expected 5 salwar cards, got %s" % got
    assert got[:2] == ["NS2", "NS1"], "newest salwars must come first: %s" % got
    assert all(s.startswith("OS") for s in got[2:]), "older salwars should follow: %s" % got


def t07_red_relevance_before_newest():
    _, got = ask("show me red kurthi", "t7", category_hint="kurthi", color_hint="red", search_query="red kurthi")
    assert "NBLUE" not in got, "newest BLUE kurthi outranked red ones: %s" % got
    assert got[:2] == ["NRED1", "NRED2"], "newest red kurthis must lead: %s" % got
    assert set(got) == {"NRED1", "NRED2", "ORED1", "ORED2", "ORED3"}, "all 5 should be red: %s" % got


def t08_already_shown_still_excluded():
    SHOWN.rows["t8"] = ["NBLUE", "NK3"]
    _, got = ask("show me kurthi", "t8", category_hint="kurthi", search_query="kurthi")
    assert got and not {"NBLUE", "NK3"} & set(got), "already-shown products came back: %s" % got


def t09_newest_shown_next_unseen_newest_chosen():
    SHOWN.rows["t9"] = ["NBLUE"]
    _, got = ask("show me kurthi", "t9", category_hint="kurthi", search_query="kurthi")
    assert got == ["NK3", "NK2", "NK1", "NRED1", "NRED2"], "next unseen newest not chosen: %s" % got


def t10_new_arrivals_no_repeat_still_works():
    with patched(ps, load_products=lambda: CATALOG):
        everything = [n(sa._text_sku(t)) for t in sa._new_arrival_products()]
        after = [n(sa._text_sku(t)) for t in sa._new_arrival_products(exclude_skus=["OK4"])]
    assert "OK4" in everything and "OS2" in everything, "fake catalog should have 2 new arrivals: %s" % everything
    assert after == ["OS2"], "shown new arrival must be dropped, not replaced by non-new products: %s" % after


def t11_newest_first_never_drops_products():
    texts = [t["text"] for t in CATALOG if "kurthi" in t["text"].lower()]
    with patched(sa, _added_map=lambda: dict(ADDED)):
        out = sa._newest_first(texts, None)
    assert len(out) == len(texts) and set(out) == set(texts), "_newest_first must only reorder"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("t") and k[1:3].isdigit() and callable(v)]

if __name__ == "__main__":
    failed = 0
    for fn in TESTS:
        SHOWN.rows.clear()
        try:
            fn()
            print("PASS  %s" % fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL  %s -> %s: %s" % (fn.__name__, type(e).__name__, e))
            if os.environ.get("TRACE"):
                traceback.print_exc()
    print("\n%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    sys.exit(1 if failed else 0)
