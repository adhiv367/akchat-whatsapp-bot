import os, sys, socket, runpy
os.environ["DATABASE_URL"] = "postgresql://nobody:nobody@127.0.0.1:1/none"
os.environ["PGCONNECT_TIMEOUT"] = "1"
for k in ("GROQ_API_KEY", "HUMAN_AGENT_GROQ_API_KEY", "GEMINI_API_KEY",
          "SHOPIFY_ACCESS_TOKEN", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_WEBHOOK_SECRET"):
    os.environ[k] = "dummy-not-a-real-key"
LOCAL = {"127.0.0.1", "localhost", "::1"}
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_gai = socket.getaddrinfo
def _check(address):
    host = address[0] if isinstance(address, tuple) else None
    if host not in LOCAL:
        raise RuntimeError("BLOCKED outbound connect to %r" % (address,))
def g_connect(self, address, *a, **k):
    _check(address); return _connect(self, address, *a, **k)
def g_connect_ex(self, address, *a, **k):
    _check(address); return _connect_ex(self, address, *a, **k)
def g_gai(host, *a, **k):
    if host is not None and host not in LOCAL:
        raise RuntimeError("BLOCKED DNS lookup for %r" % (host,))
    return _gai(host, *a, **k)
socket.socket.connect = g_connect
socket.socket.connect_ex = g_connect_ex
socket.getaddrinfo = g_gai
target = os.path.abspath(sys.argv[1])
sys.argv = [target] + sys.argv[2:]
sys.path.insert(0, os.path.dirname(target))
print("[safe_run] network guard on, dummy DB/keys set, running", target)
runpy.run_path(target, run_name="__main__")
