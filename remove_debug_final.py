with open("ai_bridge_phase2.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
removed = []
for i, line in enumerate(lines):
    if "PHASE4-DEBUG" in line:
        removed.append((i + 1, line.strip()))
        continue
    new_lines.append(line)

print(f"Removing {len(removed)} line(s):")
for lineno, content in removed:
    print(f"  Line {lineno}: {content}")

if len(removed) > 0:
    with open("ai_bridge_phase2.py", "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print("Done - file updated.")
else:
    print("No PHASE4-DEBUG lines found - no changes made.")
