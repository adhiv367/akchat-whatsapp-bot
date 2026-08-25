"""
test_memory.py — sends two messages to your local ai_bridge_phase2.py server
to prove conversation memory works, without fighting PowerShell's quoting.

Usage:
    python test_memory.py
"""

import requests

URL = "http://127.0.0.1:5050/ai"

print("Checking server is up...")
try:
    health = requests.get("http://127.0.0.1:5050/health", timeout=5)
    print("Health check:", health.json())
except Exception as e:
    print(f"\nCould not reach the server at all: {e}")
    print("Make sure the OTHER terminal window is still showing 'Press CTRL+C to quit'")
    print("with no error underneath it, then run this script again.")
    exit(1)

print("\n--- Message 1 ---")
r1 = requests.post(URL, json={"message": "do you have red kurthi", "customer_id": "919999999999"})
print("Status code:", r1.status_code)
print("Reply:", r1.json().get("reply", "(no reply field)"))

print("\n--- Message 2 (should reference message 1 if memory works) ---")
r2 = requests.post(URL, json={"message": "is it still available", "customer_id": "919999999999"})
print("Status code:", r2.status_code)
print("Reply:", r2.json().get("reply", "(no reply field)"))
