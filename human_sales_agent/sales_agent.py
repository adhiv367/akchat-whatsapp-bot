"""
sales_agent.py - the Human Sales Agent, production entry point.

HOW IT IS USED
ai_bridge_phase2.py's /ai route calls run(...) ONLY when the Render
environment variable SALES_AGENT_ENABLED=true. If SALES_AGENT_ENABLED is
"false" or missing, this file is never even imported.

run(...) returns:
  * a dict  -> /ai sends it back to Node as the reply
  * None    -> "I have nothing to say / something went wrong". /ai then simply
               carries on with the OLD code, exactly as before.

WHAT IT DOES (the flow you asked for)
  1. Call 1 (agent.understand): read the WHOLE message + recent chat and work
     out what the customer wants. It does NOT write the reply.
  2. Fetch the REAL data from the existing production code (products,
     COMPANY_REPLY, policy FAQ, orders, the temp-new tag).
  3. Call 2 (agent.generate_reply): write a natural reply using ONLY that data.

WHAT IT NEVER CHANGES
  * COMPANY_REPLY, the category overview text and the order-status text are
    the existing production texts, sent word for word (not rewritten by AI).
  * It does not write to the conversation log itself. The /ai route logs
    every reply, same as for every other branch.

PAUSE
  SALES_AGENT_PAUSE_SECONDS (Render env variable, default 0) = a short wait
  between Call 1 and Call 2. Only useful if Groq starts answering
  "429 too many requests". 0 means no wait.
"""
import os
import re
import sys
import time

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import agent
from tools import product_search

# ai_bridge_phase2 is already loaded when the /ai route imports this file
# (the route makes sure of that), so this does not load it a second time.
import ai_bridge_phase2 as prod


SKU_PATTERN = re.compile(r'\b(IC[A-Z]\d+)\b', re.IGNORECASE)

# Same wording agent.py already uses when a handoff is flagged.
HANDOFF_FALLBACK_REPLY = ("Thanks for letting me know - I've noted this, and one of our "
                          "team members will follow up with you shortly!")

NEW_ARRIVAL_TAG = "temp-new"
CARD_INTENTS = {"PRODUCT_DISCOVERY", "PRODUCT_AVAILABILITY", "COLOR_ENQUIRY"}
MAX_CARDS = 5
CARDS_INSTRUCTION = (
    "\n\n[INSTRUCTION FOR THIS REPLY: full product cards (photo, price, sizes, link) "
    "are sent automatically right after your message. Write ONLY a short, warm 1-2 line "
    "lead-in. Do NOT list, name, describe or price any product yourself and do not include "
    "links. You may end with one short question. Reply in the SAME language as the "
    "customer's message above: Tamil script -> Tamil; Tamil written in English letters "
    "-> Tanglish; English -> English.]"
)


# ── small helpers ────────────────────────────────────────────────────────
def _pause_seconds():
    try:
        return max(0.0, float(os.environ.get("SALES_AGENT_PAUSE_SECONDS", "0")))
    except ValueError:
        return 0.0


def _text_sku(text):
    for ln in text.split("\n"):
        if ln.startswith("SKU: "):
            return ln[5:].strip()
    return ""


def _is_available(text):
    return "status: available" in text.lower()


def _fallback_intro(message):
    if any("\u0b80" <= ch <= "\u0bff" for ch in (message or "")):
        return "இதோ எங்களிடம் உள்ள டிசைன்கள் 😊"
    return "Here are some options for you 😊"


def _result(understanding, reply, image=None, images=None, log_text=None):
    return {
        "reply": reply,
        "image": image,
        "images": images or [],
        "type": "product" if image else "text",
        "intent": understanding.get("intent"),
        "handoff_required": bool(understanding.get("handoff_required")),
        "handoff_reason": understanding.get("handoff_reason"),
        "log_text": log_text if log_text is not None else reply,  # /ai removes this before replying
    }


import re as _re
DIWALI_RE = _re.compile(r"diwal|diwli|diwlai|deepaval|deepawal|divali|dipaval", _re.I)
MORE_RE = _re.compile(r"^\s*(show\s+)?(me\s+)?(more|next|another)\b|\bmore\s+(designs?|dresses|options|please|pls)\b", _re.I)
_TOPIC = {}  # (workspace_id, customer_id) -> {"offset": n, "t": time}


def _norm_sku(s):
    return (s or "").strip().rstrip("*").upper()


