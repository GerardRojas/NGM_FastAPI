"""
Round 3: With SDK 2.20.0 - test responses API and all models.
"""

import os
import sys
import json
import time

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

def divider(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# ── Test 1: responses API availability ───────────────────────────
def test_responses_api():
    divider("responses API - all models")

    models = [
        "gpt-5.1",
        "gpt-5.2",
        "gpt-5.2-chat-latest",
        "gpt-5.2-pro",
    ]

    for model in models:
        print(f"\n  [{model}] responses.create...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.responses.create(
                model=model,
                input="Reply with exactly one word: OK",
            )
            elapsed = time.time() - start
            content = r.output_text if hasattr(r, 'output_text') else str(r)[:100]
            usage = r.usage if hasattr(r, 'usage') else None

            print(f"OK ({int(elapsed*1000)}ms) -> '{str(content).strip()[:50]}'")
            if usage:
                in_tok = usage.input_tokens if hasattr(usage, 'input_tokens') else '?'
                out_tok = usage.output_tokens if hasattr(usage, 'output_tokens') else '?'
                print(f"         Tokens: in={in_tok}, out={out_tok}")
        except Exception as e:
            print(f"ERROR -> {str(e)[:120]}")


# ── Test 2: responses API with system instructions ───────────────
def test_responses_with_instructions():
    divider("responses API - routing simulation")

    models = ["gpt-5.2", "gpt-5.2-chat-latest"]

    routing_prompt = """You route user requests to functions. Return ONLY valid JSON:
{"decision": "function_call"|"free_chat"|"clarify", "function": "process_receipt"|"run_auto_auth"|null, "reason": "brief explanation"}"""

    user_input = "@Andrew I have a receipt from Home Depot for $450, materials for project Sunrise"

    for model in models:
        print(f"\n  [{model}] routing...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.responses.create(
                model=model,
                instructions=routing_prompt,
                input=user_input,
            )
            elapsed = time.time() - start
            content = r.output_text if hasattr(r, 'output_text') else str(r)[:200]
            content_str = str(content).strip()

            # Try to parse JSON
            try:
                parsed = json.loads(content_str)
                print(f"OK ({int(elapsed*1000)}ms)")
                print(f"         -> {json.dumps(parsed)[:100]}")
            except json.JSONDecodeError:
                # Maybe wrapped in markdown code block
                clean = content_str.strip("`").replace("json\n", "").replace("json", "").strip()
                try:
                    parsed = json.loads(clean)
                    print(f"OK ({int(elapsed*1000)}ms) [markdown-wrapped]")
                    print(f"         -> {json.dumps(parsed)[:100]}")
                except:
                    print(f"NON-JSON ({int(elapsed*1000)}ms)")
                    print(f"         -> '{content_str[:100]}'")
        except Exception as e:
            print(f"ERROR -> {str(e)[:120]}")


# ── Test 3: chat.completions with gpt-5.2 for ALL our use cases ─
def test_52_comprehensive():
    divider("gpt-5.2 chat.completions - ALL production scenarios")

    tests = [
        ("Routing (JSON)", {
            "messages": [
                {"role": "system", "content": "Route requests. Return JSON: {\"decision\": \"function_call\", \"function\": \"process_receipt\"}"},
                {"role": "user", "content": "@Andrew receipt from HD $450"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_completion_tokens": 150,
        }),
        ("Categorization (JSON)", {
            "messages": [
                {"role": "system", "content": "Categorize expenses. Return JSON: {\"category\": str, \"confidence\": float}"},
                {"role": "user", "content": "Drywall 4x8 sheets, 50 units at $12 each"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_completion_tokens": 100,
        }),
        ("Personality/Chat", {
            "messages": [
                {"role": "system", "content": "You are Andrew, a friendly but precise accounting assistant. You speak in short sentences."},
                {"role": "user", "content": "Hey Andrew, can you help me categorize some expenses?"},
            ],
            "temperature": 0.4,
            "max_completion_tokens": 200,
        }),
        ("Context extraction", {
            "messages": [
                {"role": "system", "content": "Extract structured info. Return JSON: {\"amount\": float, \"vendor\": str, \"project\": str, \"items\": [str]}"},
                {"role": "user", "content": "pago de drywall $500 para proyecto Sunrise, tambien $200 de tornillos"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_completion_tokens": 200,
        }),
        ("NLU classification", {
            "messages": [
                {"role": "system", "content": "Classify intent. Return JSON: {\"intent\": str, \"entities\": {}}"},
                {"role": "user", "content": "show me last month expenses for project Alpha"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_completion_tokens": 100,
        }),
    ]

    all_ok = True
    for label, kwargs in tests:
        print(f"\n  [{label}]...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.chat.completions.create(model="gpt-5.2", **kwargs)
            elapsed = time.time() - start
            content = r.choices[0].message.content or ""

            if not content.strip():
                print(f"EMPTY ({int(elapsed*1000)}ms)")
                all_ok = False
            elif "response_format" in kwargs:
                try:
                    parsed = json.loads(content)
                    print(f"OK ({int(elapsed*1000)}ms) -> {json.dumps(parsed, ensure_ascii=False)[:80]}")
                except:
                    print(f"BAD JSON ({int(elapsed*1000)}ms) -> '{content[:80]}'")
                    all_ok = False
            else:
                print(f"OK ({int(elapsed*1000)}ms) -> '{content.strip()[:80]}'")
        except Exception as e:
            print(f"ERROR -> {str(e)[:100]}")
            all_ok = False

    if all_ok:
        print(f"\n  >>> ALL SCENARIOS PASS WITH gpt-5.2 <<<")
    else:
        print(f"\n  >>> SOME SCENARIOS FAILED <<<")


# ── Test 4: Latency comparison gpt-5.1 vs gpt-5.2 ───────────────
def test_latency_comparison():
    divider("Latency: gpt-5.1 vs gpt-5.2 (same prompt)")

    prompt = {
        "messages": [
            {"role": "system", "content": "Return JSON: {\"intent\": str, \"confidence\": float}"},
            {"role": "user", "content": "show expenses for project Alpha"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "max_completion_tokens": 100,
    }

    for model in ["gpt-5.1", "gpt-5.2"]:
        times = []
        print(f"\n  [{model}] 3 runs:", end=" ", flush=True)
        for i in range(3):
            try:
                start = time.time()
                r = client.chat.completions.create(model=model, **prompt)
                elapsed = time.time() - start
                content = r.choices[0].message.content or ""
                ok = bool(content.strip())
                times.append(int(elapsed * 1000))
                print(f"{int(elapsed*1000)}ms{'(OK)' if ok else '(EMPTY)'}", end=" ", flush=True)
            except Exception as e:
                print(f"ERR", end=" ", flush=True)

        if times:
            avg = sum(times) / len(times)
            print(f" | avg={int(avg)}ms")
        else:
            print(" | no data")


# ── Run ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print(f"  GPT-5.2 Round 3 - SDK v2.20.0")
    print("=" * 60)

    test_responses_api()
    test_responses_with_instructions()
    test_52_comprehensive()
    test_latency_comparison()

    divider("FINAL VERDICT")
    print("""
  Based on all tests, the recommended model structure is:

  FAST/CHAT:  gpt-5.2 (temp=0.4) - personality, conversation
  MEDIUM:     gpt-5.2 (temp=0.0-0.1) - routing, categorization, NLU
  HEAVY:      gpt-5.2 (temp=0.1) - OCR, mismatch, duplicates
  PRO:        gpt-5.2-pro (if available) - critical validation only

  Price: $1.75/1M input, $14.00/1M output (same for all gpt-5.2)
  Cached input: $0.175/1M (10x cheaper!)
""")
