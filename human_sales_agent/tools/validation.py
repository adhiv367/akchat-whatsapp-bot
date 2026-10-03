import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai_bridge_phase2 import (
    validate_reply,
    check_stock_claims,
    extract_prices,
)