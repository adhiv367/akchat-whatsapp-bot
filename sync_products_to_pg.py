"""
sync_products_to_pg.py — embeds every product from invi_products.json into
the pgvector knowledge base (coexistence.knowledge_documents / knowledge_chunks),
tagged as source_type='product', so products become semantically searchable
(e.g. "red kurthi" also matching "crimson kurta") alongside the existing
FAQ/policy content ingested by ingest_document.py.

Reuses the same .env (GEMINI_API_KEY, DATABASE_URL) and the same embedding
truncation approach as ingest_document.py (gemini-embedding-001 outputs
3072 dims; the knowledge_chunks.embedding column is 1536-dim, so we
truncate + re-normalize).

Safe to re-run: existing product documents (by SKU) are deleted and
re-inserted, so this can be run again after invi_products.json changes.

Usage:
    python sync_products_to_pg.py
"""

import json
import os
import re

from dotenv import load_dotenv
import psycopg2
import google.generativeai as genai

WORKSPACE_ID = 1  # Invi Creation
EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBED_DIM = 1536


def embed(text):
    result = genai.embed_content(model=EMBEDDING_MODEL, content=text)
    full_vector = result["embedding"]
    truncated = full_vector[:EMBED_DIM]
    norm = sum(v * v for v in truncated) ** 0.5
    return [v / norm for v in truncated] if norm > 0 else truncated


def to_vector_literal(values):
    return "[" + ",".join(str(v) for v in values) + "]"


def extract_sku(text):
    match = re.search(r"^SKU:\s*(\S+)", text, re.MULTILINE)
    return match.group(1) if match else None


def main():
    load_dotenv()

    gemini_key = os.environ.get("GEMINI_API_KEY")
    db_url = os.environ.get("DATABASE_URL")
    if not gemini_key or not db_url:
        print("GEMINI_API_KEY and DATABASE_URL must both be set in .env")
        return

    genai.configure(api_key=gemini_key)

    with open("invi_products.json", "r", encoding="utf-8") as f:
        products = json.load(f)

    print(f"Loaded {len(products)} products from invi_products.json")

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    try:
        # Remove previously-synced product documents so re-runs don't duplicate.
        cur.execute(
            "DELETE FROM coexistence.knowledge_documents "
            "WHERE workspace_id = %s AND source_type = 'product'",
            (WORKSPACE_ID,),
        )
        deleted = cur.rowcount
        if deleted:
            print(f"Removed {deleted} previously-synced product document(s)")

        synced = 0
        skipped = 0
        for p in products:
            handle = p.get("id", "")
            text = p.get("text", "")
            sku = extract_sku(text)

            if "Status: Out of Stock" in text:
                skipped += 1
                continue

            title = sku or handle or "product"
            cur.execute(
                """
                INSERT INTO coexistence.knowledge_documents
                    (workspace_id, source_type, title, content, metadata)
                VALUES (%s, 'product', %s, %s, %s)
                RETURNING id
                """,
                (WORKSPACE_ID, title, text, json.dumps({"handle": handle, "sku": sku})),
            )
            document_id = cur.fetchone()[0]

            vector = embed(text)
            cur.execute(
                """
                INSERT INTO coexistence.knowledge_chunks
                    (document_id, workspace_id, chunk_text, embedding, token_count)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (document_id, WORKSPACE_ID, text, to_vector_literal(vector), len(text) // 4),
            )
            synced += 1
            print(f"  Synced {title} ({synced}/{len(products) - skipped})")

        conn.commit()
        print(f"\nSuccess. {synced} in-stock products embedded, {skipped} out-of-stock skipped.")
    except Exception as e:
        conn.rollback()
        print(f"\nFailed, rolled back. Error: {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
