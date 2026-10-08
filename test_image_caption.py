import os, sys, re
os.environ["DATABASE_URL"] = "postgresql://x:x@127.0.0.1:1/none"
os.environ["AI_SERVICE_SHARED_SECRET"] = "dummy-test-secret"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_bridge_phase2 as b
import image_branch

b.resolve_workspace_id = lambda wa: "ws-test"
b.log_message = lambda *a, **k: None
b.remember_skus = lambda *a, **k: None
b.classify_intent_smart = lambda *a, **k: ("browsing", 0, 0.5)
b.update_customer_score = lambda *a, **k: None
b._maybe_capture_followup = lambda *a, **k: None

prod = b.load_products()[0]
det = b.parse_product_details(prod)
MATCH = {"handle": prod.get("id"), "sku": det["SKU"], "pos": 0, "distance": 0.01}

seen = []
mode = {"v": "answer"}
def fake_agent(message, *a, **k):
    seen.append(message)
    if re.search(r"\bIC[A-Z]\d+\b", message, re.I):   # real agent returns None for SKU text
        return None
    if mode["v"] == "answer":
        return {"reply": "AGENT-ANSWER", "image": None, "type": "text", "log_text": "x"}
    if mode["v"] == "none":
        return None
    if mode["v"] == "cards":
        return {"reply": "CARDS", "images": [{"sku": "X"}], "type": "product"}
    if mode["v"] == "handoff":
        return {"reply": "AGENT-ANSWER", "type": "text", "handoff_required": True, "handoff_reason": "r"}
b._try_sales_agent = fake_agent

client = b.app.test_client()
H = {"X-AI-Service-Secret": "dummy-test-secret"}
def post(**kw):
    seen.clear()
    body = {"customer_id": "c1", "wa_number": "w1", "image_base64": "AAAA"}
    body.update(kw)
    return client.post("/ai", json=body, headers=H)

fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += 0 if cond else 1

image_branch.identify_product = lambda b64: MATCH
r = post(message=""); j = r.get_json()
check("image only -> card, agent not called", j["type"] == "product" and j["image"] and not seen)
card_len = len(j["reply"])

mode["v"] = "answer"
r = post(message="XL available?"); j = r.get_json()
check("caption -> agent gets caption only", seen == ["XL available?"])
check("caption -> card + answer in one reply", j["reply"].endswith("AGENT-ANSWER") and len(j["reply"]) > card_len and j["image"] and j["type"] == "product")

mode["v"] = "none"
j = post(message="price?").get_json()
check("agent returns None -> card only", "AGENT-ANSWER" not in j["reply"] and len(j["reply"]) == card_len)

mode["v"] = "cards"
j = post(message="show me more").get_json()
check("agent returns cards -> card only", "CARDS" not in j["reply"] and len(j["reply"]) == card_len)

mode["v"] = "handoff"
_old = (b._handoff_alerts_enabled, b._low_alerts_enabled)
b._handoff_alerts_enabled = lambda: True
b._low_alerts_enabled = lambda: False
_old = (b._handoff_alerts_enabled, b._low_alerts_enabled)
b._handoff_alerts_enabled = lambda: True
b._low_alerts_enabled = lambda: False
j = post(message="very bad quality").get_json()
b._handoff_alerts_enabled, b._low_alerts_enabled = _old
b._handoff_alerts_enabled, b._low_alerts_enabled = _old
check("handoff flag carried over", j.get("handoff_required") is True)

mode["v"] = "answer"
j = post(message="ICK99999 price?").get_json()
check("caption with SKU -> card only", "AGENT-ANSWER" not in j["reply"])

image_branch.identify_product = lambda b64: None
post(message="XL available?")
check("no match -> caption goes to existing flow unchanged", seen == ["XL available?"])

seen.clear(); client.post("/ai", json={"customer_id": "c1", "wa_number": "w1", "message": "hello there"}, headers=H)
check("plain text message unaffected", True)
print("FAILS:", fails)