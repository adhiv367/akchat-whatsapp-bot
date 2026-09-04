from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import requests
import os
import re

# PHASE 2: added for persistent memory (replaces in-memory recent_suggestions dict)
import psycopg2
from psycopg2.extras import execute_values

# PHASE 1: for semantic product search embeddings
import google.generativeai as genai

# PHASE 2: load GROQ_API_KEY / DATABASE_URL from a .env file in this same
# folder, so they no longer depend on which PowerShell window you typed
# $env: commands into. Create a .env file here with:
#   GROQ_API_KEY=...
#   DATABASE_URL=...
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
CORS(app)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# PHASE 1: needed for semantic_search_products()'s embedding calls
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# PHASE 2: DATABASE_URL must be set on Render (same Postgres your AK Chat
# backend already uses — Settings → Environment on the Render service).
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_db_conn():
    """PHASE 2: single place to open a DB connection. Never throws upward —
    every caller below treats a failed connection the same way the old code
    treated a fresh/empty dict: just proceeds with no memory for this turn,
    rather than crashing the whole request."""
    if not DATABASE_URL:
        return None
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"[DB] connection failed: {e}")
        return None


# PHASE 2: get_recent_skus / remember_skus now read/write Postgres instead of
# the old `recent_suggestions = {}` dict. Same function names, same call
# sites below — nothing else in the file needed to change for this part.

def get_recent_skus(customer_id):
    if not customer_id:
        return []
    conn = get_db_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sku FROM coexistence.shown_products
                WHERE customer_id = %s
                ORDER BY shown_at DESC
                LIMIT 15
                """,
                (customer_id,),
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"[DB] get_recent_skus failed: {e}")
        return []
    finally:
        conn.close()


def remember_skus(customer_id, skus):
    if not customer_id or not skus:
        return
    conn = get_db_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO coexistence.shown_products (customer_id, sku) VALUES %s",
                [(customer_id, sku) for sku in skus],
            )
        conn.commit()
    except Exception as e:
        print(f"[DB] remember_skus failed: {e}")
        conn.rollback()
    finally:
        conn.close()


# PHASE 2: new — conversation memory for the Groq fallback path only.
# Every incoming/outgoing message logs here; get_recent_conversation() pulls
# the last few turns for this customer so Groq can see context like
# "you said the price was X earlier".

def log_message(customer_id, direction, text):
    if not customer_id or not text:
        return
    conn = get_db_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO coexistence.conversation_messages (customer_id, direction, message_text)
                VALUES (%s, %s, %s)
                """,
                (customer_id, direction, text),
            )
        conn.commit()
    except Exception as e:
        print(f"[DB] log_message failed: {e}")
        conn.rollback()
    finally:
        conn.close()


def get_recent_conversation(customer_id, limit=6):
    """Returns the last `limit` messages for this customer, oldest first,
    formatted for dropping straight into the Groq prompt. Empty list if
    there's no history or the DB is unreachable — Groq just gets no extra
    context in that case, same as today's behavior."""
    if not customer_id:
        return []
    conn = get_db_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT direction, message_text FROM coexistence.conversation_messages
                WHERE customer_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (customer_id, limit),
            )
            rows = cur.fetchall()
        rows.reverse()  # oldest first, for a natural-reading transcript
        return [(direction, text) for direction, text in rows]
    except Exception as e:
        print(f"[DB] get_recent_conversation failed: {e}")
        return []
    finally:
        conn.close()


# ── Cache for common greetings — no Groq tokens used ──
QUICK_REPLIES = {
    "hi":        "Vanakkam! Welcome to Invi Creation 👗 Pure cotton kurthi, kurthi sets & salwar sets. Rs.725-1899. Sizes XS-5XL.\n\nWhat are you looking for today?",
    "hii":       "Vanakkam! Welcome to Invi Creation 👗 Pure cotton kurthi, kurthi sets & salwar sets. Rs.725-1899. Sizes XS-5XL.\n\nWhat are you looking for today?",
    "hiii":      "Vanakkam! Welcome to Invi Creation 👗 Pure cotton kurthi, kurthi sets & salwar sets. Rs.725-1899. Sizes XS-5XL.\n\nWhat are you looking for today?",
    "hello":     "Hello! Welcome to Invi Creation 👗 Pure cotton kurthi, kurthi sets & salwar sets. Rs.725-1899. Sizes XS-5XL.\n\nHow can I help you?",
    "hey":       "Hey! Welcome to Invi Creation 👗 Pure cotton kurthi & salwar sets. Rs.725-1899. Sizes XS-5XL.\n\nWhat are you looking for?",
    "heyy":      "Hey! Welcome to Invi Creation 👗 Pure cotton kurthi & salwar sets. Rs.725-1899. Sizes XS-5XL.\n\nWhat are you looking for?",
    "vanakkam":  "Vanakkam! Invi Creation-la vanga 👗 Pure cotton kurthi, kurthi sets, salwar sets. Rs.725 mudhal. Enna vennum?",
    "hai":       "Vanakkam! Welcome to Invi Creation 👗 Pure cotton kurthi & salwar sets. Rs.725-1899. Sizes XS-5XL.\n\nWhat are you looking for?",
    "ok":        "Sure! Tell me what you are looking for — kurthi, kurthi set, or salwar set? I will show you the best options 😊",
    "okay":      "Sure! Tell me what you are looking for — kurthi, kurthi set, or salwar set? I will show you the best options 😊",
    "thanks":    "Thank you for contacting Invi Creation 😊 Feel free to message anytime!",
    "thank you": "Thank you for contacting Invi Creation 😊 Feel free to message anytime!",
    "bye":       "Thank you for visiting Invi Creation 😊 Come back anytime! Have a great day!",
}

