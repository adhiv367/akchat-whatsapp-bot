import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai_bridge_phase2 import (
    lookup_order_by_phone,
    lookup_order_by_number,
    lookup_order_by_email,
    # Added for the bridge merge — same production functions, read-only
    # imports, nothing duplicated or reimplemented here.
    build_order_status_reply,
    extract_order_number,
    extract_email,
    was_just_asked_for_order_number,
)
