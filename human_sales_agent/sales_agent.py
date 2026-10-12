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

# NO_REPEAT: a customer must not be shown the same product twice while browsing.
SHOWN_MEMORY_LIMIT = 1000   # how many remembered SKUs to read back (the bridge default is only 15)
WIDE_POOL = 30              # search candidates looked at once this customer has already seen products
ALREADY_SEEN_REPLY = ("You've already seen all the matching designs I have for this \U0001F60A "
                      "Would you like to see another category, a different colour or style, or our new arrivals?")
ALREADY_SEEN_NEW_REPLY = ("You've already seen all our current new arrivals \U0001F60A "
                          "Would you like to see a different category, colour or style?")
# Used ONLY when the AI's own "refers_to_shown" flag is missing (e.g. the understand step failed).
REFERENCE_RE = _re.compile(
    r"\b(?:again|same one|this one|that one|the one you|you (?:showed|sent|shared)|(?:showed|sent|shown) earlier)\b",
    _re.I)


# NO_REPEAT guard: an English message can only be a reference if it contains a pointing word.
POINTING_RE = _re.compile(
    r"\b(?:this|that|these|those|same|again|earlier|before|previous|last|first|second|third|fourth|fifth|"
    r"showed|sent|shared|shown|the one)\b", _re.I)


def _norm_sku(s):
    return (s or "").strip().rstrip("*").upper()


def _get_shown(customer_id, workspace_id, wa_number):
    """NO_REPEAT: every SKU remembered for this customer (not just the bridge's default 15)."""
    try:
        return prod.get_recent_skus(customer_id, workspace_id=workspace_id, wa_number=wa_number,
                                    limit=SHOWN_MEMORY_LIMIT) or []
    except TypeError:
        # bridge without the new `limit` argument yet: behave exactly as before
        return prod.get_recent_skus(customer_id, workspace_id=workspace_id, wa_number=wa_number) or []


def _is_reference(understanding, message):
    """NO_REPEAT: True when the customer points at a product they were already shown
    ("this one", "that red kurthi again"). The AI decides (refers_to_shown); the regex is only
    a fallback when that flag is absent - same pattern as wants_more."""
    m = message or ""
    flag = understanding.get("refers_to_shown")
    if isinstance(flag, bool):
        if flag and m.isascii() and not POINTING_RE.search(m):
            return False  # NO_REPEAT guard: "show me red kurthi" is browsing, whatever the model said
        return flag
    return bool(REFERENCE_RE.search(m)) and not MORE_RE.search(m)


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
    """Products tagged temp-new, Available only. NO_REPEAT: SKUs this customer was already
    shown are left out (dropped, not moved to the end)."""
    exclude = {_norm_sku(s) for s in (exclude_skus or [])}
    out = []
    for p in product_search.load_products():
        t = p.get("text", "")
        tags = next((ln for ln in t.split("\n") if ln.lower().startswith("tags:")), "")
        _tag_list = [x.strip() for x in tags.split(":", 1)[-1].lower().split(",")]  # STEP6C: exact tag, not a substring
        if NEW_ARRIVAL_TAG in _tag_list and _is_available(t) and _norm_sku(_text_sku(t)) not in exclude:
            out.append(t)
    return out[:limit]


# NEW_PRODUCT_PRIORITY: newest products first. Dates live in Postgres (bridge: {handle: time});
# here they are translated to {SKU: time} because the cards and the shown-history work with SKUs.
def _added_map():
    try:
        by_handle = prod.get_added_at_map()
        if not by_handle:
            return {}
        out = {}
        for p in product_search.load_products():
            h = p.get("id") or ""
            if h in by_handle:
                sku = _norm_sku(_text_sku(p.get("text", "")))
                if sku:
                    out[sku] = by_handle[h]
        return out
    except Exception as e:
        print(f"[SALES-AGENT] added_at map skipped: {e}")
        return {}


def _added_ts(value):
    try:
        import datetime as _dt
        d = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except Exception:
        return 0.0


def _newest_first(texts, color=None, query=None):
    """Reorder only, never drop. Relevance first (the words the customer asked for beyond the
    generic category words, e.g. 'red', matched as whole words), then newest first. Products with
    no date count as oldest and keep their incoming order."""
    texts = list(texts or [])
    try:
        added = _added_map()
        words = set()
        for w in _re.findall(r"[a-z]+", ((query or "") + " " + (color or "")).lower()):
            if len(w) > 1 and w not in GENERIC_WORDS:
                words.add(w)
        pats = [_re.compile(r"\b" + _re.escape(w) + r"\b") for w in words]

        def _key(t):
            body = "\n".join(ln for ln in t.split("\n") if not ln.lower().startswith("image:")).lower()
            rel = sum(1 for p in pats if p.search(body))
            d = added.get(_norm_sku(_text_sku(t)))
            return (rel, _added_ts(d) if d else 0.0)

        return sorted(texts, key=_key, reverse=True)
    except Exception as e:
        print(f"[SALES-AGENT] newest-first skipped: {e}")
        return texts


