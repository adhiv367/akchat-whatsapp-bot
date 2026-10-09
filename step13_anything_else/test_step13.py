import contextlib
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Safety: always a dummy DB and dummy keys, never a real service.
os.environ["DATABASE_URL"] = "postgresql://nobody:nobody@127.0.0.1:1/none"
for _k in ("GROQ_API_KEY", "HUMAN_AGENT_GROQ_API_KEY", "GEMINI_API_KEY"):
    os.environ[_k] = "dummy-not-a-real-key"

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "human_sales_agent"))
import sales_agent  # noqa: E402

_n = [0]
MAXI = "Pure Cotton Maxi"
SALWAR = "Salwar Suit Set"


def txt(sku, ptype="Cotton Kurthi", status="Available", tags=""):
    return ("Product: Dress " + sku + "\nSKU: " + sku + "\nType: " + ptype
            + "\nStatus: " + status + "\nTags: " + tags)


def fake_build_cards(product_texts, exclude_skus=None, max_cards=5):
    ex = {sales_agent._norm_sku(s) for s in (exclude_skus or [])}
    out = []
    for t in product_texts:
        sku = sales_agent._text_sku(t)
        if not sales_agent._is_available(t) or sales_agent._norm_sku(sku) in ex:
            continue
        out.append({"sku": sku, "name": "Dress " + sku,
                    "image": "http://x.invalid/" + sku, "text": "card " + sku})
        if len(out) >= max_cards:
            break
    return out


def und(intent="PRODUCT_DISCOVERY", **kw):
    u = {"intent": intent, "referenced_product_hint": None, "category_hint": None,
         "color_hint": None, "budget_hint": None, "size_hint": None,
         "search_query": None, "handoff_required": False, "handoff_reason": None,
         "refers_to_shown": False, "wants_more": False}
    u.update(kw)
    return u


def run_case(message, u, catalog, shown, gather, added=None):
    _n[0] += 1
    m = {}
    with contextlib.ExitStack() as st:
        def p(obj, name, **kw):
            return st.enter_context(mock.patch.object(obj, name, **kw))
        p(sales_agent.agent, "understand", return_value=(u, []))
        m["gather"] = p(sales_agent.agent, "gather_verified_data",
                        return_value={"products": list(gather), "orders": [],
                                      "images": [], "policy": []})
        m["generate"] = p(sales_agent.agent, "generate_reply",
                          return_value=("LLM intro", True))
        p(sales_agent, "_get_shown", return_value=list(shown))
        p(sales_agent.product_search, "load_products",
          return_value=[{"id": "h-" + sales_agent._text_sku(t), "text": t} for t in catalog])
        p(sales_agent, "_added_map", return_value=dict(added or {}))
        p(sales_agent, "_is_available", side_effect=lambda t: "Status: Available" in t)
        p(sales_agent, "_build_cards", side_effect=fake_build_cards)
        m["failed"] = p(sales_agent, "_mark_failed")
        m["remember"] = p(sales_agent.prod, "remember_skus")
        p(sales_agent.prod, "log_customer_interest")
        p(sales_agent.prod, "detect_category", return_value=None)
        p(sales_agent.prod, "_pending_followup_note", return_value=None, create=True)
        out = sales_agent.run(message, "TEST_S13_%d" % _n[0], 1, "TEST_WA")
    return out, m


def skus(out):
    return [c["sku"] for c in out.get("cards", [])]


def maxi_exhausted():
    m1, m2 = txt("ICM001", MAXI), txt("ICM002", MAXI)
    k1, k2, s1 = txt("ICK001"), txt("ICK002"), txt("ICS001", SALWAR)
    added = {"ICK002": "2026-10-05T00:00:00Z", "ICK001": "2026-10-03T00:00:00Z",
             "ICS001": "2026-10-01T00:00:00Z"}
    return m1, m2, [m1, m2, k1, k2, s1], ["ICM002", "ICM001"], added


WANT = ["ICK002", "ICK001", "ICS001"]


class Step13(unittest.TestCase):
    def check_continues_elsewhere(self, out, m):
        self.assertIsNotNone(out)
        self.assertEqual(skus(out), WANT)
        self.assertEqual(out["intro"], sales_agent.FILL_MORE_REPLY)
        self.assertNotIn(sales_agent.ALREADY_SEEN_REPLY, out["reply"])
        m["generate"].assert_not_called()

    def test_a_anything_else_search_returns_the_shown_products(self):
        m1, m2, catalog, shown, added = maxi_exhausted()
        out, m = run_case("anything else", und(wants_more=True), catalog, shown, [m1, m2], added)
        self.check_continues_elsewhere(out, m)

    def test_b_anything_else_search_returns_nothing(self):
        m1, m2, catalog, shown, added = maxi_exhausted()
        out, m = run_case("anything else", und(wants_more=True), catalog, shown, [], added)
        self.check_continues_elsewhere(out, m)

    def test_c_anything_else_labelled_other_by_the_ai(self):
        m1, m2, catalog, shown, added = maxi_exhausted()
        out, m = run_case("anything else", und(intent="OTHER", wants_more=True),
                          catalog, shown, [m1, m2], added)
        self.check_continues_elsewhere(out, m)

    def test_d_everything_seen_restarts_and_never_claims_unseen(self):
        k1, k2, k3 = txt("ICK001"), txt("ICK002"), txt("ICK003")
        out, m = run_case("anything else", und(wants_more=True), [k1, k2, k3],
                          ["ICK001", "ICK002", "ICK003"], [k1, k2, k3])
        self.assertIsNotNone(out)
        self.assertEqual(skus(out), ["ICK003", "ICK002", "ICK001"])
        self.assertEqual(out["intro"], sales_agent.FILL_RESTART_REPLY)
        self.assertNotIn(sales_agent.ALREADY_SEEN_REPLY, out["reply"])
        m["generate"].assert_not_called()

    def test_e_never_more_than_five_cards(self):
        m1 = txt("ICM001", MAXI)
        pool = [txt("ICK00%d" % i) for i in range(1, 9)]
        added = {"ICK00%d" % i: "2026-10-0%dT00:00:00Z" % i for i in range(1, 9)}
        out, m = run_case("anything else", und(wants_more=True), [m1] + pool,
                          ["ICM001"], [m1], added)
        self.assertIsNotNone(out)
        self.assertEqual(len(skus(out)), 5)
        self.assertTrue(all(s.startswith("ICK") for s in skus(out)))

    def test_f_customer_who_has_seen_nothing_is_unchanged(self):
        k1, k2 = txt("ICK001"), txt("ICK002")
        out, m = run_case("anything else", und(wants_more=True), [k1, k2], [], [k1, k2])
        self.assertIsNotNone(out)
        self.assertEqual(sorted(skus(out)), ["ICK001", "ICK002"])
        self.assertEqual(out["intro"], "LLM intro")
        self.assertNotIn(sales_agent.ALREADY_SEEN_REPLY, out["reply"])
        m["generate"].assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
