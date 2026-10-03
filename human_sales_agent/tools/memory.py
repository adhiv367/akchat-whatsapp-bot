import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai_bridge_phase2 import (
    get_recent_conversation as _get_recent_conversation,
    log_message as _log_message,
)

TEST_MODE = True  # regression/test harness leaves this True

def get_recent_conversation(customer_id, limit=6, workspace_id=None, wa_number=None):
    return _get_recent_conversation(customer_id, limit, workspace_id=workspace_id, wa_number=wa_number)

def log_message(customer_id, direction, text):
    if TEST_MODE and not str(customer_id).startswith("TEST_"):
        print(f"[SAFETY] Blocked log_message for non-test customer_id={customer_id} while TEST_MODE=True")
        return
    return _log_message(customer_id, direction, text)