def _build_cards(product_texts, exclude_skus=None, max_cards=MAX_CARDS):
    """One card per product, same layout as the SKU lookup reply. Only the
    last card keeps the 'To order...' footer. Values come straight from the
    existing product data - nothing is invented."""
    # NO_REPEAT: SKUs this customer was already shown are dropped, never moved to the back.
    exclude = {_norm_sku(s) for s in (exclude_skus or [])}
    available = [t for t in product_texts
                 if _is_available(t) and _norm_sku(_text_sku(t)) not in exclude]
    cards, seen = [], set()
    for t in available:
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
# STEP10_RECOVER: conservative check, used ONLY when the Groq "understand" step has failed.
# Plain browse / suggest / gift messages get product cards (no LLM); anything that mentions
# orders, refunds, offers etc. still goes to the old code exactly as before.
_DISCOVERY_RE = _re.compile(
    r"\b(suggest|recommend|gift|show me|dress(?:es)?|kurt(?:a|i|ha|hi)s?|salwar|co-?ords?|designs?|options?)\b",
    _re.I)
_NOT_DISCOVERY_RE = _re.compile(
    r"\b(?:order|refund|cancel|return|exchange|track|status|complain|delay|damag|wrong|pay|paid|invoice|discount|offer|coupon|cod|deliver)",
    _re.I)


def _looks_like_discovery(message):
    m = message or ""
    return bool(_DISCOVERY_RE.search(m)) and not _NOT_DISCOVERY_RE.search(m)


def _mark_failed():
    # SALES_AGENT_PATCH2: tells /ai the AI steps failed, so the old keyword rules that
    # misfire on words like "about" / "my order" are skipped for this message.
    try:
        from flask import g
        g.sales_agent_failed = True
    except Exception:
        pass


# STEP11_NO_DEAD_END: fresh dresses (or a restart) instead of "you have already seen these"
FILL_MORE_REPLY = "Here are some fresh designs you haven't seen yet \U0001F60A"
FILL_NEW_REPLY = ("You've seen all our current new arrivals \U0001F60A "
                  "Here are some other fresh designs you haven't seen yet:")
FILL_RESTART_REPLY = ("You've now seen all our designs \U0001F60A "
                      "Here they are again from the start:")


def _is_test_product(text):
    sku = _norm_sku(_text_sku(text))
    tags = next((ln for ln in text.split("\n") if ln.lower().startswith("tags:")), "")
    tag_list = [x.strip() for x in tags.split(":", 1)[-1].lower().split(",")]
    return sku.startswith("TEST") or "test" in tag_list


def _fill_text(message, english):
    # Tamil customers keep the existing Tamil intro
    if any("\u0b80" <= ch <= "\u0bff" for ch in (message or "")):
        return _fallback_intro(message)
    return english


def _fresh_fill(shown, matching, limit=MAX_CARDS):
    # Returns (product_texts, "fresh" | "restart") or None when nothing can be shown.
    # fresh   = in-stock, non-test products this customer has not seen: same group first, newest first.
    # restart = every in-stock product was already seen: oldest-shown first, same group first.
    try:
        seen = {_norm_sku(s) for s in (shown or [])}
        by_sku = {}
        for p in product_search.load_products():
            t = p.get("text", "")
            sku = _norm_sku(_text_sku(t))
            if not sku or sku in by_sku or _is_test_product(t) or not _is_available(t):
                continue
            by_sku[sku] = t
        types = {_type_of(t) for t in (matching or []) if _type_of(t)}

        def _group_first(texts):
            same = [t for t in texts if _type_of(t) in types]
            return same + [t for t in texts if _type_of(t) not in types]

        fresh = [t for sku, t in by_sku.items() if sku not in seen]
        if fresh:
            return _group_first(_newest_first(fresh))[:limit], "fresh"
        order = []
        for s in reversed(list(shown or [])):  # shown list is most-recent-first
            n = _norm_sku(s)
            if n in by_sku and n not in order:
                order.append(n)
        again = [by_sku[n] for n in order]
        if again:
            return _group_first(again)[:limit], "restart"
        return None
    except Exception as e:
        print("[SALES-AGENT] STEP11 fill skipped: %s" % e)
        return None


