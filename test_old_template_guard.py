"""
test_old_template_guard.py - offline regression test for OLD_TEMPLATE_GUARD.

Run:  python test_old_template_guard.py
OFFLINE: no Groq, no database, no Shopify, no WhatsApp (uses the stubs in test_no_repeat.py).
Exit code 0 = all passed, 1 = something failed.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import test_no_repeat as T  # noqa: E402
from flask import g  # noqa: E402

sa = T.sa
B = sa.prod
if not hasattr(B, "_guard_old_template"):
    import ai_bridge_phase2 as B  # noqa: E402,F811
FALLBACK = B.OLD_TEMPLATE_FALLBACK_REPLY
FAIL_ERR = "JSON parse failed: truncated"


def screenshot_template():
    def item(n, name, sku, price, handle):
        return ("%d. \U0001F457 *%s*\n"
                "\U0001F3F7\ufe0f SKU: %s\n"
                "\U0001F4B0 Price: Rs.%s\n"
                "\U0001F4CF Sizes: XS to 5XL\n"
                "\U0001F517 View & Order: https://www.invicreation.com/products/%s"
                % (n, name, sku, price, handle))
    return ("Vanakkam! Here are our raw-silk salwar sets:\n\n"
            + item(1, "Premium Wine Raw Silk Salwar Set", "ICP00196", 1749, "premium-wine-raw-silk-salwar-set")
            + "\n\n"
            + item(2, "Premium Wine & Green Raw Silk Salwar Set", "ICP00193", 1849, "premium-wine-green-raw-silk-salwar-set")
            + "\n\n"
            + item(3, "Premium Sage Green & Maroon Raw Silk Salwar Set", "ICP00195", 1849, "premium-sage-green-maroon-raw-silk-salwar-set")
            + "\n\n"
            + "Reply with the SKU and your size to order! \U0001F60A")


def t_template_blocked_when_agent_failed():
    with B.app.test_request_context("/ai"):
        g.sales_agent_failed = True
        out = B._guard_old_template(screenshot_template())
    assert out == FALLBACK, "template was not replaced: %r" % out[:80]
    assert out.strip(), "fallback must not be empty"
    assert "View & Order" not in out and "SKU" not in out
    assert "Here are our raw-silk salwar sets" not in out


def t_template_untouched_when_agent_did_not_fail():
    tpl = screenshot_template()
    with B.app.test_request_context("/ai"):
        out = B._guard_old_template(tpl)
    assert out == tpl, "guard changed a reply although the agent did not fail"


def t_other_replies_unchanged_even_when_agent_failed():
    try:
        order_reply = B.build_order_status_reply([])
    except Exception:
        order_reply = "I could not find an order with that number. Could you resend your order number?"
    single = ("\U0001F457 *Pink Floral Print Pure Cotton Suit Set*\n"
              "\U0001F3F7\ufe0f SKU: ICS00141\n"
              "\U0001F4B0 Price: Rs.1639.00\n"
              "\U0001F517 View & Order: https://www.invicreation.com/products/pink-floral-print-pure-cotton-suit-set\n\n"
              "To order, reply with your *size* and we will process it!")
    samples = {
        "company info": getattr(B, "COMPANY_REPLY", "Invi Creation, Erode. Call 9751100905."),
        "order status": order_reply,
        "policy answer": "We offer cash on delivery. Delivery usually takes 5 to 7 working days.",
        "single product card": single,
        "handoff text": getattr(sa, "HANDOFF_FALLBACK_REPLY", "Our team will contact you shortly."),
    }
    with B.app.test_request_context("/ai"):
        g.sales_agent_failed = True
        for name, text in samples.items():
            assert B._guard_old_template(text) == text, "guard changed the %s reply" % name


def t_failed_agent_sets_flag_then_guard_blocks_template():
    msg = "do you deliver to Chennai"
    T.script(msg, intent="OTHER", _error=FAIL_ERR)
    with B.app.test_request_context("/ai"):
        out = sa.run(msg, "guard-flag-1", "WS1", "919999999999")
        assert out is None, "a non-discovery message should still go to the old code"
        assert getattr(g, "sales_agent_failed", False) is True, "_mark_failed did not set the flag"
        assert B._guard_old_template(screenshot_template()) == FALLBACK


def t_raw_silk_messages_use_recovery_not_old_path():
    for i, msg in enumerate(["raw silks", "Raw silks", "raw silk"]):
        T.script(msg, intent="OTHER", _error=FAIL_ERR)
        out, skus = T.ask(msg, "guard-raw-%d" % i)
        assert out is not None, "%r was handed to the old path" % msg
        reply = out.get("reply") or ""
        assert reply.strip(), "%r got an empty reply" % msg
        assert "Here are our raw-silk salwar sets" not in reply
        assert 1 <= len(skus) <= 5, "%r returned %d cards" % (msg, len(skus))


def t_order_delivery_return_words_still_go_to_old_path():
    for i, msg in enumerate(["is raw silk available with cod",
                             "what is the delivery time for raw silk",
                             "i want to return my raw silk salwar"]):
        T.script(msg, intent="OTHER", _error=FAIL_ERR)
        out, skus = T.ask(msg, "guard-old-%d" % i)
        assert out is None, "%r should still be answered by the old code" % msg


def t_company_info_and_handoff_still_work():
    T.script("where is your shop", intent="COMPANY_INFO")
    out, _ = T.ask("where is your shop", "guard-co")
    assert out and out["reply"] == sa.prod.COMPANY_REPLY, "company info reply changed"
    T.script("i want to talk to a human", intent="OTHER", handoff_required=True, handoff_reason="asked for a human")
    out, _ = T.ask("i want to talk to a human", "guard-ho")
    assert out and (out.get("reply") or "").strip(), "handoff gave an empty reply"
    assert any(str(k).startswith("handoff") for k in out), "handoff keys missing"


def t_guard_is_wired_between_ask_groq_and_logging():
    text = open(B.__file__, encoding="utf-8", errors="replace").read()
    route = text[text.index("def ai_reply"):]
    i_ask = route.index("reply = ask_groq(")
    i_guard = route.index("reply = _guard_old_template(reply)")
    i_log = route.index('print(f"[GROQ] {message}"')
    assert i_ask < i_guard < i_log, "guard is not between ask_groq and the log/return"
    assert text.count("reply = _guard_old_template(reply)") == 1, "guard is called more than once"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]


def main():
    passed = 0
    for fn in TESTS:
        try:
            fn()
            print("PASS  %s" % fn.__name__)
            passed += 1
        except Exception as e:
            msg = str(e).encode("ascii", "replace").decode()
            print("FAIL  %s : %s: %s" % (fn.__name__, type(e).__name__, msg))
    print("%d/%d passed" % (passed, len(TESTS)))
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