def _diwali_products():
    # Diwali = ICP category only, in stock, in catalog order.
    out = []
    for p in product_search.load_products():
        t = p.get("text", "")
        if _norm_sku(_text_sku(t)).startswith("ICP") and _is_available(t):
            out.append(t)
    return out


CATEGORY_PREFIXES = [("ICK", "Kurthis"), ("ICS", "Salwar Suit Sets"), ("ICM", "Maxi Dresses")]


def _category_cards():
    # CATEGORY_CARDS: one in-stock product (card + photo) per main category, in catalog order.
    picked = {}
    for p in product_search.load_products():
        t = p.get("text", "")
        sku = _norm_sku(_text_sku(t))
        if not sku or sku.startswith("TEST") or not _is_available(t):
            continue
        for pre, _n in CATEGORY_PREFIXES:
            if sku.startswith(pre) and pre not in picked:
                picked[pre] = t
    texts = [picked[pre] for pre, _n in CATEGORY_PREFIXES if pre in picked]
    return _build_cards(texts, max_cards=len(texts)) if texts else []


def _category_cards_result(understanding, message, cards):
    if any("\u0b80" <= ch <= "\u0bff" for ch in (message or "")):
        intro = "எங்களிடம் குர்தி, சல்வார் செட், மேக்ஸி டிரெஸ் உள்ளன. ஒவ்வொன்றிலும் ஒரு டிசைன் இதோ 😊"
    else:
        intro = "We have Kurthis, Salwar Suit Sets and Maxi Dresses. Here is one design from each 😊 Tell me which one you'd like to see more of!"
    full_text = intro + "\n\n" + "\n\n".join(c["text"] for c in cards)
    images = [{"sku": c["sku"], "name": c["name"], "image": c["image"]} for c in cards if c["image"]]
    log_text = intro + "\n(Shown: " + ", ".join(c["name"] + " (" + c["sku"] + ")" for c in cards) + ")"
    out = _result(understanding, full_text, image=images[0]["image"] if images else None,
                  images=images, log_text=log_text)
    out["intro"] = intro
    out["cards"] = cards
    return out


GENERIC_WORDS = {"kurthi", "kurthis", "kurti", "kurtis", "kurta", "kurtas", "salwar", "salwars",
                 "suit", "suits", "set", "sets", "maxi", "maxis", "dress", "dresses", "design", "designs",
                 "collection", "model", "models", "pure", "cotton", "types", "type", "all", "some", "new"}


def _category_of(*texts):
    # CATEGORY_FILTER: map the AI's category words to the exact catalog Type value.
    t = " ".join(str(x) for x in texts if x).lower()
    if "salwar" in t or "suit" in t:
        return "salwar suit set"
    if "maxi" in t:
        return "pure cotton maxi"
    if "kurthi" in t or "kurti" in t or "kurta" in t:
        if _re.search(r"\bsets?\b|co-?ord", t):
            return "cotton kurthi set"
        return "cotton kurthi"
    return None


def _type_of(text):
    for ln in text.split("\n"):
        if ln.startswith("Type:"):
            return ln.split(":", 1)[1].strip().lower()
    return ""


def _by_category(cat):
    out = []
    for p in product_search.load_products():
        t = p.get("text", "")
        sku = _norm_sku(_text_sku(t))
        if sku and not sku.startswith("TEST") and _type_of(t) == cat and _is_available(t):
            out.append(t)
    return out


def _new_arrival_products(exclude_skus=None, limit=MAX_CARDS):
    """Products tagged temp-new, Available only. SKUs this customer was already
    shown come last."""
    exclude = set(exclude_skus or [])
    fresh, stale = [], []
    for p in product_search.load_products():
        t = p.get("text", "")
        tags = next((ln for ln in t.split("\n") if ln.lower().startswith("tags:")), "")
        _tag_list = [x.strip() for x in tags.split(":", 1)[-1].lower().split(",")]  # STEP6C: exact tag, not a substring
        if NEW_ARRIVAL_TAG in _tag_list and _is_available(t):
            (stale if _text_sku(t) in exclude else fresh).append(t)
    return (fresh + stale)[:limit]


def _build_cards(product_texts, exclude_skus=None, max_cards=MAX_CARDS):
    """One card per product, same layout as the SKU lookup reply. Only the
    last card keeps the 'To order...' footer. Values come straight from the
    existing product data - nothing is invented."""
    exclude = set(exclude_skus or [])
    available = [t for t in product_texts if _is_available(t)]
    ordered = sorted(available, key=lambda t: (1 if _text_sku(t) in exclude else 0))
    cards, seen = [], set()
    for t in ordered:
        details = product_search.parse_product_details({"text": t})
        text, image = product_search.build_product_reply(details)
        sku = details.get("SKU", "")
        if sku in seen:
            continue
        seen.add(sku)
        cards.append({"sku": sku, "name": details.get("Product", ""), "image": image, "text": text})
        if len(cards) >= max_cards:
            break
    for c in cards[:-1]:
        c["text"] = c["text"].split("\n\nTo order")[0].rstrip()
    return cards


