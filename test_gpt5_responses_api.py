"""
Test gpt-5-mini and gpt-5-nano via responses API (correct API for these models).
Previous tests used chat.completions which returned EMPTY - wrong API!

Pricing:
  gpt-5-nano: $0.05 input / $0.01 cached / $0.40 output
  gpt-5-mini: $0.25 input / $0.03 cached / $2.00 output
  gpt-5.2:    $1.75 input / $0.18 cached / $14.00 output
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
print(f"SDK version: {__import__('openai').__version__}")


def divider(title):
    print(f"\n{'=' * 65}")
    print(f"  {title}")
    print(f"{'=' * 65}")


MODELS = ["gpt-5-nano", "gpt-5-mini", "gpt-5.2"]


# ── Test 1: Basic response ───────────────────────────────────────
def test_basic():
    divider("Test 1: Basic response (responses API)")

    for model in MODELS:
        print(f"\n  [{model}]...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.responses.create(
                model=model,
                input="Reply with exactly one word: OK",
            )
            elapsed = time.time() - start
            content = r.output_text if hasattr(r, 'output_text') else ""
            usage = r.usage if hasattr(r, 'usage') else None
            in_t = usage.input_tokens if usage and hasattr(usage, 'input_tokens') else "?"
            out_t = usage.output_tokens if usage and hasattr(usage, 'output_tokens') else "?"
            print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) tok={in_t}/{out_t} -> '{content.strip()[:40]}'")
        except Exception as e:
            print(f"ERROR: {str(e)[:100]}")


# ── Test 2: Instructions (system prompt equivalent) ──────────────
def test_instructions():
    divider("Test 2: Instructions (system prompt)")

    for model in MODELS:
        print(f"\n  [{model}]...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.responses.create(
                model=model,
                instructions="You are Andrew, a friendly accounting assistant. Keep responses under 2 sentences.",
                input="Hey Andrew, can you help me with some expenses?",
            )
            elapsed = time.time() - start
            content = r.output_text if hasattr(r, 'output_text') else ""
            print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms)")
            print(f"    -> '{content.strip()[:100]}'")
        except Exception as e:
            print(f"ERROR: {str(e)[:100]}")


# ── Test 3: JSON structured output ──────────────────────────────
def test_json():
    divider("Test 3: JSON structured output")

    for model in MODELS:
        print(f"\n  [{model}]")

        # Method A: Ask for JSON in instructions
        print(f"    [A] JSON via instructions...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.responses.create(
                model=model,
                instructions="Always respond with valid JSON only. No markdown, no explanation.",
                input='Classify this expense and return JSON: {"category": str, "confidence": float}\nExpense: "Drywall 4x8 sheets, 50 units"',
            )
            elapsed = time.time() - start
            content = r.output_text if hasattr(r, 'output_text') else ""
            content_clean = content.strip().strip("`").replace("json\n", "").replace("json", "").strip()
            try:
                parsed = json.loads(content_clean)
                print(f"OK ({int(elapsed*1000)}ms) -> {json.dumps(parsed)[:70]}")
            except:
                print(f"BAD JSON ({int(elapsed*1000)}ms) -> '{content.strip()[:70]}'")
        except Exception as e:
            print(f"ERROR: {str(e)[:80]}")

        # Method B: Using text.format = json_schema (if supported)
        print(f"    [B] JSON via response_format...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.responses.create(
                model=model,
                instructions="Classify expenses into categories.",
                input="Drywall 4x8 sheets, 50 units at $12 each",
                text={"format": {"type": "json_schema", "name": "classification", "schema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "confidence": {"type": "number"}
                    },
                    "required": ["category", "confidence"],
                    "additionalProperties": False
                }}},
            )
            elapsed = time.time() - start
            content = r.output_text if hasattr(r, 'output_text') else ""
            try:
                parsed = json.loads(content)
                print(f"OK ({int(elapsed*1000)}ms) -> {json.dumps(parsed)[:70]}")
            except:
                print(f"BAD ({int(elapsed*1000)}ms) -> '{content.strip()[:70]}'")
        except Exception as e:
            err = str(e)
            if "not supported" in err.lower() or "invalid" in err.lower():
                print(f"NOT SUPPORTED")
            else:
                print(f"ERROR: {err[:80]}")

        # Method C: json_object format
        print(f"    [C] JSON via json_object...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.responses.create(
                model=model,
                instructions="Classify expenses. Return JSON with category and confidence.",
                input="Drywall 4x8 sheets, 50 units at $12 each",
                text={"format": {"type": "json_object"}},
            )
            elapsed = time.time() - start
            content = r.output_text if hasattr(r, 'output_text') else ""
            try:
                parsed = json.loads(content)
                print(f"OK ({int(elapsed*1000)}ms) -> {json.dumps(parsed)[:70]}")
            except:
                print(f"BAD ({int(elapsed*1000)}ms) -> '{content.strip()[:70]}'")
        except Exception as e:
            err = str(e)
            if "not supported" in err.lower():
                print(f"NOT SUPPORTED")
            else:
                print(f"ERROR: {err[:80]}")


# ── Test 4: Agent routing (our core use case) ───────────────────
def test_routing():
    divider("Test 4: Agent routing simulation")

    routing_instructions = """You route user requests to functions. Return ONLY valid JSON:
{"decision": "function_call"|"free_chat"|"clarify", "function": "process_receipt"|"run_auto_auth"|null, "reason": "brief"}"""

    inputs = [
        "@Andrew I have a receipt from Home Depot for $450, materials for project Sunrise",
        "@Daneel run authorization check on project Alpha",
        "@Andrew hey how are you doing today?",
    ]

    for model in MODELS:
        print(f"\n  [{model}]")
        for inp in inputs:
            label = inp[:50]
            print(f"    '{label}...'", end=" ", flush=True)
            try:
                start = time.time()
                r = client.responses.create(
                    model=model,
                    instructions=routing_instructions,
                    input=inp,
                )
                elapsed = time.time() - start
                content = r.output_text if hasattr(r, 'output_text') else ""
                content_clean = content.strip().strip("`").replace("json\n", "").replace("json", "").strip()
                try:
                    parsed = json.loads(content_clean)
                    print(f"OK ({int(elapsed*1000)}ms) -> {json.dumps(parsed)[:70]}")
                except:
                    print(f"BAD ({int(elapsed*1000)}ms) -> '{content.strip()[:60]}'")
            except Exception as e:
                print(f"ERROR: {str(e)[:60]}")


# ── Test 5: Context extraction (personality/NLU) ─────────────────
def test_context_extraction():
    divider("Test 5: Context extraction (NLU)")

    instructions = """Extract structured info from user message. Return ONLY valid JSON:
{"amount": float|null, "vendor": str|null, "project": str|null, "workers": [str], "labor_type": str|null}"""

    inputs = [
        "pago de drywall $500 para proyecto Sunrise, Smith y Jones",
        "receipt from Home Depot $1,234.56 for project Alpha, materials",
        "check #4521 for electrician work, $2,000, project Beta",
    ]

    for model in MODELS:
        print(f"\n  [{model}]")
        for inp in inputs:
            print(f"    '{inp[:45]}...'", end=" ", flush=True)
            try:
                start = time.time()
                r = client.responses.create(
                    model=model,
                    instructions=instructions,
                    input=inp,
                )
                elapsed = time.time() - start
                content = r.output_text if hasattr(r, 'output_text') else ""
                content_clean = content.strip().strip("`").replace("json\n", "").replace("json", "").strip()
                try:
                    parsed = json.loads(content_clean)
                    print(f"OK ({int(elapsed*1000)}ms) -> {json.dumps(parsed, ensure_ascii=False)[:70]}")
                except:
                    print(f"BAD ({int(elapsed*1000)}ms) -> '{content.strip()[:60]}'")
            except Exception as e:
                print(f"ERROR: {str(e)[:60]}")


# ── Test 6: Personality wrapping ─────────────────────────────────
def test_personality():
    divider("Test 6: Personality wrapping")

    instructions = """You are Andrew, a friendly but precise accounting assistant for a construction company.
