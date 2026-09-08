with open("ai_bridge_phase2.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    "order_number = extract_order_number(msg)",
    "order_number = extract_order_number(message)"
)
content = content.replace(
    'no valid number extracted from: {msg}")',
    'no valid number extracted from: {message}")'
)

with open("ai_bridge_phase2.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Done.")