"""
test_sales_agent_direct.py — calls sales_agent.run() directly: the EXACT
same function ai_bridge_phase2.py's /ai route calls in production (via
_try_sales_agent()), with the exact same import order it uses. No Flask,
no HTTP server, no bridge_agent.py — this tests the real current
production code path, locally, with nothing in between that could drift.

Usage:
    python test_sales_agent_direct.py "customer message" [customer_id] [workspace_id] [wa_number]

customer_id must start with TEST_. workspace_id is REQUIRED — sales_agent.run()
returns None immediately without one (multi-tenant safety, same as production).
Get a real one from your local DB with list_workspaces.py in this folder.
"""
import sys, os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_THIS_DIR, '..')  # phase2-test root — where
# ai_bridge_phase2.py, invi_products.json and .env live.
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _THIS_DIR)

# Same two lines _try_sales_agent() runs before importing sales_agent, so
# tools/*.py's "from ai_bridge_phase2 import ..." reuses this one loaded
# copy instead of loading ai_bridge_phase2.py a second time.
import ai_bridge_phase2
sys.modules.setdefault("ai_bridge_phase2", ai_bridge_phase2)

import sales_agent

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python test_sales_agent_direct.py "customer message" '
              '[customer_id] [workspace_id] [wa_number]')
        sys.exit(1)

    message = sys.argv[1]
    customer_id = sys.argv[2] if len(sys.argv) > 2 else "TEST_direct1"
    workspace_id = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("TEST_WORKSPACE_ID", "")
    wa_number = sys.argv[4] if len(sys.argv) > 4 else customer_id

    if not customer_id.startswith("TEST_"):
        print(f"[SAFETY] customer_id must start with TEST_. Got: {customer_id}")
        sys.exit(1)

    if not workspace_id:
        print("[WARN] No workspace_id given. sales_agent.run() will return None immediately "
              "(same multi-tenant safety check production uses). Run list_workspaces.py to "
              "get a real one, then pass it as the 3rd argument, e.g.:\n"
              '  python test_sales_agent_direct.py "hi" TEST_v1 <workspace_id>\n')

    print("=" * 60)
    print(f"INPUT: {message!r}")
    print(f"customer_id={customer_id}  workspace_id={workspace_id!r}  wa_number={wa_number!r}")
    print("=" * 60)

    result = sales_agent.run(message, customer_id, workspace_id, wa_number)

    if result is None:
        print("RESULT: None")
        print("This means one of: the Sales Agent is declining on purpose (SKU message, "
              "ORDER_CHANGE handled elsewhere), no workspace_id, or the understand()/generate_reply() "
              "LLM call failed (check the [SALES-AGENT] print above this block for the real reason). "
              "/ai would fall back to the old keyword logic for this message.")
    else:
        for k, v in result.items():
            print(f"{k}: {v}")
    print("=" * 60)
