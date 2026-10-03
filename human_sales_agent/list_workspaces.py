"""
list_workspaces.py — prints the active workspace_id(s) + phone numbers from
your local DB, from the exact table ai_bridge_phase2.py's resolve_workspace_id()
reads (coexistence.whatsapp_accounts). Use one of these workspace_id values
with test_sales_agent_direct.py.
"""
import sys, os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_THIS_DIR, '..')
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, _PROJECT_ROOT)

import ai_bridge_phase2 as prod

conn = prod.get_db_conn()
if not conn:
    print("[ERROR] Could not connect to the database. Check DATABASE_URL in your .env "
          "(the one at the project root, phase2-test\\.env) points at the DB you intend "
          "to test against.")
    sys.exit(1)

try:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT workspace_id, display_phone_number, is_active
            FROM coexistence.whatsapp_accounts
            ORDER BY is_active DESC
        """)
        rows = cur.fetchall()
finally:
    conn.close()

if not rows:
    print("No rows in coexistence.whatsapp_accounts — nothing to test against yet.")
else:
    print(f"{'workspace_id':40} {'display_phone_number':25} active")
    print("-" * 75)
    for workspace_id, phone, active in rows:
        print(f"{str(workspace_id):40} {str(phone):25} {active}")