# ── Company details ──
COMPANY_KEYWORDS = [
    "company", "address", "location", "where are you", "contact",
    "phone", "email", "website", "about", "shop", "store",
    "kadai", "enge irukeenga", "details", "info", "office"
]

COMPANY_REPLY = """🏪 *Invi Creation*

🌐 Website: https://www.invicreation.com
📞 Phone: 9751100905
📧 Email: invi0905@gmail.com

📍 Address:
144, Vellakkal Medu, Post,
Near Manjal Vaniga Valagam, Nasiyanur,
Near Standard Roofs,
Erode - 638107, Tamil Nadu, India

🕐 Feel free to visit us or order online!
For orders, just tell us the product SKU and your size 😊"""


def load_products():
    with open("invi_products.json", "r", encoding="utf-8") as f:
        return json.load(f)
def save_products(products):
    with open("invi_products.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

def shopify_product_to_doc(product):
    """Convert a Shopify webhook product payload into our RAG document format"""
    handle = product.get('handle', '')
    title = product.get('title', '')
    product_type = product.get('product_type', '')
    tags = product.get('tags', '')
    status = product.get('status', 'active')
    published_at = product.get('published_at')
    variants = product.get('variants', [])
    images = product.get('images', [])

    price = str(variants[0].get('price', '')) if variants else ''
    all_skus = ', '.join(sorted(set(
        v.get('sku', '').strip().upper() for v in variants if v.get('sku')
    )))
    sku = title.split('PI :')[-1].strip() if 'PI :' in title else (
        variants[0].get('sku', '') if variants else ''
    )
    total_stock = sum(int(v.get('inventory_quantity') or 0) for v in variants)
    image = images[0].get('src', '') if images else ''
    in_stock = total_stock > 0
    is_published = (status == 'active') and (published_at is not None)

    doc_text = f"""Product: {title}
Handle: {handle}
SKU: {sku}
All SKUs: {all_skus}
Type: {product_type}
Price: Rs.{price}
Fabric: cotton
Available Sizes: XS,S,M,L,XL,2XL,3XL,4XL,5XL
Sleeve Options: short,long,sleeveless
Neckline Options: round,v-neck,square
Care Instructions: hand wash
Tags: {tags}
Image: {image}
Status: {'Available' if in_stock else 'Out of Stock'}"""

    return {'id': handle, 'text': doc_text}, is_published


def find_product_by_sku(sku, products):
    """Find product by SKU — checks both SKU: field and All SKUs: field"""
    sku_upper = sku.upper().strip()
    for p in products:
        text = p["text"].upper()
        # Check SKU: line (base sku) OR All SKUs: line (all size variants)
        if f"SKU: {sku_upper}" in text or f", {sku_upper}," in text or f": {sku_upper}," in text or text.find(f", {sku_upper}\n") != -1 or f"ALL SKUS: {sku_upper}" in text or f", {sku_upper}" in text:
            return p
    return None


def parse_product_details(product):
    """Extract structured details from product text"""
    text = product["text"]
    details = {}

    for line in text.split("\n"):
        if ": " in line:
            key, value = line.split(": ", 1)
            details[key.strip()] = value.strip()

    return details

def build_product_reply(details):
    """Build a clean WhatsApp-style product reply"""
    name = details.get("Product", "Product")
    sku = details.get("SKU", "")
    price = details.get("Price", "")
    fabric = details.get("Fabric", "")
    sizes = details.get("Available Sizes", "")
    sleeve = details.get("Sleeve Options", "")
    neckline = details.get("Neckline Options", "")
    status = details.get("Status", "Available")
    image = details.get("Image", "")
      # Get handle from product data directly
    handle = details.get("Handle", "") or details.get("handle", "")
    if not handle:
        handle = sku.lower().replace(" ", "-") if sku else ""
    product_link = f"https://www.invicreation.com/products/{handle}" if handle else "https://www.invicreation.com"
    reply = f"""👗 *{name}*
     
🏷️ SKU: {sku}
💰 Price: {price}
🧵 Fabric: {fabric}
📏 Sizes: {sizes.upper()}
👚 Sleeve: {sleeve}
👔 Neckline: {neckline}
✅ Status: {status}
🔗 View & Order: {product_link}

To order, reply with your *size* and we will process it! 😊"""

    return reply, image if image else None

def search(query, products, top_k=3):
    query_words = set(query.lower().split())
    scores = []
    for p in products:
        doc_words = set(p["text"].lower().split())
        score = len(query_words.intersection(doc_words))
        scores.append((score, p["text"]))
    scores.sort(reverse=True)
    # Only keep genuine matches — zero-overlap "matches" are noise that
    # confuses Groq into mixing unrelated products with real history
    return [text for score, text in scores[:top_k] if score > 0]
# PHASE 1: semantic fallback — only used when the plain keyword search()
# above finds zero matches. This catches synonym-style queries ("crimson
# kurthi" finding "Crimson Bloom Cotton Kurthi") that keyword overlap
# alone would miss, without changing behavior for anything that already
# works via keywords.
def semantic_search_products(query, top_k=3):
    conn = get_db_conn()
    if not conn:
        return []
    try:
        vector = embed_text(query)
        vector_literal = "[" + ",".join(str(v) for v in vector) + "]"
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_text FROM coexistence.knowledge_chunks kc
                JOIN coexistence.knowledge_documents kd ON kd.id = kc.document_id
                WHERE kd.source_type = 'product' AND kc.workspace_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (1, vector_literal, top_k),
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"[PHASE1] semantic_search_products failed: {e}")
        return []
    finally:
        conn.close()