def _order_reply(message, customer_id, workspace_id):
    """Order status, using the EXISTING production lookups and the EXISTING
    reply text. Same rules as the old code: a phone number typed in the message
    is never used for the lookup - only the sender's own number is."""
    order_number = prod.extract_order_number(message)
    email = prod.extract_email(message)
    if order_number:
        orders = prod.lookup_order_by_number(order_number)
    elif email:
        orders = prod.lookup_order_by_email(email)
    else:
        orders = prod.lookup_order_by_phone(customer_id, workspace_id=workspace_id)
    return prod.build_order_status_reply(orders)


def _has_order_details(message):
    return bool(prod.extract_order_number(message)
                or prod.extract_email(message)
                or re.search(r'\b\d{10}\b', re.sub(r'\D', ' ', message)))


# ── the entry point ──────────────────────────────────────────────────────
def _mark_failed():
    # SALES_AGENT_PATCH2: tells /ai the AI steps failed, so the old keyword rules that
    # misfire on words like "about" / "my order" are skipped for this message.
    try:
        from flask import g
        g.sales_agent_failed = True
    except Exception:
        pass


def run(message, customer_id, workspace_id, wa_number):
    # Fixed rule: SKU messages are handled by the existing SKU code in /ai.
    if SKU_PATTERN.search(message):
        return None
    # Never run without a resolved workspace (multi-tenant safety).
    if not workspace_id:
        return None

    # ── Call 1: understand ──
    understanding, history = agent.understand(
        message, customer_id, workspace_id=workspace_id, wa_number=wa_number)
    understanding["_raw_message"] = message
    intent = understanding.get("intent")

    if intent == "ORDER_CHANGE":
        # Changing/cancelling an existing order is a job for the team, not the AI.
        understanding["handoff_required"] = True
        understanding["handoff_reason"] = (understanding.get("handoff_reason")
                                           or "Customer wants to change an existing order")
    handoff = bool(understanding.get("handoff_required"))

    if understanding.get("_error"):
        print(f"[SALES-AGENT] understand step failed: {understanding['_error']}")
        if handoff:
            # The keyword safety net still works when Groq is down.
            return _result(understanding, HANDOFF_FALLBACK_REPLY)
        _mark_failed()
        return None  # old code takes over

    # ── Existing data, fixed texts (no second Groq call) ──
    if not handoff:
        if intent == "COMPANY_INFO":
            return _result(understanding, prod.COMPANY_REPLY)

        if intent == "CATEGORY_OVERVIEW":
            _cc = _category_cards()
            if _cc:
                return _category_cards_result(understanding, message, _cc)
            reply, top_image, image_list = prod.build_collection_overview_reply(prod.load_products())
            return _result(understanding, reply, image=top_image, images=image_list)

        if intent == "ORDER_STATUS" or (
                _has_order_details(message)
                and prod.was_just_asked_for_order_number(
                    customer_id, workspace_id=workspace_id, wa_number=wa_number)):
            return _result(understanding, _order_reply(message, customer_id, workspace_id))

    # ── Existing product data ──
    already_shown = prod.get_recent_skus(customer_id, workspace_id=workspace_id, wa_number=wa_number) or []

    if intent == "NEW_ARRIVALS" and not handoff:
        new_products = _new_arrival_products(exclude_skus=already_shown)
        if not new_products:
            # STEP6B: no new arrivals -> say so honestly, never fall back to generic products
            return _result(understanding, prod.NEW_ARRIVALS_NONE_REPLY)
        verified_data = {"products": new_products, "orders": [], "images": [], "policy": []}
        understanding["intent"] = "PRODUCT_DISCOVERY"
        pause = _pause_seconds()
        if pause:
            time.sleep(pause)
    else:
        pause = _pause_seconds()
        if pause:
            time.sleep(pause)
        verified_data = agent.gather_verified_data(
            understanding, customer_id, workspace_id=workspace_id, wa_number=wa_number)

    # CATEGORY_FILTER: a plain category request ("show me kurthis") must return that category only.
    try:
        if intent in CARD_INTENTS and not handoff:
            _q = understanding.get("search_query") or understanding.get("referenced_product_hint") or ""
            _cat = _category_of(understanding.get("category_hint"), _q)
            if _cat:
                _words = _re.findall(r"[a-z]+", str(_q).lower())
                if _words and all(w in GENERIC_WORDS for w in _words) and not understanding.get("color_hint"):
                    _pool = _by_category(_cat)
                else:
                    _ok = {_cat, "cotton kurthi set"} if _cat == "cotton kurthi" else {_cat}
                    _pool = [t for t in verified_data.get("products", [])
                             if _type_of(t) in _ok and _is_available(t)]
                if _pool:
                    verified_data["products"] = _pool[:10]
    except Exception as _ce:
        print(f"[SALES-AGENT] category filter skipped: {_ce}")

    # STEP4_SA_FOLLOWUP: let the reply step see this customer's saved follow-up plan (None when the
    # feature is off or there is none, so nothing changes in that case).
    try:
        _note = prod._pending_followup_note(customer_id, workspace_id, wa_number)
    except Exception:
        _note = None
    if _note:
        verified_data["followup_note"] = _note

    # DIWALI_ICP: festival requests show ONLY in-stock ICP products, MAX_CARDS per reply;
    # "more" shows the next page.
    _dmode = False
    _key = (workspace_id, customer_id)
    _st = _TOPIC.get(_key) or {}
    _live = bool(_st) and (time.time() - _st.get("t", 0) < 1800)
    _wm = understanding.get("wants_more")
    _is_more = (_wm is True) if isinstance(_wm, bool) else bool(MORE_RE.search(message or ""))
    if _is_more and (understanding.get("color_hint") or understanding.get("category_hint")):
        _is_more = False  # names a new colour/category: a fresh search, not paging
    _is_diwali = (str(understanding.get("collection") or "").lower() == "diwali") or bool(DIWALI_RE.search(message or ""))
    if not handoff and (_is_diwali or (_is_more and _live)):
        _icp = _diwali_products()
        _start = _st.get("offset", 0) if (_is_more and _live and not _is_diwali) else 0
        _page = _icp[_start:_start + MAX_CARDS]
        if not _page:
            _TOPIC.pop(_key, None)
            return _result(understanding, "That's all the Diwali pieces we have right now. Would you like to see kurthis or other sets?")
        _TOPIC[_key] = {"offset": _start + len(_page), "t": time.time()}
        verified_data["products"] = _page
        understanding["intent"] = "PRODUCT_DISCOVERY"
        _dmode = True
    elif not _is_more:
        _TOPIC.pop(_key, None)
    cards = []
    if (understanding.get("intent") in CARD_INTENTS
            and not handoff
            and verified_data.get("products")):
        cards = _build_cards(verified_data["products"], exclude_skus=([] if _dmode else already_shown))

    # ── Call 2: write the reply from the real data ──
    reply, _was_valid = agent.generate_reply(
        ((("Customer asks for the Diwali collection (spell it Diwali). Original message: " + message) if _dmode else message) + CARDS_INSTRUCTION) if cards else message,
        understanding, verified_data, history)

    if not cards and reply.lower().startswith("sorry, i'm having trouble"):
        _mark_failed()
        return None  # second Groq call failed: let the old code answer

    if not cards:
        return _result(understanding, reply)

    intro = reply
    low = intro.lower()
    if any(t in low for t in ("rs.", "\u20b9", "sku", "http")) or low.startswith("sorry, i'm having trouble"):
        intro = _fallback_intro(message)
    full_text = intro + "\n\n" + "\n\n".join(c["text"] for c in cards)
    log_text = intro + "\n(Shown: " + ", ".join(c["name"] + " (" + c["sku"] + ")" for c in cards) + ")"
    images = [{"sku": c["sku"], "name": c["name"], "image": c["image"]} for c in cards if c["image"]]

    # Remember what was shown, and record interest - same helpers the old
    # suggestion code uses, with the same workspace scoping.
    shown = [c["sku"] for c in cards if c["sku"]]
    if shown:
        prod.remember_skus(customer_id, shown, workspace_id=workspace_id, wa_number=wa_number)
    category = prod.detect_category(message)
    for c in cards:
        prod.log_customer_interest(customer_id, c["sku"], c["name"], category, workspace_id=workspace_id)

    out = _result(understanding, full_text,
                  image=images[0]["image"] if images else None,
                  images=images, log_text=log_text)
    out["intro"] = intro
    out["cards"] = cards
    return out
