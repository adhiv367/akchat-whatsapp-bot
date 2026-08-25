-- Phase 2 migration: conversation memory tables
-- Reconstructed from local akchat-db, cleaned up for safe production use.
-- Owner lines removed (production DB user may not be named "postgres").
-- Run this whole file in one transaction so it's all-or-nothing.

BEGIN;

CREATE SCHEMA IF NOT EXISTS coexistence;

--
-- Table: conversation_messages
--

CREATE TABLE IF NOT EXISTS coexistence.conversation_messages (
    id bigint NOT NULL,
    customer_id text NOT NULL,
    direction text NOT NULL,
    message_text text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT conversation_messages_direction_check CHECK ((direction = ANY (ARRAY['incoming'::text, 'outgoing'::text])))
);

CREATE SEQUENCE IF NOT EXISTS coexistence.conversation_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE coexistence.conversation_messages_id_seq OWNED BY coexistence.conversation_messages.id;

ALTER TABLE ONLY coexistence.conversation_messages
    ALTER COLUMN id SET DEFAULT nextval('coexistence.conversation_messages_id_seq'::regclass);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'conversation_messages_pkey'
    ) THEN
        ALTER TABLE ONLY coexistence.conversation_messages
            ADD CONSTRAINT conversation_messages_pkey PRIMARY KEY (id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_conversation_messages_customer_id
    ON coexistence.conversation_messages USING btree (customer_id, created_at);

--
-- Table: shown_products
--

CREATE TABLE IF NOT EXISTS coexistence.shown_products (
    id bigint NOT NULL,
    customer_id text NOT NULL,
    sku text NOT NULL,
    shown_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS coexistence.shown_products_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE coexistence.shown_products_id_seq OWNED BY coexistence.shown_products.id;

ALTER TABLE ONLY coexistence.shown_products
    ALTER COLUMN id SET DEFAULT nextval('coexistence.shown_products_id_seq'::regclass);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'shown_products_pkey'
    ) THEN
        ALTER TABLE ONLY coexistence.shown_products
            ADD CONSTRAINT shown_products_pkey PRIMARY KEY (id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_shown_products_customer_id
    ON coexistence.shown_products USING btree (customer_id, shown_at);

COMMIT;
