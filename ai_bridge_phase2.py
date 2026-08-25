from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import requests
import os
import re

# PHASE 2: added for persistent memory (replaces in-memory recent_suggestions dict)
import psycopg2
from psycopg2.extras import execute_values

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
    return [text for _, text in scores[:top_k]]
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

def build_suggestion_reply(query, products, top_k=5, exclude_skus=None):
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
def ask_groq(query, context, conversation_history=None):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    # PHASE 2: format recent turns as a simple transcript, if any exist.
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

{history_block}Relevant Products:
{chr(10).join(context)}

Customer Message: {query}

Instructions:
- Reply friendly and short like a boutique staff on WhatsApp
- If customer says hi or hello, greet them and introduce Invi Creation
- If the recent conversation above answers part of the question (e.g. a price or product already mentioned), use it — don't ask the customer to repeat themselves
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


@app.route("/ai", methods=["POST"])
def ai_reply():
    data = request.json
    message = data.get("message", "").strip()
    customer_id = data.get("customer_id", "")
    if not message:
        return jsonify({"reply": "", "image": None, "type": "text"})

    # PHASE 2: log every incoming message. This is a plain append — it never
    # affects which branch below runs, so quick replies/SKU/suggestions all
    # behave exactly as before.
    log_message(customer_id, "incoming", message)

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
    if any(kw in msg_lower for kw in suggest_keywords):
        already_shown = get_recent_skus(customer_id)
        reply, top_image, image_list = build_suggestion_reply(message, products, top_k=5, exclude_skus=already_shown)
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
    # ── 5. General question — call Groq ──
    context = search(message, products)
    conversation_history = get_recent_conversation(customer_id)  # PHASE 2
    reply = ask_groq(message, context, conversation_history)     # PHASE 2: history added
    print(f"[GROQ] {message}")
    log_message(customer_id, "outgoing", reply)  # PHASE 2
    return jsonify({
        "reply": reply,
        "image": None,
        "type": "text"
    })
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
