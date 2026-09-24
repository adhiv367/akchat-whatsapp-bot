-- Migration: 2026_09_24_phase2_multitenant_scoping.sql
--
-- Purpose: add workspace/wa_number scoping columns required by the AI
-- multi-tenant hardening work. ADDITIVE ONLY — no DROP, no DELETE, no
-- TRUNCATE, no data rewrites. Existing rows are left exactly as they are
-- (new columns default to NULL for them); only new writes from the updated
-- ai_bridge_phase2.py populate the new columns going forward.
--
-- Do NOT run this against production. Run it against a local/staging copy
-- of the schema first and confirm it applies cleanly.
--
-- Audit context this migration is based on (read-only audit, see chat):
--   - conversation_messages: 1020 rows, 71 customers, no workspace_id/wa_number today
--   - shown_products: 150 rows, 9 customers, no workspace_id/wa_number today
--   - shopify_orders: 1170 rows, no workspace_id today
--   - whatsapp_accounts: 1 active account, workspace_id=1, no NULL-workspace
--     accounts, no duplicate display numbers
--   - customer_intent_profiles / customer_intent_history / customer_interests /
--     knowledge_* tables are already workspace-scoped — untouched here.
--
-- Per explicit instruction: legacy ambiguous rows in conversation_messages
-- and shown_products are NOT backfilled. They are left with NULL
-- workspace_id/wa_number.

BEGIN;

-- ── conversation_messages ────────────────────────────────────────────────
ALTER TABLE coexistence.conversation_messages
    ADD COLUMN IF NOT EXISTS workspace_id bigint,
    ADD COLUMN IF NOT EXISTS wa_number text;

-- Small table (1020 rows) — a plain non-concurrent index is safe and fast;
-- no risk of a long lock on a table this size.
CREATE INDEX IF NOT EXISTS idx_conversation_messages_tenant_scope
    ON coexistence.conversation_messages (workspace_id, wa_number, customer_id);

COMMENT ON COLUMN coexistence.conversation_messages.workspace_id IS
    'Added by 2026_09_24_phase2_multitenant_scoping.sql. NULL on legacy rows written before this migration — intentionally not backfilled (audit found no reliable 1:1 wa_number mapping for most customers).';
COMMENT ON COLUMN coexistence.conversation_messages.wa_number IS
    'Added by 2026_09_24_phase2_multitenant_scoping.sql. NULL on legacy rows — see workspace_id comment.';

-- ── shown_products ───────────────────────────────────────────────────────
ALTER TABLE coexistence.shown_products
    ADD COLUMN IF NOT EXISTS workspace_id bigint,
    ADD COLUMN IF NOT EXISTS wa_number text;

-- Small table (150 rows) — plain index is safe.
CREATE INDEX IF NOT EXISTS idx_shown_products_tenant_scope
    ON coexistence.shown_products (workspace_id, wa_number, customer_id);

COMMENT ON COLUMN coexistence.shown_products.workspace_id IS
    'Added by 2026_09_24_phase2_multitenant_scoping.sql. NULL on legacy rows — intentionally not backfilled.';
COMMENT ON COLUMN coexistence.shown_products.wa_number IS
    'Added by 2026_09_24_phase2_multitenant_scoping.sql. NULL on legacy rows — see workspace_id comment.';

-- ── shopify_orders ───────────────────────────────────────────────────────
ALTER TABLE coexistence.shopify_orders
    ADD COLUMN IF NOT EXISTS workspace_id bigint;

-- 1170 rows — still small enough for a plain (non-CONCURRENTLY) index inside
-- this transaction; CONCURRENTLY cannot run inside BEGIN/COMMIT anyway.
CREATE INDEX IF NOT EXISTS idx_shopify_orders_workspace_phone
    ON coexistence.shopify_orders (workspace_id, phone_digits);

COMMENT ON COLUMN coexistence.shopify_orders.workspace_id IS
    'Added by 2026_09_24_phase2_multitenant_scoping.sql. NULL on rows synced before this migration. New rows are tagged by ai_bridge_phase2.py via resolve_sole_active_workspace() (queries the single active whatsapp_accounts workspace); left NULL if more than one active workspace ever exists, since order webhooks carry no wa_number to disambiguate with.';

-- ── Explicitly NOT touched by this migration ────────────────────────────
-- customer_intent_profiles, customer_intent_history, customer_interests,
-- knowledge_documents, knowledge_chunks: already workspace_id/wa_number
-- scoped per audit. No DEFAULT 1 is dropped from any of these tables.

COMMIT;
