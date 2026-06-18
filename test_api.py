"""
Quick test script for the ChatGPT Free API.
Make sure the server is running first: python chatgpt_api.py
"""

import requests
import json
import sys

API_URL = "http://localhost:8000"


def test_health():
    """Test the health endpoint."""
    print("🔍 Testing /health ...")
    resp = requests.get(f"{API_URL}/health")
    print(f"   Status: {resp.status_code}")
    print(f"   Response: {resp.json()}")
    print()


def test_chat(prompt: str = "What is 2 + 2? Answer in one word."):
    """Test the /chat endpoint."""
    print(f"💬 Testing /chat ...")
    print(f"   Prompt: {prompt}")
    print(f"   ⏳ Waiting for response (this may take 30-60 seconds)...")
    print()

    resp = requests.post(
        f"{API_URL}/chat",
        json={"prompt": prompt},
        timeout=180,
    )

    if resp.status_code == 200:
        data = resp.json()
        print(f"   ✅ Success!")
        print(f"   ID: {data['id']}")
        print(f"   Model: {data['model']}")
        print(f"   Time: {data['elapsed_seconds']}s")
        print(f"   Response:\n")
        print(f"   {data['response']}")
    else:
        print(f"   ❌ Error: {resp.status_code}")
        print(f"   {resp.text}")


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None

    test_health()

    if prompt:
        test_chat(prompt)
    else:
        test_chat()