Rewrite the following system message in your personality. Keep it concise (1-2 sentences max).
Do not add information that isn't in the original message."""

    messages = [
        "Receipt processed. 3 expenses created totaling $450.",
        "Authorization check complete. 12 expenses authorized, 2 flagged for review.",
        "Missing information: vendor name and project assignment needed.",
    ]

    for model in MODELS:
        print(f"\n  [{model}]")
        for msg in messages:
            print(f"    wrap: '{msg[:45]}...'", end=" ", flush=True)
            try:
                start = time.time()
                r = client.responses.create(
                    model=model,
                    instructions=instructions,
                    input=msg,
                )
                elapsed = time.time() - start
                content = r.output_text if hasattr(r, 'output_text') else ""
                print(f"OK ({int(elapsed*1000)}ms)")
                print(f"      -> '{content.strip()[:80]}'")
            except Exception as e:
                print(f"ERROR: {str(e)[:60]}")


# ── Test 7: Vision support ──────────────────────────────────────
def test_vision():
    divider("Test 7: Vision support")
    test_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="

    for model in MODELS:
        print(f"\n  [{model}]...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.responses.create(
                model=model,
                input=[
                    {"role": "user", "content": [
                        {"type": "input_text", "text": "What color is this image? One word."},
                        {"type": "input_image", "image_url": f"data:image/png;base64,{test_image}"},
                    ]}
                ],
            )
            elapsed = time.time() - start
            content = r.output_text if hasattr(r, 'output_text') else ""
            print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) -> '{content.strip()[:40]}'")
        except Exception as e:
            err = str(e)
            if "image" in err.lower() or "vision" in err.lower() or "not support" in err.lower():
                print(f"NO VISION")
            else:
                print(f"ERROR: {err[:80]}")


# ── Run ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 65)
    print("  GPT-5 Family via responses API")
    print(f"  Models: {', '.join(MODELS)}")
    print("=" * 65)

    test_basic()
    test_instructions()
    test_json()
    test_routing()
    test_context_extraction()
    test_personality()
    test_vision()

    divider("COST COMPARISON")
    print("""
  Model        Input/1M   Cached/1M  Output/1M   Speed    Reasoning
  ------------ --------   ---------  ---------   -------  ---------
  gpt-5-nano   $0.05      $0.01      $0.40       5/5      2/5
  gpt-5-mini   $0.25      $0.03      $2.00       4/5      3/5
  gpt-5.2      $1.75      $0.18      $14.00      3/5      5/5

  gpt-5-nano is 35x cheaper (input) and 35x cheaper (output) than gpt-5.2
  gpt-5-mini is 7x cheaper (input) and 7x cheaper (output) than gpt-5.2
""")
