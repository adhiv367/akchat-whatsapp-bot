"""
test_card_fixes.py — regression tests for:
  1. _build_cards() must exclude out-of-stock products entirely (no LLM/DB needed).
  2. sales_agent.run() must return None immediately for a SKU message, so /ai
     falls back to the existing (untouched) SKU branch (no LLM/DB needed).

Run anytime with:
    python test_card_fixes.py
"""
import sys, os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_THIS_DIR, '..')
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _THIS_DIR)

import ai_bridge_phase2
sys.modules.setdefault("ai_bridge_phase2", ai_bridge_phase2)
import sales_agent

failures = 0


def check(name, condition):
    global failures
    status = "PASS" if condition else "FAIL"
    if not condition:
        failures += 1
    print(f"[{status}] {name}")


print("=" * 60)
print("TEST 1 — out-of-stock products excluded from cards")
print("=" * 60)
available_a = ("Product: Available Kurthi A -PI : ICKA001\nSKU: ICKA001\nPrice: Rs.900\n"
               "Image: https://example.com/a.jpg\nStatus: Available")
available_b = ("Product: Available Kurthi B -PI : ICKB002\nSKU: ICKB002\nPrice: Rs.950\n"
               "Image: https://example.com/b.jpg\nStatus: Available")
oos_c = ("Product: OOS Salwar C -PI : ICSC003\nSKU: ICSC003\nPrice: Rs.1600\n"
         "Image: https://example.com/c.jpg\nStatus: Out of Stock")

cards = sales_agent._build_cards([available_a, available_b, oos_c], exclude_skus=[])
skus = [c["sku"] for c in cards]
check("out-of-stock SKU (ICSC003) is not in the returned cards", "ICSC003" not in skus)
check("both available products are returned", len(cards) == 2)
check("order footer lands on the last card, and that card is available (not out of stock)",
      bool(cards) and cards[-1]["text"].strip().endswith("process it! \U0001F60A"))

print()
print("=" * 60)
print("TEST 2 — all products out of stock -> zero cards, nothing invented")
print("=" * 60)
oos_only = ["Product: X -PI : ICKX1\nSKU: ICKX1\nPrice: Rs.500\n"
            "Image: https://example.com/x.jpg\nStatus: Out of Stock"]
cards2 = sales_agent._build_cards(oos_only, exclude_skus=[])
check("zero cards when every candidate is out of stock", cards2 == [])

print()
print("=" * 60)
print("TEST 3 — SKU message in sales_agent.run() returns None (fallback path)")
print("=" * 60)
result = sales_agent.run("do you have ICK00130", "TEST_fallback1", "1", "TEST_fallback1")
check("sales_agent.run() returns None for a SKU message (so /ai's existing SKU "
      "branch handles it, unchanged)", result is None)

print()
print("=" * 60)
if failures:
    print(f"{failures} CHECK(S) FAILED")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
print("=" * 60)