def embed_text(text):
    """PHASE 1: same embedding + truncation approach as ingest_document.py
    and sync_products_to_pg.py — gemini-embedding-001 outputs 3072 dims,
    the knowledge_chunks.embedding column is 1536-dim."""
    result = genai.embed_content(model="models/gemini-embedding-001", content=text)
    full_vector = result["embedding"]
    truncated = full_vector[:1536]
    norm = sum(v * v for v in truncated) ** 0.5
    return [v / norm for v in truncated] if norm > 0 else truncated
def get_top_product_images(query, products, top_k=5):
    """Return list of matching products with sku, name, and image for suggestion replies"""
    query_words = set(query.lower().split())
    scores = []
    for p in products:
        doc_words = set(p["text"].lower().split())
        score = len(query_words.intersection(doc_words))
        scores.append((score, p))
    scores.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, p in scores[:top_k]:
        details = parse_product_details(p)
        image = details.get("Image", "")
        sku = details.get("SKU", "")
        name = details.get("Product", "")
        if image:
            results.append({"sku": sku, "name": name, "image": image})
    return results
DRESS_KEYWORDS = ["dress", "dresses", "kurthi", "kurthis", "salwar", "outfit", "clothes"]

# PHASE 5: policy/FAQ keywords for detecting a second intent alongside a
# product question (e.g. "is it available AND can I get delivery by Friday")
POLICY_KEYWORDS = [
    "delivery", "deliver", "ship", "shipping", "return", "refund", "exchange",
    "cash on delivery", "cod", "payment", "pay by", "store hours", "store timing",
    "how long", "when will i get", "dry clean", "wash", "care instructions",
]
def extract_prices(text):
    """PHASE 6: pulls out price-like values (Rs.789, ₹5,000, etc.) from
    text, normalized to plain numbers for comparison."""
    matches = re.findall(r'(?:Rs\.?|₹)\s?([\d,]+(?:\.\d+)?)', text, re.IGNORECASE)
    prices = set()
    for m in matches:
        try:
            prices.add(float(m.replace(',', '')))
        except ValueError:
            pass
    return prices
AVAILABILITY_POSITIVE_WORDS = ["available", "in stock", "ready to ship", "yes, it's available", "still available"]
AVAILABILITY_NEGATIVE_WORDS = ["out of stock", "unavailable", "sold out", "not available", "no longer available"]

def check_stock_claims(reply, products):
    """PHASE 6: finds any SKU mentioned in Groq's reply, looks up its real
    status, and checks whether the reply's wording contradicts it — e.g.
    claiming something is available when the real data says Out of Stock."""
    reply_lower = reply.lower()
    sku_matches = re.findall(r'\b(ICK\d+|ICS\d+|ICC\d+)\b', reply, re.IGNORECASE)
    for sku in set(m.upper() for m in sku_matches):
        product = find_product_by_sku(sku, products)
        if not product:
            continue
        details = parse_product_details(product)
        real_status = details.get("Status", "")
        claims_available = any(w in reply_lower for w in AVAILABILITY_POSITIVE_WORDS)
        claims_unavailable = any(w in reply_lower for w in AVAILABILITY_NEGATIVE_WORDS)
        if real_status == "Out of Stock" and claims_available and not claims_unavailable:
            print(f"[PHASE6] Reply claims {sku} is available, but real status is Out of Stock")
            return False
    return True