def run(message, customer_id, workspace_id, wa_number):
    # Fixed rule: SKU messages are handled by the existing SKU code in /ai.
    if SKU_PATTERN.search(message):
        return None
    # Never run without a resolved workspace (multi-tenant safety).
    if not workspace_id:
        print("[SALES-AGENT] STEP10 no workspace_id resolved - handing the message to the old code")
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
    # NO_REPEAT: is the customer pointing at a product they were already shown?
    _ref = (not handoff) and _is_reference(understanding, message)

    _recovered = False  # STEP10_RECOVER
    if understanding.get("_error"):
        print(f"[SALES-AGENT] understand step failed: {understanding['_error']}")
        if handoff:
            # The keyword safety net still works when Groq is down.
            return _result(understanding, HANDOFF_FALLBACK_REPLY)
        if _looks_like_discovery(message):
            # STEP10_RECOVER: Groq failed but this is a plain browse/gift request, so answer with
            # product cards (no LLM) instead of the old text template.
            understanding["intent"] = "PRODUCT_DISCOVERY"
            understanding["search_query"] = message
            intent = "PRODUCT_DISCOVERY"
            _recovered = True
            print("[SALES-AGENT] STEP10 recovering a discovery message without the LLM")
        else:
            print("[SALES-AGENT] STEP10 understand failed, not a discovery message - old code answers")
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
    _fill_reply = None  # STEP11: fixed intro when fresh/restart dresses replace a dead end
    already_shown = _get_shown(customer_id, workspace_id, wa_number)  # NO_REPEAT: was limited to 15

    if intent == "NEW_ARRIVALS" and not handoff:
        new_products = _new_arrival_products(exclude_skus=([] if _ref else already_shown),
                                             limit=(WIDE_POOL if _ref else MAX_CARDS))
        _diwali_msg = (str(understanding.get("collection") or "").lower() == "diwali") or bool(DIWALI_RE.search(message or ""))  # STEP11
        if not new_products:
            if already_shown and not _ref and _new_arrival_products(limit=1):
                # NO_REPEAT: there ARE new arrivals, this customer has simply seen them all
                # STEP11: show fresh dresses (or restart) instead of stopping. A Diwali request is
                # not trapped here: it falls through to the Diwali block below.
                _fill = None if _diwali_msg else _fresh_fill(already_shown, _new_arrival_products(limit=1000))
                if _fill:
                    new_products, _kind = _fill
                    _fill_reply = _fill_text(message, FILL_NEW_REPLY if _kind == "fresh" else FILL_RESTART_REPLY)
                elif not _diwali_msg:
                    return _result(understanding, ALREADY_SEEN_NEW_REPLY)
            elif not _diwali_msg:
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
        # NO_REPEAT: once this customer has seen products, look at a wider candidate list so that
        # dropping the already-seen ones still leaves unseen matches. Fresh customers: call unchanged.
        _kw = {"max_products": WIDE_POOL} if intent in CARD_INTENTS else {}  # NEW_PRODUCT_PRIORITY: every category request, incl. brand-new customers
        verified_data = agent.gather_verified_data(
            understanding, customer_id, workspace_id=workspace_id, wa_number=wa_number, **_kw)

    # CATEGORY_FILTER: a plain category request ("show me kurthis") must return that category only.
    try:
        if intent in CARD_INTENTS and not handoff and not _fill_reply:  # STEP11
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
                    verified_data["products"] = _pool  # NO_REPEAT: was _pool[:10]; trimmed to the cards below
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
    _all_seen = False
    if (understanding.get("intent") in CARD_INTENTS
            and not handoff
            and verified_data.get("products")):
        _cands = verified_data["products"]
        _seen_set = {_norm_sku(s) for s in already_shown}
        if _dmode or _fill_reply:  # STEP11: fill products are already fresh/restart, no exclusion
            cards = _build_cards(_cands, exclude_skus=[])
        elif _ref and _seen_set:
            # NO_REPEAT exception: the customer points at something already shown, so show that
            # product again. If none of the results was shown before, treat them as normal results.
            _prev = [t for t in _cands if _norm_sku(_text_sku(t)) in _seen_set]
            cards = _build_cards(_prev or _cands, exclude_skus=[])
        else:
            # NO_REPEAT: browsing / new request - products already shown are dropped.
            _cands = _newest_first(_cands, understanding.get("color_hint"), understanding.get("search_query") or understanding.get("referenced_product_hint"))  # NEW_PRODUCT_PRIORITY: relevance first, then newest; the shown filter comes after
            cards = _build_cards(_cands, exclude_skus=already_shown)
            _all_seen = (not cards) and any(_is_available(t) for t in _cands)
        # the reply step should only see the products that are actually being shown
        if cards:
            _on_cards = {_norm_sku(c["sku"]) for c in cards}
            verified_data["products"] = [t for t in _cands if _norm_sku(_text_sku(t)) in _on_cards] or _cands[:MAX_CARDS]
        else:
            verified_data["products"] = _cands[:MAX_CARDS]

    if _all_seen:
        # STEP11: show fresh dresses (or restart) instead of stopping; old message if nothing can be filled
        _fill = _fresh_fill(already_shown, _cands)
        if _fill:
            _ftexts, _kind = _fill
            cards = _build_cards(_ftexts, exclude_skus=[])
            if cards:
                _fill_reply = _fill_text(message, FILL_MORE_REPLY if _kind == "fresh" else FILL_RESTART_REPLY)
                _on_cards = {_norm_sku(c["sku"]) for c in cards}
                verified_data["products"] = [t for t in _ftexts if _norm_sku(_text_sku(t)) in _on_cards] or _ftexts[:MAX_CARDS]
                _all_seen = False

    # STEP13: a bare "anything else" / "more" with nothing to show (the search had no query, or the
    # intent was not a card intent) but this customer has already been shown products: continue with
    # unseen dresses from the whole catalog (or restart), never a dead end or a free-text category question.
    if (not cards and not _all_seen and not _fill_reply and not handoff and not _ref
            and already_shown and understanding.get("wants_more") is True
            and not understanding.get("color_hint") and not understanding.get("category_hint")
            and understanding.get("intent") in (CARD_INTENTS | {"OTHER"})):
        _fill = _fresh_fill(already_shown, [])
        if _fill:
            _ftexts, _kind = _fill
            cards = _build_cards(_ftexts, exclude_skus=[])
            if cards:
                _fill_reply = _fill_text(message, FILL_MORE_REPLY if _kind == "fresh" else FILL_RESTART_REPLY)
                _on_cards = {_norm_sku(c["sku"]) for c in cards}
                verified_data["products"] = [t for t in _ftexts if _norm_sku(_text_sku(t)) in _on_cards] or _ftexts[:MAX_CARDS]
                understanding["intent"] = "PRODUCT_DISCOVERY"

    if _all_seen:
        # STEP14: last-resort fill. _fresh_fill returned nothing (error or empty), so show the first
        # available, non-test products instead of a dead end. Same cards, same five-card maximum.
        try:
            _last = []
            for _p in product_search.load_products():
                _t = _p.get("text", "")
                if _is_available(_t) and not _is_test_product(_t) and _norm_sku(_text_sku(_t)):
                    _last.append(_t)
                if len(_last) >= MAX_CARDS:
                    break
            cards = _build_cards(_last, exclude_skus=[]) if _last else []
        except Exception as _e:
            print("[SALES-AGENT] STEP14 last-resort fill failed: %s" % _e)
            cards = []
        if cards:
            _fill_reply = _fill_text(message, FILL_RESTART_REPLY)
            _on_cards = {_norm_sku(c["sku"]) for c in cards}
            verified_data["products"] = [t for t in _last if _norm_sku(_text_sku(t)) in _on_cards] or _last[:MAX_CARDS]
            understanding["intent"] = "PRODUCT_DISCOVERY"
            _all_seen = False
    if _all_seen:
        # NO_REPEAT (Option A): everything that matches was already shown - do not repeat it.
        print("[SALES-AGENT] NO_REPEAT all matching products were already shown to this customer")
        return _result(understanding, ALREADY_SEEN_REPLY)

    # ── Call 2: write the reply from the real data ──
    if _recovered and not cards:
        # STEP10_RECOVER: nothing to show, so behave exactly as before (old code answers)
        print("[SALES-AGENT] STEP10 recovery found no available products - handing over to the old code")
        _mark_failed()
        return None
    if _fill_reply:
        reply, _was_valid = _fill_reply, True  # STEP11: fixed text, no LLM call
    elif _recovered:
        reply, _was_valid = _fallback_intro(message), True  # STEP10_RECOVER: no second LLM call
    else:
        reply, _was_valid = agent.generate_reply(
            ((("Customer asks for the Diwali collection (spell it Diwali). Original message: " + message) if _dmode else message) + CARDS_INSTRUCTION) if cards else message,
            understanding, verified_data, history)

    if not cards and reply.lower().startswith("sorry, i'm having trouble"):
        print("[SALES-AGENT] STEP10 second LLM call failed and there are no cards - old code answers")
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
