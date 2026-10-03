import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ai_bridge_phase2 import (
    load_products,
    find_product_by_sku,
    parse_product_details,
    search,
    semantic_search_products,
    # Added for the bridge merge — same production functions, read-only
    # imports, nothing duplicated or reimplemented here.
    get_top_product_images,
    build_product_reply,
)
