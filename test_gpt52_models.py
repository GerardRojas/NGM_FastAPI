"""
Test script to validate GPT-5.2 model family availability and behavior.
Tests: gpt-5.2, gpt-5.2-chat-latest, gpt-5.2-pro

Run: python test_gpt52_models.py
Requires: OPENAI_API_KEY in environment or .env file
"""

import os
import sys
import json
import time

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import OpenAI

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    print("ERROR: OPENAI_API_KEY not set")
    sys.exit(1)

client = OpenAI(api_key=api_key)

# ── Models to test ───────────────────────────────────────────────
MODELS = [
    # Current production models (baseline)
    ("gpt-5.1",              "Current Medium tier"),
    ("gpt-5.2",              "Current Heavy tier"),
    # New models from documentation
    ("gpt-5.2-chat-latest",  "NEW - Fast chat / instant"),
    ("gpt-5.2-instant",      "NEW - Alias for chat-latest?"),
    ("gpt-5.2-pro",          "NEW - Pro tier (expensive)"),
]

# ── Test 1: Basic completion ─────────────────────────────────────
def test_basic(model_id, label):
    """Test basic chat completion - does the model respond at all?"""
    try:
        start = time.time()
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": "Reply with exactly: OK"},
                {"role": "user", "content": "Status check"},
            ],
            temperature=0.0,
            max_completion_tokens=10,
        )
        elapsed = time.time() - start
        content = response.choices[0].message.content or ""
        usage = response.usage

        return {
            "status": "OK" if content.strip() else "EMPTY",
            "response": content.strip()[:80],
            "latency_ms": int(elapsed * 1000),
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
        }
    except Exception as e:
        return {"status": "ERROR", "response": str(e)[:120]}


# ── Test 2: JSON structured output ──────────────────────────────
def test_json(model_id, label):
    """Test JSON mode - critical for routing and categorization."""
    try:
        start = time.time()
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": "Return valid JSON with keys: action, confidence"},
                {"role": "user", "content": "Categorize this: 'drywall materials for project A'"},
            ],
            temperature=0.0,
            max_completion_tokens=100,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start
        content = response.choices[0].message.content or ""

        # Validate JSON
        try:
            parsed = json.loads(content)
            has_keys = "action" in parsed and "confidence" in parsed
        except json.JSONDecodeError:
            parsed = None
            has_keys = False

        return {
            "status": "OK" if has_keys else ("PARTIAL" if parsed else "INVALID"),
            "response": content.strip()[:120],
            "latency_ms": int(elapsed * 1000),
            "valid_json": parsed is not None,
            "has_expected_keys": has_keys,
        }
    except Exception as e:
        return {"status": "ERROR", "response": str(e)[:120]}


# ── Test 3: Temperature control ──────────────────────────────────
def test_temperature(model_id, label):
    """Test custom temperature (some models only support temp=1)."""
    results = {}
    for temp in [0.0, 0.1, 0.4]:
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "user", "content": "Say 'hello'"},
                ],
                temperature=temp,
                max_completion_tokens=10,
            )
            content = response.choices[0].message.content or ""
            results[f"temp_{temp}"] = "OK" if content.strip() else "EMPTY"
        except Exception as e:
            results[f"temp_{temp}"] = f"ERROR: {str(e)[:60]}"
    return results


# ── Test 4: Vision support ───────────────────────────────────────
def test_vision(model_id, label):
    """Test if model supports image input (critical for OCR)."""
    # Use a tiny 1x1 white PNG as test image (base64)
    test_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="

    try:
        start = time.time()
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": "What color is this image? Reply in one word."},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{test_image}"
                    }},
                ]},
            ],
            temperature=0.0,
            max_completion_tokens=20,
        )
        elapsed = time.time() - start
        content = response.choices[0].message.content or ""

        return {
            "status": "OK" if content.strip() else "EMPTY",
            "response": content.strip()[:50],
            "latency_ms": int(elapsed * 1000),
            "supports_vision": True,
        }
    except Exception as e:
        error_str = str(e)
        is_unsupported = "does not support" in error_str.lower() or "vision" in error_str.lower()
        return {
            "status": "NO_VISION" if is_unsupported else "ERROR",
            "response": error_str[:120],
            "supports_vision": False,
        }


