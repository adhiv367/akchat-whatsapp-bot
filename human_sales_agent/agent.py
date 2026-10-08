import json
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from llm_client import chat_completion
from tools import product_search, order_lookup, memory, validation, policy

# STEP8B: 'repeats the same question' is NOT a handoff trigger here; the bridge raises a LOW alert for that.
UNDERSTAND_SYSTEM_PROMPT = """You are analyzing a WhatsApp message to a women's boutique (Invi Creation).
Given the customer's message and recent conversation history, extract structured signals.
Reply with ONLY valid JSON, no other text, in this exact shape:
{
  "intent": "GREETING|PRODUCT_DISCOVERY|PRODUCT_AVAILABILITY|PRICE_ENQUIRY|SIZE_ENQUIRY|COLOR_ENQUIRY|OBJECTION|ORDER_STATUS|COMPLAINT|BUYING_INTENT|HESITATION|COMPANY_INFO|CATEGORY_OVERVIEW|NEW_ARRIVALS|ORDER_CHANGE|OTHER",
  "referenced_product_hint": "short text describing what product the customer means, from context, or null",
  "category_hint": "kurthi|salwar|maxi|co-ord|null",
  "color_hint": "string or null",
  "budget_hint": "number or null",
  "size_hint": "string or null",
  "search_query": "a short natural-language query to search the product catalog with, or null if not a product question",
  "wants_more": true/false,
  "refers_to_shown": true/false,
"collection": "diwali" or null,
"handoff_required": true/false,
  "handoff_reason": "string or null"
}
How to choose the intent - read the WHOLE message and the conversation, never react to a single word:
- COMPANY_INFO: the customer wants to know about the shop itself (its address, location, phone number, email, website, or how to visit). The words "address", "office", "details" or "about" on their own do NOT make a message COMPANY_INFO. Example: "show me office wear kurthi" is a product request, and "change the delivery address on my order" is ORDER_CHANGE.
- CATEGORY_OVERVIEW: the customer asks, in general, what kinds of products or categories the shop has.
- NEW_ARRIVALS: the customer wants to see the newest products, the new collection or the latest designs.
- ORDER_STATUS: the customer asks where their existing order is, its status or its tracking.
- ORDER_CHANGE: the customer wants to change, cancel or correct something on an existing order (delivery address, phone number, size, cancellation). Also set handoff_required=true.
- For questions about a product, its fabric, price, size or colour, use the matching PRODUCT_ / PRICE_ / SIZE_ / COLOR_ intent and fill in search_query.
Set handoff_required=true for ANY of these — check carefully, do not miss this:
- complaints, damaged/refund requests, angry tone
- customer explicitly asks for a human, a real person, an agent, a team member, or says things like "talk to someone", "connect me to a person", "I want to speak to staff"
- customer says the AI/bot is not helping
wants_more: true ONLY when the customer is asking to see more, other or further items beyond those already shown in the recent conversation, in any wording or language (for example: show other dresses, anything else, still more, in Tamil too). false when "more" means something else (more details, more expensive, more sizes) or when no items were shown yet.
refers_to_shown: true ONLY when the customer clearly points at a specific product that was ALREADY shown earlier in the recent conversation, in any wording or language (for example: "this one", "that one", "the same one", "show me that red kurthi again", "send me the dress you showed earlier", "I like the second one"). The message itself must contain a pointing word (this, that, these, same, again, earlier, before, first, second, the one you showed); a message that only names a colour or category is false even if such a product was shown earlier. false for every normal browsing or new request, even when it names a colour or a category (for example "show me red kurthi", "show me salwar", "suggest a dress for my friend", "show me party wear"), and false when nothing has been shown yet.
collection: "diwali" when the customer asks for the Diwali, festival, festive or pandigai collection or festive wear, in any spelling or language; otherwise null.

This is a safety-critical check. When in doubt about whether a message requests a human, set handoff_required=true rather than false.
Never guess facts — this step is understanding intent only, not answering."""

