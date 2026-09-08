with open("ai_bridge_phase2.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Remove the debug lines that landed in the wrong function (lookup_order_by_phone)
wrong_block = '''        print(f"[PHASE4-DEBUG] URL={url} params={params}")
        print(f"[PHASE4-DEBUG] status={resp.status_code}")
'''
content = content.replace(wrong_block, "", 1)

wrong_line2 = '        print(f"[PHASE4-DEBUG] orders_returned={len(orders)} names={[o.get(\'name\') for o in orders]}")\n'
content = content.replace(wrong_line2, "", 1)

# 2. Add debug lines to the correct function (lookup_order_by_number)
old_block = '''        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            print(f"[PHASE4] Shopify order-number lookup failed: {resp.status_code} {resp.text[:200]}")
            return []
        orders = resp.json().get("orders", [])
        return [{
            "order_number": o.get("order_number") or o.get("name"),'''

new_block = '''        resp = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"[PHASE4-DEBUG] URL={url} params={params}")
        print(f"[PHASE4-DEBUG] status={resp.status_code}")
        if resp.status_code != 200:
            print(f"[PHASE4] Shopify order-number lookup failed: {resp.status_code} {resp.text[:200]}")
            return []
        orders = resp.json().get("orders", [])
        print(f"[PHASE4-DEBUG] orders_returned={len(orders)} names={[o.get('name') for o in orders]}")
        return [{
            "order_number": o.get("order_number") or o.get("name"),'''

count = content.count(old_block)
print(f"Match count for target block: {count}")
if count == 1:
    content = content.replace(old_block, new_block)
    with open("ai_bridge_phase2.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Fixed.")
else:
    print("Did NOT write — block not found exactly once. No changes made.")