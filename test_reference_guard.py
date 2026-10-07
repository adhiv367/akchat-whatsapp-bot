import os, sys, unittest
ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, os.path.join(ROOT, "human_sales_agent")):
    if p not in sys.path:
        sys.path.insert(0, p)
try:
    import ai_bridge_phase2  # noqa: F401
except Exception:
    pass
import sales_agent as sa

class ReferenceGuard(unittest.TestCase):
    def test_plain_browsing_never_reference_even_if_model_says_true(self):
        self.assertFalse(sa._is_reference({"refers_to_shown": True}, "show me red kurthi"))
        self.assertFalse(sa._is_reference({"refers_to_shown": True}, "show me the salwar"))
    def test_real_references_stay_true(self):
        for m in ("show me that red kurthi again", "I like the second one",
                  "the first one looks nice, send it again", "I like this one",
                  "send me the dress you showed earlier"):
            self.assertTrue(sa._is_reference({"refers_to_shown": True}, m), m)
    def test_non_ascii_trusts_the_model(self):
        self.assertTrue(sa._is_reference({"refers_to_shown": True}, "\u0b87\u0ba4\u0bc1 \u0bb5\u0bc7\u0ba3\u0bcd\u0b9f\u0bc1\u0bae\u0bcd"))
    def test_model_false_stays_false(self):
        self.assertFalse(sa._is_reference({"refers_to_shown": False}, "show me that red kurthi again"))
    def test_missing_flag_uses_regex_fallback(self):
        self.assertTrue(sa._is_reference({}, "show me the same one"))
        self.assertFalse(sa._is_reference({}, "show me red kurthi"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