REPLY_SYSTEM_PROMPT = """You are a warm, experienced human sales executive at Invi Creation, a women's cotton-wear boutique.
Reply naturally and conversationally, like a real staff member on WhatsApp - not like a scripted bot.
Keep replies short and human. Use Tamil/Tanglish or English to match the customer's language.
You are given VERIFIED PRODUCT/ORDER/POLICY DATA below. You must NEVER state a price, stock status, size availability,
delivery date, discount, or policy that is not explicitly present in this verified data.
If the data doesn't answer the question, say so naturally and ask a clarifying question, or say you'll check.
Do not repeat information already given earlier in the conversation unless asked again.
You will be told whether this message has been flagged for human handoff. If HANDOFF FLAGGED is true, you must NEVER claim or imply that you are already a human or a real person. Instead, acknowledge the customer's request/situation naturally and let them know a team member will follow up with them shortly. Do not try to resolve the issue yourself in this case.
When the customer's request has NO usable detail at all (no color, no category, no product reference, no occasion) - for example "something for my sister" or "I need a dress" with nothing else - you MUST do BOTH of these in the same reply:
1. Ask ONE natural question about what type/style they (or the person they're shopping for) want.
2. ALSO show up to 3 real products from VERIFIED PRODUCT DATA below, if any were retrieved, as example options.
Do not assume their preference or the preference of anyone they're shopping for - frame the products as examples to react to, not as your recommendation of what suits them.
If the customer already gave ANY specific detail (a color, an occasion, a style word, a budget), search and show real matching products directly, without asking about type first.
If VERIFIED POLICY/FAQ DATA is present below and the customer's question relates to it (delivery, returns, payment, COD, care instructions, store hours), answer using that data. If it's empty and they ask a policy question, say you'll confirm and get back to them - never guess a policy.
FORMATTING RULES (WhatsApp does not render Markdown):
- For bold text use a single asterisk on each side, like *this*, never **this**.
- Never use image markdown like ![alt](url). If you need to mention an image, just describe it in words.
- Do not use markdown headers (#), numbered markdown lists with dashes are fine as plain text (e.g. "1. Product Name").
- Keep formatting simple: plain text, single-asterisk bold, emojis, line breaks - nothing else.
- Do not describe or narrate what an image would look like (no "picture a..." or "imagine..." lines). Just describe the product in words normally.
- Never include raw image URLs or links in your reply text. Images are sent separately as attachments, not as text.

CRITICAL FACTUALITY RULE:
- If VERIFIED PRODUCT DATA below is empty or says "(no matching products found)", you have ZERO information
  about any specific product's availability, stock, color options, or price.
- In that case you must NOT say a product is/isn't available, in stock, or out of stock, and you must NOT
  name a specific product as unavailable. Instead, ask the customer to clarify which product they mean,
  or say you'll check and confirm shortly.
- Only ever state availability, stock status, color, or price for a product that literally appears in
  VERIFIED PRODUCT DATA below."""
def _build_history_block(history):
    if not history:
        return "(no prior conversation)"
    lines = []
    for direction, text in history:
        speaker = "Customer" if direction == "incoming" else "You"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _keyword_handoff_check(message, parsed):
    """Deterministic safety net (SALES_AGENT_PATCH): an explicit request for a human is
    ALWAYS flagged, whether or not the Groq understanding call worked."""
    explicit_human_phrases = [
        "talk to a real person", "talk to a human", "speak to a real person",
        "speak to a human", "connect me to a person", "connect me to an agent",
        "talk to someone", "speak to someone", "talk to your team",
        "want to speak to staff", "real agent", "human agent", "customer care",
        "speak to a representative", "talk to a representative",
    ]
    msg_lower = message.lower()
    if any(p in msg_lower for p in explicit_human_phrases):
        parsed["handoff_required"] = True
        parsed["handoff_reason"] = "Customer explicitly requested a human/agent (keyword override)"
    return parsed


