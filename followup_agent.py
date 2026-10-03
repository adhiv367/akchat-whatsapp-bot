"""followup_agent.py - saves "I'll buy next week" style promises into
coexistence.customer_followups. Off unless FOLLOWUP_AGENT_ENABLED=true.
Never raises and never blocks the customer's reply (runs in a background thread)."""
import os
import threading
from datetime import date, datetime

from followup_detector import detect_future_intent

SUPPRESS_HOURS = 24


def enabled():
    return os.environ.get("FOLLOWUP_AGENT_ENABLED", "false").strip().lower() == "true"


def _today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(os.environ.get("FOLLOWUP_TZ", "Asia/Kolkata"))).date()
    except Exception:
        return date.today()


def save_followup(get_conn, workspace_id, wa_number, contact_number, message, resolve_product=None):
    """Detect + store one follow-up. Returns 'created', 'updated' or None."""
    try:
        hit = detect_future_intent(message, today=_today())
        if not hit or not workspace_id or not contact_number:
            return None
        sku, name = None, None
        if resolve_product:
            try:
                sku, name = resolve_product(message)
            except Exception as e:
                print(f"[FOLLOWUP] product lookup skipped: {e}")
        conn = get_conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM coexistence.customer_followups
                     WHERE workspace_id = %s AND wa_number = %s AND contact_number = %s
                       AND status = 'pending'
                       AND COALESCE(product_sku, '') = COALESCE(%s, '')
                       AND (%s OR created_at > NOW() - (%s * INTERVAL '1 hour'))
                     ORDER BY id DESC LIMIT 1
                    """,
                    (workspace_id, wa_number, contact_number, sku, bool(sku), SUPPRESS_HOURS),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """
                        UPDATE coexistence.customer_followups
                           SET product_name = COALESCE(%s, product_name), followup_text = %s,
                               due_date = %s, original_message = %s, updated_at = NOW()
                         WHERE id = %s
                        """,
                        (name, hit["followup_text"], hit["due_date"], message[:500], row[0]),
                    )
                    action = "updated"
                else:
                    cur.execute(
                        """
                        INSERT INTO coexistence.customer_followups
                            (workspace_id, wa_number, contact_number, product_sku, product_name,
                             followup_text, due_date, original_message, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (workspace_id, wa_number, contact_number, sku, name,
                         hit["followup_text"], hit["due_date"], message[:500]),
                    )
                    action = "created"
            conn.commit()
            print(f"[FOLLOWUP] {action}: {contact_number} '{hit['followup_text']}' due={hit['due_date']} sku={sku}")
            return action
        except Exception as e:
            print(f"[FOLLOWUP] save failed: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            conn.close()
    except Exception as e:
        print(f"[FOLLOWUP] skipped: {e}")
        return None


def capture_in_background(*args, **kwargs):
    threading.Thread(target=save_followup, args=args, kwargs=kwargs, daemon=True).start()