# ── Run all tests ────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("  GPT-5.2 Family Model Validation")
    print("  Testing: basic, JSON, temperature, vision")
    print("=" * 70)

    results = {}

    for model_id, label in MODELS:
        print(f"\n{'─' * 70}")
        print(f"  {model_id} ({label})")
        print(f"{'─' * 70}")

        model_results = {}

        # Test 1: Basic
        print("  [1/4] Basic completion...", end=" ", flush=True)
        r = test_basic(model_id, label)
        model_results["basic"] = r
        print(f"{r['status']} ({r.get('latency_ms', '?')}ms) -> {r.get('response', '')[:50]}")

        # Skip remaining tests if basic fails
        if r["status"] == "ERROR":
            print(f"  [SKIP] Remaining tests skipped - model unavailable")
            print(f"         Error: {r['response']}")
            results[model_id] = model_results
            continue

        if r["status"] == "EMPTY":
            print(f"  [WARN] Model returned empty response - likely unusable")

        # Test 2: JSON
        print("  [2/4] JSON structured output...", end=" ", flush=True)
        r = test_json(model_id, label)
        model_results["json"] = r
        print(f"{r['status']} ({r.get('latency_ms', '?')}ms)")

        # Test 3: Temperature
        print("  [3/4] Temperature control...", end=" ", flush=True)
        r = test_temperature(model_id, label)
        model_results["temperature"] = r
        status_str = ", ".join(f"{k}={v}" for k, v in r.items())
        print(status_str)

        # Test 4: Vision
        print("  [4/4] Vision/image input...", end=" ", flush=True)
        r = test_vision(model_id, label)
        model_results["vision"] = r
        print(f"{r['status']} (vision={'YES' if r.get('supports_vision') else 'NO'})")

        results[model_id] = model_results

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("  SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Model':<25} {'Basic':<8} {'JSON':<10} {'Temp 0.0':<10} {'Vision':<8}")
    print(f"{'─' * 25} {'─' * 8} {'─' * 10} {'─' * 10} {'─' * 8}")

    for model_id, data in results.items():
        basic = data.get("basic", {}).get("status", "?")
        json_s = data.get("json", {}).get("status", "SKIP")
        temp = data.get("temperature", {}).get("temp_0.0", "SKIP")
        vision = "YES" if data.get("vision", {}).get("supports_vision") else "NO"
        print(f"{model_id:<25} {basic:<8} {json_s:<10} {temp:<10} {vision:<8}")

    # ── Recommendation ───────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("  RECOMMENDATION FOR NGM HUB")
    print(f"{'=' * 70}")

    working = []
    for model_id, data in results.items():
        if data.get("basic", {}).get("status") == "OK":
            working.append(model_id)

    if "gpt-5.2-chat-latest" in working:
        print("  gpt-5.2-chat-latest WORKS -> Can replace gpt-5-mini (broken) and gpt-5.1")
    if "gpt-5.2" in working:
        print("  gpt-5.2 WORKS             -> Keep as heavy tier")
    if "gpt-5.2-pro" in working:
        print("  gpt-5.2-pro WORKS         -> Reserve for critical validation only")

    not_working = [m for m, _ in MODELS if m not in working]
    if not_working:
        print(f"\n  NOT WORKING: {', '.join(not_working)}")

    print(f"\n  Estimated monthly cost at 560 calls/day:")
    print(f"  Current (gpt-5.1 + gpt-5.2):          ~$19/month")
    print(f"  Proposed (gpt-5.2 family unified):     ~$15-18/month")
    print(f"  With prompt caching (10x cheaper in):  ~$5-8/month")


if __name__ == "__main__":
    main()
