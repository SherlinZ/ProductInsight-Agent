"""
LLM Client Smoke Test.

Verifies that the LLM client can successfully call the API
and return valid JSON responses.
"""

from __future__ import annotations

import json
import time
import sys
import os

# Add project root to path so we can import the backend module
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Load .env file from project root before reading any env vars
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def main() -> None:
    print("=" * 60)
    print("LLM Client Smoke Test")
    print("=" * 60)

    api_key = os.getenv("MODEL_API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "")
    endpoint = os.getenv("MODEL_ENDPOINT", "")

    configured = bool(api_key and model_name)
    print(f"Model: {model_name or '(not set)'}")
    print(f"Endpoint: {endpoint or '(not set)'}")
    print(f"API Key configured: {configured}")
    print("-" * 60)

    if not configured:
        print("ERROR: MODEL_API_KEY and MODEL_NAME must be configured in .env")
        sys.exit(1)

    from backend.app.services.llm_client import LLMClient, LLMConfig

    config = LLMConfig()
    client = LLMClient(config)

    print(f"LLM Client initialized: {client.model_name}")
    print()

    # Test 1: Simple text request
    print("[Test 1] Simple text request...")
    start = time.time()
    messages = [
        {"role": "user", "content": 'Return JSON: {"ok": true, "message": "hello"}'}
    ]
    try:
        text = client.chat_text(messages, temperature=0.0)
        elapsed = (time.time() - start) * 1000
        print(f"  SUCCESS - Response: {text.strip()[:100]}")
        print(f"  Latency: {elapsed:.0f}ms")
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        print(f"  FAILED after {elapsed:.0f}ms: {e}")
        sys.exit(1)

    print()

    # Test 2: JSON structured request
    print("[Test 2] Structured JSON request (analysis scenario)...")
    start = time.time()
    messages = [
        {
            "role": "user",
            "content": (
                'Return valid JSON only (no markdown): '
                '{"product": "Dify", "core_capabilities": ["workflow", "agents", "RAG"], '
                '"open_source": true}'
            ),
        }
    ]
    try:
        result = client.chat_json(messages, temperature=0.0)
        elapsed = (time.time() - start) * 1000
        print(f"  SUCCESS - Parsed JSON: {json.dumps(result, ensure_ascii=False)[:200]}")
        print(f"  Latency: {elapsed:.0f}ms")
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        print(f"  FAILED after {elapsed:.0f}ms: {e}")
        sys.exit(1)

    print()
    print("=" * 60)
    print("All smoke tests PASSED")
    print(f"Model: {client.model_name}")
    print("=" * 60)


if __name__ == "__main__":
    main()
