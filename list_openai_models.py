"""
List the OpenAI models your API key can actually access.

Run from the repo root:
    python list_openai_models.py

It reads your key from .env (OPENAI_API_KEY or CHATGPT_API_KEY) — the same one
the pipeline uses — and prints the models available to that key, with the GPT
chat models highlighted so you can see which gpt-5.x you have.
"""
from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai package not installed. Run: pip install openai")

key = (os.getenv("OPENAI_API_KEY") or os.getenv("CHATGPT_API_KEY") or "").strip()
if not key:
    sys.exit("No OPENAI_API_KEY (or CHATGPT_API_KEY) found in environment / .env")

client = OpenAI(api_key=key)

try:
    ids = sorted(m.id for m in client.models.list().data)
except Exception as exc:
    sys.exit(f"Could not list models: {type(exc).__name__}: {exc}")

# Highlight the text/chat GPT models you'd use for writing.
gpt = [m for m in ids if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))]

print(f"\n{len(ids)} models available to this key.\n")
print("=== GPT / chat models (writing candidates) ===")
for m in gpt:
    print(f"  {m}")
print("\n=== all models ===")
for m in ids:
    print(f"  {m}")
print("\nTip: pick the highest gpt-5.x you see (e.g. gpt-5.5) for OPENAI_WRITING_MODEL.")
