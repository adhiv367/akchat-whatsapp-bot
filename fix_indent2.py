with open("ai_bridge_phase2.py", "r", encoding="utf-8") as f:
    content = f.read()

old = '''    if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_ACCESS_TOKEN:
           print(f"[PHASE4-DEBUG] Missing creds: domain_set={bool(SHOPIFY_STORE_DOMAIN)} token_set={bool(SHOPIFY_ACCESS_TOKEN)}")
        return []
    try:
        clean_number = order_number.lstrip('#').strip()'''

new = '''    if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_ACCESS_TOKEN:
        print(f"[PHASE4-DEBUG] Missing creds: domain_set={bool(SHOPIFY_STORE_DOMAIN)} token_set={bool(SHOPIFY_ACCESS_TOKEN)}")
        return []
    try:
        clean_number = order_number.lstrip('#').strip()'''

count = content.count(old)
print(f"Match count: {count}")
if count == 1:
    content = content.replace(old, new)
    with open("ai_bridge_phase2.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Fixed.")
else:
    print("Block not found exactly once — no changes made. Paste current lines 467-478 instead.")