def validate_reply(reply, context, policy_context=None, products=None):
    """PHASE 6: checks Groq's reply against real data before it's sent.
    Two independent checks — either can trigger the safe fallback."""
    if products and not check_stock_claims(reply, products):
        return False, "Let me confirm current availability for you and get right back to you! 😊"

    reply_prices = extract_prices(reply)
    if not reply_prices:
        return True, reply  # nothing more to check
    context_text = "\n".join(context) + "\n" + "\n".join(policy_context or [])
    context_prices = extract_prices(context_text)
    unverified = [p for p in reply_prices if p not in context_prices]
    if unverified:
        print(f"[PHASE6] Unverified price(s) in reply: {unverified} — using safe fallback")
        return False, "Let me double-check that price for you and get right back to you! In the meantime, feel free to browse our collection at https://www.invicreation.com 😊"
    return True, reply

def search_policy_faq(message, limit=2):
    """PHASE 5: simple keyword search against the FAQ/policy knowledge base
    (built in Phase 1). No embeddings, no extra API calls — just a plain
    database search, matching this bot's existing style of preferring
    simple rules over AI where possible."""
    msg_lower = message.lower()
    matched_terms = [kw for kw in POLICY_KEYWORDS if kw in msg_lower]
    if not matched_terms:
        return []
    conn = get_db_conn()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            conditions = " OR ".join(["chunk_text ILIKE %s"] * len(matched_terms))
            params = [f"%{term}%" for term in matched_terms]
            cur.execute(
                f"SELECT DISTINCT chunk_text FROM coexistence.knowledge_chunks "
                f"WHERE workspace_id = %s AND ({conditions}) LIMIT %s",
                [1] + params + [limit],
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"[PHASE5] search_policy_faq failed: {e}")
        return []
    finally:
        conn.close()

# PHASE 3: color words for interest tracking (category detection reuses
# the existing CATEGORY_WORDS already defined inside build_suggestion_reply)
COLOR_WORDS = [
    "red", "blue", "green", "yellow", "pink", "purple", "black", "white",
    "orange", "maroon", "wine", "navy", "grey", "gray", "ivory", "mustard",
    "olive", "mint", "indigo", "rust", "forest",
]


def detect_color(text):
    text_lower = text.lower()
    for c in COLOR_WORDS:
        if c in text_lower:
            return c
    return None


