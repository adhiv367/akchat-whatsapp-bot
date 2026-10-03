import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

# SALES_AGENT_PATCH: falls back to the bridge's normal GROQ_API_KEY, so no new secret is needed on Render
GROQ_API_KEY = os.environ.get("HUMAN_AGENT_GROQ_API_KEY") or os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Defaults are the old values. On Render you can lower them (SALES_AGENT_MAX_RETRIES=1,
# SALES_AGENT_LLM_TIMEOUT=6) so a slow Groq can never make the customer wait past Node's 15s limit.
MAX_RETRIES = int(os.environ.get("SALES_AGENT_MAX_RETRIES", "3"))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("SALES_AGENT_LLM_TIMEOUT", "20"))
BASE_DELAY_SECONDS = 2


def chat_completion(messages, max_tokens=500, temperature=0.4):
    """Provider-agnostic entry point with retry-with-backoff. Everything else
    in this codebase should call THIS function, never requests.post(...)
    directly, so swapping providers later means changing only this file.
    messages: list of {"role": "system"|"user"|"assistant", "content": str}
    Returns: (success: bool, text_or_error: str)
    """
    if not GROQ_API_KEY:
        return False, "GROQ_API_KEY not configured"

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    last_error = "Unknown error"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT_SECONDS)

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                print(f"[LLM] Rate limited (429). Retry {attempt}/{MAX_RETRIES} after {delay:.1f}s")
                time.sleep(delay)
                last_error = "Rate limited (429)"
                continue

            if resp.status_code >= 500:
                delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                print(f"[LLM] Server error ({resp.status_code}). Retry {attempt}/{MAX_RETRIES} after {delay:.1f}s")
                time.sleep(delay)
                last_error = f"Server error ({resp.status_code})"
                continue

            result = resp.json()
            if "choices" not in result:
                last_error = f"LLM error response: {result}"
                delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                print(f"[LLM] Unexpected response. Retry {attempt}/{MAX_RETRIES} after {delay:.1f}s")
                time.sleep(delay)
                continue

            return True, result["choices"][0]["message"]["content"]

        except Exception as e:
            last_error = f"LLM call failed: {e}"
            delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            print(f"[LLM] Exception on attempt {attempt}/{MAX_RETRIES}: {e}. Retry after {delay:.1f}s")
            time.sleep(delay)

    return False, f"LLM call failed after {MAX_RETRIES} attempts: {last_error}"