def understand(message, customer_id, workspace_id=None, wa_number=None):
    history = memory.get_recent_conversation(customer_id, workspace_id=workspace_id, wa_number=wa_number)
    history_block = _build_history_block(history)

    user_prompt = f"Recent conversation:\n{history_block}\n\nNew customer message: {message}"
    ok, raw = chat_completion(
        [
            {"role": "system", "content": UNDERSTAND_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=600,
        temperature=0.1,
    )
    if not ok:
        return _keyword_handoff_check(message, {
            "intent": "OTHER", "referenced_product_hint": None, "category_hint": None,
            "color_hint": None, "budget_hint": None, "size_hint": None,
            "search_query": None, "handoff_required": False, "handoff_reason": None,
            "_error": raw,
        }), history

    try:
        cleaned = raw.strip().strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        parsed = json.loads(cleaned)
    except Exception as e:
        parsed = {
            "intent": "OTHER", "referenced_product_hint": None, "category_hint": None,
            "color_hint": None, "budget_hint": None, "size_hint": None,
            "search_query": None, "handoff_required": False, "handoff_reason": None,
            "_error": f"JSON parse failed: {e}, raw={raw!r}",
        }
        # Deterministic safety net — gpt-oss-20b unreliably catches explicit
    # human-handoff requests via prompt instruction alone. This guarantees
    # it regardless of model behavior, since this is safety-critical.
    parsed = _keyword_handoff_check(message, parsed)

    return parsed, history


def gather_verified_data(understanding, customer_id, workspace_id=None, wa_number=None, max_products=5):  # NO_REPEAT: max_products
    """Deterministic step — no LLM. Pulls real data based on the LLM's
    understanding, so the reply step can never invent facts.

    Extended for the bridge merge: also pulls product IMAGES (so a WhatsApp
    reply can carry a real photo, same as the old ai_bridge_phase2 keyword
    branches did) and POLICY/FAQ data (delivery, returns, COD, care
    instructions), grounded in the same real sources — nothing invented."""
    data = {"products": [], "orders": [], "images": [], "policy": []}

    if understanding.get("intent") == "ORDER_STATUS":
        data["orders"] = order_lookup.lookup_order_by_phone(customer_id, workspace_id=workspace_id)

    # Fall back to the referenced-product hint when there's no explicit
    # search query — covers objections/follow-ups like "this is too expensive"
    # or "is this available in medium" where the customer means something
    # already discussed, not a fresh search.
    query = understanding.get("search_query") or understanding.get("referenced_product_hint")
    if query:
        products = product_search.load_products()
        _k = max_products if max_products > 5 else 3  # NEW_PRODUCT_PRIORITY: only the wide request (30) widens the pool; the default stays 3
        results = product_search.search(query, products, top_k=_k)
        if not results:
            results = product_search.semantic_search_products(query, top_k=_k, workspace_id=workspace_id)
        data["products"] = results[:max_products]

        # Real product photos for whatever the text search matched on —
        # same get_top_product_images() production already uses for its
        # own suggestion replies, just reused here read-only.
        try:
            data["images"] = product_search.get_top_product_images(query, products, top_k=5)
        except Exception as e:
            print(f"[AGENT2] get_top_product_images failed: {e}")
            data["images"] = []

    # Policy/FAQ grounding — only runs a lookup when the message actually
    # contains a policy-ish keyword, same gating production's
    # search_policy_faq() already applies internally.
    try:
        data["policy"] = policy.search_policy_faq(understanding.get("_raw_message", "") or "", workspace_id=workspace_id)
    except Exception as e:
        print(f"[AGENT2] search_policy_faq failed: {e}")
        data["policy"] = []

    return data


def generate_reply(message, understanding, verified_data, history):
    history_block = _build_history_block(history)
    products_block = "\n---\n".join(verified_data["products"]) if verified_data["products"] else "(no matching products found)"
    orders_block = json.dumps(verified_data["orders"]) if verified_data["orders"] else "(no order data)"
    policy_block = "\n---\n".join(verified_data.get("policy") or []) if verified_data.get("policy") else "(no policy data retrieved)"

    handoff_flag = understanding.get("handoff_required", False)
    # STEP4_SA_FOLLOWUP: the customer's saved "I'll buy it next Friday" plan, when there is one.
    _note = (verified_data or {}).get("followup_note")
    plan_block = ("\n\nCUSTOMER'S SAVED PLAN (from earlier in this chat; mention it only if the customer asks "
                  "what they said they would buy or when, or it is directly relevant):\n" + _note) if _note else ""
    user_prompt = f"""Recent conversation:
{history_block}

Customer's new message: {message}

Detected intent: {understanding.get('intent')}

HANDOFF FLAGGED: {handoff_flag}

VERIFIED PRODUCT DATA:
{products_block}

VERIFIED ORDER DATA:
{orders_block}

VERIFIED POLICY/FAQ DATA:
{policy_block}{plan_block}

Write the sales reply now."""

    ok, reply = chat_completion(
        [
            {"role": "system", "content": REPLY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=700,
        temperature=0.5,
    )
    if not ok:
        return "Sorry, I'm having trouble right now. Please give me a moment! 😊", False

    is_valid, final_reply = validation.validate_reply(reply, verified_data["products"], [], product_search.load_products())

    # ISOLATED extra guard for the Human Sales Agent only — production
    # validate_reply() only catches SKU-anchored stock claims. This catches
    # general availability/stock claims made with zero retrieved product data,
    # which production's validator does not check.
    if is_valid and not verified_data["products"]:
        no_data_stock_phrases = [
            "don't have", "do not have", "out of stock", "not in stock",
            "not available", "unavailable", "sold out", "no longer available",
            "not seeing", "don't see", "do not see", "not in our", "not in our current",
            "couldn't find", "could not find", "no such product", "not in the list",
            "not in our list", "not in our catalog", "doesn't exist in",
        ]
        reply_lower = final_reply.lower()
        if any(p in reply_lower for p in no_data_stock_phrases):
            print(f"[AGENT2-SAFETY] Blocked ungrounded availability claim with zero product data: {final_reply!r}")
            return ("I want to double-check that for you — could you tell me the product name or color "
                    "you're asking about? I'll confirm availability right away! 😊"), False

    if handoff_flag:
        fake_human_phrases = [
            "i'm a real person", "i am a real person", "i'm a human", "i am a human",
            "i'm a team member", "i am a team member", "as a real person", "as a human",
        ]
        reply_lower = final_reply.lower()
        if any(p in reply_lower for p in fake_human_phrases):
            print(f"[AGENT2-SAFETY] Blocked dishonest handoff claim: {final_reply!r}")
            return "Thanks for letting me know - I've noted this, and one of our team members will follow up with you shortly!", False

    return final_reply, is_valid


def handle_message(message, customer_id):
    """Full pipeline. Returns a structured debug dict — never exposes raw
    chain-of-thought, only the structured signals requested."""
    import time
    understanding, history = understand(message, customer_id)
    understanding["_raw_message"] = message  # used by gather_verified_data() for policy lookup only
    time.sleep(3)  # space out the two LLM calls to stay under free-tier RPM limits
    verified_data = gather_verified_data(understanding, customer_id)
    reply, was_valid = generate_reply(message, understanding, verified_data, history)

    memory.log_message(customer_id, "incoming", message)
    memory.log_message(customer_id, "outgoing", reply)

    return {
        "intent": understanding.get("intent"),
        "context_used": bool(history),
        "referenced_product_hint": understanding.get("referenced_product_hint"),
        "search_query": understanding.get("search_query"),
        "products_retrieved": len(verified_data["products"]),
        "orders_retrieved": len(verified_data["orders"]),
        "images_retrieved": len(verified_data.get("images") or []),
        "policy_chunks_retrieved": len(verified_data.get("policy") or []),
        "reply": reply,
        "reply_passed_validation": was_valid,
        "handoff_required": understanding.get("handoff_required", False),
        "handoff_reason": understanding.get("handoff_reason"),
        "verified_data": verified_data,
        "_understanding_raw": understanding,
    }