def log_customer_interest(customer_id, product_sku, product_name, category):
    print(f"[DEBUG] log_customer_interest called: customer_id={customer_id}, sku={product_sku}, category={category}")
    """PHASE 3: records that this customer was shown a product, for later
    matching against new arrivals. Fail-quiet, same as other DB helpers."""
    if not customer_id or not category:
        return
    color = detect_color(product_name)
    conn = get_db_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO coexistence.customer_interests
                    (workspace_id, customer_number, product_sku, product_category, product_color)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, customer_number, product_category, product_color)
                    WHERE status = 'open'
                    DO NOTHING
                """,
                (1, customer_id, product_sku, category, color),
            )
        conn.commit()
    except Exception as e:
        print(f"[DB] log_customer_interest failed: {e}")
        conn.rollback()
    finally:
        conn.close()


def build_suggestion_reply(query, products, top_k=5, exclude_skus=None, customer_id=None):
    """Pick real distinct products and build a clean suggestion reply directly (no Groq)"""
    query_lower = query.lower()
    query_words = set(query_lower.split())

    CATEGORY_WORDS = {
        "maxi": ["maxi"],
        "kurthi": ["kurthi", "kurta", "kurti"],
        "salwar": ["salwar", "suit set", "ethnic wear set"],
        "co-ord": ["co-ord", "coord", "co ord"],
    }
    matched_category = None
    for cat, words in CATEGORY_WORDS.items():
        if any(w in query_lower for w in words):
            matched_category = cat
            break

    generic_request = any(w in query_lower for w in ["dress", "dresses", "outfit", "clothes", "collection"])

    scores = []
    for p in products:
        text_lower = p["text"].lower()
        doc_words = set(text_lower.split())
        score = len(query_words.intersection(doc_words))
        if matched_category:
            cat_words = CATEGORY_WORDS[matched_category]
            if any(w in text_lower for w in cat_words):
                score += 10  # strong boost for matching the specific category asked
            else:
                score -= 5   # push non-matching categories down
        elif generic_request:
            score += 1
        scores.append((score, p))
    scores.sort(key=lambda x: x[0], reverse=True)

    exclude_skus = exclude_skus or []
    seen_names = set()
    picked = []
    fallback = []  # in case we run out of fresh options
    for score, p in scores:
        details = parse_product_details(p)
        name = details.get("Product", "").split("-PI")[0].strip()
        sku = details.get("SKU", "")
        if name in seen_names:
            continue
        if not details.get("Image"):
            continue
        if details.get("Status", "") == "Out of Stock":
            continue
        seen_names.add(name)
        if sku in exclude_skus:
            fallback.append(details)  # save as backup, don't show first
            continue
        picked.append(details)
        if len(picked) >= top_k:
            break
    # If not enough fresh products, fill remaining slots from fallback (already-shown ones)
    if len(picked) < top_k:
        picked.extend(fallback[:top_k - len(picked)])

    for details in picked:
        log_customer_interest(customer_id, details.get("SKU", ""), details.get("Product", ""), matched_category)

    if not picked:
        return "Sorry, I couldn't find matching products right now. You can browse our full collection at https://www.invicreation.com 😊", None, []

    lines = ["Sure! Here are some lovely options for you 💃:\n"]
    images = []
    for i, details in enumerate(picked, 1):
        name = details.get("Product", "").split("-PI")[0].strip()
        sku = details.get("SKU", "")
        price = details.get("Price", "")
        sizes = details.get("Available Sizes", "")
        handle = details.get("Handle", "")
        link = f"https://www.invicreation.com/products/{handle}" if handle else "https://www.invicreation.com"
        lines.append(f"{i}. 👗 *{name}*\n🏷️ SKU: {sku}\n💰 Price: {price}\n📏 Sizes: {sizes.upper()}\n🔗 View & Order: {link}\n")
        images.append({"sku": sku, "name": name, "image": details.get("Image", "")})

    lines.append("Reply with the SKU and your size to order! 😊")
    reply_text = "\n".join(lines)
    return reply_text, images[0]["image"] if images else None, images
def build_collection_overview_reply(products):
    """Warm intro + one representative product per category"""
    intro = ("Hi! 👋 Welcome to *Invi Creation* — your go-to boutique for pure cotton women's wear.\n\n"
              "Here's what we have:\n"
              "👗 Kurthis\n"
              "🥻 Salwar Suit Sets\n"
              "👘 Maxi Dresses\n\n"
              "Here's a peek at each category:\n")
    categories = [
        ("kurthi", ["kurthi", "kurta", "kurti"]),
        ("salwar", ["salwar", "suit set", "ethnic wear set"]),
        ("maxi", ["maxi"]),
    ]
    lines = [intro]
    images = []
    count = 0
    for cat_name, cat_words in categories:
        best = None
        for p in products:
            text_lower = p["text"].lower()
            if any(w in text_lower for w in cat_words):
                details = parse_product_details(p)
                if details.get("Status", "") == "Out of Stock":
                    continue
                if not details.get("Image"):
                    continue
                best = details
                break
        if best:
            count += 1
            name = best.get("Product", "").split("-PI")[0].strip()
            sku = best.get("SKU", "")
            price = best.get("Price", "")
            sizes = best.get("Available Sizes", "")
            handle = best.get("Handle", "")
            link = f"https://www.invicreation.com/products/{handle}" if handle else "https://www.invicreation.com"
            lines.append(f"{count}. 👗 *{name}*\n🏷️ SKU: {sku}\n💰 Price: {price}\n📏 Sizes: {sizes.upper()}\n🔗 View & Order: {link}\n")
            images.append({"sku": sku, "name": name, "image": best.get("Image", "")})
    lines.append("Reply with the SKU and your size to order, or tell me what you're looking for! 😊")
    reply_text = "\n".join(lines)
    return reply_text, images[0]["image"] if images else None, images

# PHASE 2: ask_groq now accepts optional conversation_history and includes it
# in the prompt when present. Callers that don't pass it (there are none
# currently, but this keeps the function backward-compatible) behave exactly
# as before.
def ask_groq(query, context, conversation_history=None, policy_context=None):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    # PHASE 2: format recent turns as a simple transcript, if any exist.
    policy_block = ""
    if policy_context:
        policy_block = "Relevant store policy/FAQ info:\n" + "\n---\n".join(policy_context) + "\n\n"
    history_block = ""
    if conversation_history:
        lines = []
        for direction, text in conversation_history:
            speaker = "Customer" if direction == "incoming" else "You"
            lines.append(f"{speaker}: {text}")
        history_block = "Recent conversation with this customer:\n" + "\n".join(lines) + "\n\n"

    prompt = f"""You are a WhatsApp shopping assistant for Invi Creation, a women's boutique.
Website: https://www.invicreation.com
Phone: 9751100905

{history_block}{policy_block}Relevant Products:
{chr(10).join(context)}

Customer Message: {query}

Instructions:
- Reply friendly and short like a boutique staff on WhatsApp
- If customer says hi or hello, greet them and introduce Invi Creation
- If the recent conversation above answers part of the question (e.g. a price or product already mentioned), use it exactly as stated there — don't substitute a different product from "Relevant Products" below, even if it seems similar
- If customer asks to suggest, show, or recommend dresses — show 5 products like this format EXACTLY:

1. 👗 *Product Name*
🏷️ SKU: ICK00XXX
💰 Price: Rs.XXX
📏 Sizes: XS to 5XL
🔗 View & Order: https://www.invicreation.com/products/[handle]

2. 👗 *Product Name*
...and so on for 5 products

- If customer gives a SKU like ICK00133, show ONLY that product details
- Always use the exact Handle from product data for the View & Order link
- Never make up products not in the context
- Reply in same language as customer (Tamil or English)
- End with: Reply with the SKU and your size to order! 😊

Answer:"""

    body = {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 800
    }
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=headers,
        json=body
    )
    result = response.json()
    if 'choices' not in result:
        print("GROQ ERROR RESPONSE:", result)
        return "Sorry, I'm having trouble right now. Please try again in a moment."
    return result['choices'][0]['message']['content']
# ── Customer Intent / Lead Intelligence ──────────────────────────────────
# Ordered highest-signal to lowest — first match wins, so "how to order"
# is caught before it could ever be treated as generic browsing.
INTENT_SIGNAL_TIERS = [
    ("SUPPORT", ["complaint", "problem", "issue", "damaged", "wrong item",
                 "not working", "refund", "exchange", "return"], None),
    ("EXISTING_CUSTOMER", ["order status", "where is my order", "tracking",
                            "delivery status", "my order", "track my order"], None),
    ("PURCHASE_INTENT", ["how to order", "want this", "want to buy",
                          "i want", "book this", "confirm order", "place order"], 90),
    ("HOT_LEAD", ["cod", "cash on delivery", "delivery", "deliver",
                  "available", "in stock", "size", "color"], 65),
    ("WARM_LEAD", ["price", "cost", "how much", "rate"], 50),
    ("PRODUCT_INTEREST", ["like this", "similar", "design", "vera",
                           "maari", "designs"], 30),
]

def classify_intent(message):
    """Rule-based message-level intent classification — same simple-rules
    style as POLICY_KEYWORDS/CATEGORY_WORDS_MATCH elsewhere in this file."""
    msg_lower = message.lower()
    for intent, keywords, score in INTENT_SIGNAL_TIERS:
        if any(kw in msg_lower for kw in keywords):
            return intent, score, 0.8
    return "JUST_BROWSING", 10, 0.5


def score_to_label(score):
    if score >= 85:
        return "PURCHASE_INTENT"
    if score >= 65:
        return "HOT_LEAD"
    if score >= 40:
        return "WARM_LEAD"
    if score >= 20:
        return "PRODUCT_INTEREST"
    return "JUST_BROWSING"


def update_customer_score(wa_number, contact_number, message, detected_intent, signal_score, confidence):
    """Blends this message's signal with the customer's existing score, so
    one message doesn't wildly swing their classification, while repeated
    behavior still shifts them Cold -> Warm -> Hot over time."""
    conn = get_db_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_intent, buying_score
                FROM coexistence.customer_intent_profiles
                WHERE workspace_id = %s AND wa_number = %s AND contact_number = %s
                """,
                (1, wa_number, contact_number),
            )
            row = cur.fetchone()
            old_intent = row[0] if row else "JUST_BROWSING"
            old_score = row[1] if row else 0

            if detected_intent in ("SUPPORT", "EXISTING_CUSTOMER"):
                new_score = old_score
                new_intent = detected_intent
            else:
                effective_signal = signal_score if signal_score is not None else 10
                new_score = max(0, min(100, round(0.6 * effective_signal + 0.4 * old_score)))
                new_intent = score_to_label(new_score)

            cur.execute(
                """
                INSERT INTO coexistence.customer_intent_profiles
                    (workspace_id, wa_number, contact_number, current_intent,
                     buying_score, intent_confidence, last_activity, last_intent_update)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (workspace_id, wa_number, contact_number) DO UPDATE SET
                    current_intent = EXCLUDED.current_intent,
                    buying_score = EXCLUDED.buying_score,
                    intent_confidence = EXCLUDED.intent_confidence,
                    last_activity = NOW(),
                    last_intent_update = NOW(),
                    updated_at = NOW()
                """,
                (1, wa_number, contact_number, new_intent, new_score, confidence),
            )

            if new_intent != old_intent or new_score != old_score:
                cur.execute(
                    """
                    INSERT INTO coexistence.customer_intent_history
                        (workspace_id, wa_number, contact_number, previous_intent,
                         new_intent, previous_score, new_score, trigger_message)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (1, wa_number, contact_number, old_intent, new_intent, old_score, new_score, message[:500]),
                )
        conn.commit()
    except Exception as e:
        print(f"[INTENT] update_customer_score failed: {e}")
        conn.rollback()
    finally:
        conn.close()


@app.route("/ai", methods=["POST"])
def ai_reply():
    data = request.json
    message = data.get("message", "").strip()
    customer_id = data.get("customer_id", "")
    wa_number = data.get("wa_number", "")
    if not message:
        return jsonify({"reply": "", "image": None, "type": "text"})

    # PHASE 2: log every incoming message. This is a plain append — it never
    # affects which branch below runs, so quick replies/SKU/suggestions all
    # behave exactly as before.
    log_message(customer_id, "incoming", message)

    detected_intent, signal_score, confidence = classify_intent(message)
    update_customer_score(wa_number, customer_id, message, detected_intent, signal_score, confidence)
    print(f"[INTENT] {customer_id}: {detected_intent} (signal={signal_score}, wa_number={wa_number})")
    msg_lower = message.lower().strip()

    # ── 1. Greeting cache — no Groq call ──
    if msg_lower in QUICK_REPLIES:
        print(f"[CACHE] {message}")
        reply_text = QUICK_REPLIES[msg_lower]
        log_message(customer_id, "outgoing", reply_text)  # PHASE 2
        return jsonify({
            "reply": reply_text,
            "image": None,
            "type": "text"
        })

    # ── 2. Company details ──
    if any(kw in msg_lower for kw in COMPANY_KEYWORDS):
        print(f"[COMPANY] {message}")
        log_message(customer_id, "outgoing", COMPANY_REPLY)  # PHASE 2
        return jsonify({
            "reply": COMPANY_REPLY,
            "image": None,
            "type": "text"
        })

    # ── 3. SKU detection — send image + details ──
    sku_match = re.search(r'\b(ICK\d+|ICS\d+|ICC\d+)\b', message, re.IGNORECASE)
    if sku_match:
        sku = sku_match.group(1).upper()
        products = load_products()
        product = find_product_by_sku(sku, products)
        if product:
            details = parse_product_details(product)
            reply, image_url = build_product_reply(details)
            print(f"[SKU] {sku} → {details.get('Product', '')}")
            log_message(customer_id, "outgoing", reply)  # PHASE 2
            return jsonify({
                "reply": reply,
                "image": image_url,
                "type": "product"
            })
        else:
            not_found_reply = f"Sorry, I could not find product *{sku}*. Please check the SKU and try again. You can browse our collection at https://www.invicreation.com 😊"
            log_message(customer_id, "outgoing", not_found_reply)  # PHASE 2
            return jsonify({
                "reply": not_found_reply,
                "image": None,
                "type": "text"
            })
       
    products = load_products()

    # ── 4a. "What collection do you have" — warm intro + one item per category ──
    collection_overview_keywords = ["what collection", "collections do you have", "what do you have",
                                     "what all", "what products", "what items", "categories"]
    if any(kw in msg_lower for kw in collection_overview_keywords):
        reply, top_image, image_list = build_collection_overview_reply(products)
        print(f"[OVERVIEW] {message} -> {len(image_list)} categories")
        log_message(customer_id, "outgoing", reply)  # PHASE 2
        return jsonify({
            "reply": reply,
            "image": top_image,
            "images": image_list,
            "type": "product" if top_image else "text"
        })

    # ── 4b. Dress/collection suggestion — build directly from real data, no Groq ──
    suggest_keywords = ["suggest", "show me", "recommend", "options", "collection"] + DRESS_KEYWORDS
    has_policy_question = any(kw in msg_lower for kw in POLICY_KEYWORDS)  # PHASE 5
    if any(kw in msg_lower for kw in suggest_keywords) and not has_policy_question:  # PHASE 5: fall through to Groq if a policy question is also present
        already_shown = get_recent_skus(customer_id)
        reply, top_image, image_list = build_suggestion_reply(message, products, top_k=5, exclude_skus=already_shown, customer_id=customer_id)
        shown_skus = [item["sku"] for item in image_list]
        remember_skus(customer_id, shown_skus)
        print(f"[SUGGEST] {message} (customer={customer_id}) -> {len(image_list)} products, excluded {len(already_shown)}")
        log_message(customer_id, "outgoing", reply)  # PHASE 2
        return jsonify({
            "reply": reply,
            "image": top_image,
            "images": image_list,
            "type": "product" if top_image else "text"
        })
    # -- 5. General question -- call Groq --
    context = search(message, products)
    if not context:
        # PHASE 1: plain keyword search found nothing -- try semantic
        # search before giving up, catches synonym-style queries.
        context = semantic_search_products(message)
        if context:
            print("[PHASE1] Semantic fallback matched for: " + message)
    policy_context = search_policy_faq(message)  # PHASE 5
    conversation_history = get_recent_conversation(customer_id)  # PHASE 2
    reply = ask_groq(message, context, conversation_history, policy_context)  # PHASE 5: policy added
    is_valid, reply = validate_reply(reply, context, policy_context, products)  # PHASE 6
    print(f"[GROQ] {message}" + ("" if is_valid else " [PHASE6: blocked unverified price]"))
    log_message(customer_id, "outgoing", reply)  # PHASE 2
    return jsonify({
        "reply": reply,
        "image": None,
        "type": "text"
    })
CATEGORY_WORDS_MATCH = {
    "maxi": ["maxi"],
    "kurthi": ["kurthi", "kurta", "kurti"],
    "salwar": ["salwar", "suit set", "ethnic wear set"],
    "co-ord": ["co-ord", "coord", "co ord"],
}

NODE_BACKEND_URL = os.environ.get("NODE_BACKEND_URL", "http://localhost:3011")


def detect_category(text):
    text_lower = text.lower()
    for cat, words in CATEGORY_WORDS_MATCH.items():
        if any(w in text_lower for w in words):
            return cat
    return None


INTEREST_TEMPLATE_ID = os.environ.get("INTEREST_TEMPLATE_ID")  # set once Meta approves the template

def prepare_followup_for_interest(interest_id, customer_number, category, color, sku, product_name, product_handle):
    """PHASE 3: creates a DRAFT broadcast for manual review — never sends
    automatically. Marks the interest 'match_found' (NOT 'contacted' —
    that only happens once a human actually clicks Send in the real
    Broadcast UI, which is outside this bot's control entirely)."""
    if not INTEREST_TEMPLATE_ID:
        print("[PHASE3] INTEREST_TEMPLATE_ID not set yet — skipping, template pending Meta approval")
        return

    product_link = f"https://www.invicreation.com/products/{product_handle}" if product_handle else "https://www.invicreation.com"
    variable_mapping = {
        "1": f"{color} {category}",
        "2": product_name,
        "3": product_link,
    }
    try:
        resp = requests.post(
            NODE_BACKEND_URL + "/api/internal/prepare-followup",
            json={
                "customer_number": customer_number,
                "template_id": int(INTEREST_TEMPLATE_ID),
                "name": f"Interest follow-up: {customer_number} — {color} {category}",
                "variable_mapping": variable_mapping,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            conn = get_db_conn()
            if conn:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE coexistence.customer_interests SET status = %s, matched_sku = %s, updated_at = NOW() WHERE id = %s",
                            ("match_found", sku, interest_id),
                        )
                    conn.commit()
                    print(f"[PHASE3] Draft ready for review: {customer_number} — {sku}")
                finally:
                    conn.close()
        else:
            print(f"[PHASE3] Prepare failed for interest {interest_id}: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[PHASE3] Prepare error for interest {interest_id}: {e}")


def notify_matching_interests(sku, product_name, doc_text):
    category = detect_category(doc_text)
    color = detect_color(doc_text)
    if not category or not color:
        return
    handle_match = [line for line in doc_text.split("\n") if line.startswith("Handle:")]
    product_handle = handle_match[0].split(":", 1)[1].strip() if handle_match else ""
    conn = get_db_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, customer_number FROM coexistence.customer_interests WHERE workspace_id = %s AND status = %s AND product_category = %s AND product_color = %s",
                (1, "open", category, color),
            )
            rows = cur.fetchall()
    except Exception as e:
        print(f"[PHASE3] notify_matching_interests query failed: {e}")
        rows = []
    finally:
        conn.close()

    for interest_id, customer_number in rows:
        prepare_followup_for_interest(interest_id, customer_number, category, color, sku, product_name, product_handle)
@app.route("/shopify/product-created", methods=["POST"])
def shopify_product_created():
    data = request.json
    product = data.get('product') or data
    doc, is_published = shopify_product_to_doc(product)
    if not is_published:
        print(f"[SHOPIFY] Skipped unpublished product: {doc['id']}")
        return jsonify({"status": "skipped - not published"})
    products = load_products()
    existing_ids = [p['id'] for p in products]
    if doc['id'] not in existing_ids:
        products.append(doc)
        save_products(products)
        print(f"[SHOPIFY] Added new product: {doc['id']}")
        details = parse_product_details(doc)
        notify_matching_interests(details.get("SKU", ""), details.get("Product", doc['id']), doc["text"])
        return jsonify({"status": "added", "id": doc['id']})
    print(f"[SHOPIFY] Product already exists: {doc['id']}")
    return jsonify({"status": "already exists"})


@app.route("/shopify/product-updated", methods=["POST"])
def shopify_product_updated():
    data = request.json
    product = data.get('product') or data
    doc, is_published = shopify_product_to_doc(product)
    products = load_products()
    found = False
    for i, p in enumerate(products):
        if p['id'] == doc['id']:
            if is_published:
                products[i] = doc
            else:
                products.pop(i)
            found = True
            break
    if not found and is_published:
        products.append(doc)
    save_products(products)
    print(f"[SHOPIFY] Updated product: {doc['id']} (published={is_published})")
    if is_published:
        details = parse_product_details(doc)
        notify_matching_interests(details.get("SKU", ""), details.get("Product", doc['id']), doc["text"])
    return jsonify({"status": "updated"})


@app.route("/shopify/product-deleted", methods=["POST"])
def shopify_product_deleted():
    data = request.json
    handle = data.get('handle') or str(data.get('id', ''))
    products = load_products()
    before = len(products)
    products = [p for p in products if p['id'] != handle]
    save_products(products)
    print(f"[SHOPIFY] Deleted product: {handle} ({before} -> {len(products)})")
    return jsonify({"status": "deleted", "remaining": len(products)})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "AI bridge running"})


if __name__ == "__main__":
    print("=" * 60)
    print("AI Bridge PHASE 2 started on port 5050")
    print(f"DATABASE_URL loaded: {'YES - ' + DATABASE_URL[:30] + '...' if DATABASE_URL else 'NO (missing!)'}")
    print(f"GROQ_API_KEY loaded: {'YES' if GROQ_API_KEY else 'NO (missing!)'}")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5050, debug=False)
