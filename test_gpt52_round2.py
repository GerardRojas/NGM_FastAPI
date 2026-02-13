"""
Round 2: Deeper testing based on Round 1 findings.
- gpt-5.2-chat-latest: test WITHOUT temperature (default only)
- gpt-5.2-pro: test with responses API instead of chat.completions
- gpt-5.1: test JSON mode (it worked) vs basic (empty)
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


# ── Test A: gpt-5.2-chat-latest WITHOUT temperature ─────────────
def test_chat_latest():
    divider("gpt-5.2-chat-latest (no temperature param)")

    # A1: Basic, no temperature
    print("\n  [A1] Basic (default temp)...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.chat.completions.create(
            model="gpt-5.2-chat-latest",
            messages=[
                {"role": "system", "content": "Reply with exactly: OK"},
                {"role": "user", "content": "Status check"},
            ],
            max_completion_tokens=10,
        )
        elapsed = time.time() - start
        content = r.choices[0].message.content or ""
        print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) -> '{content.strip()[:50]}'")
        print(f"         Tokens: in={r.usage.prompt_tokens}, out={r.usage.completion_tokens}")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")

    # A2: JSON mode, no temperature
    print("  [A2] JSON mode (default temp)...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.chat.completions.create(
            model="gpt-5.2-chat-latest",
            messages=[
                {"role": "system", "content": "Return JSON: {\"action\": str, \"confidence\": float}"},
                {"role": "user", "content": "Categorize: drywall materials for project A"},
            ],
            max_completion_tokens=100,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start
        content = r.choices[0].message.content or ""
        try:
            parsed = json.loads(content)
            print(f"OK ({int(elapsed*1000)}ms) -> {json.dumps(parsed)[:80]}")
        except:
            print(f"INVALID JSON ({int(elapsed*1000)}ms) -> '{content[:80]}'")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")

    # A3: Vision, no temperature
    test_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
    print("  [A3] Vision (default temp)...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.chat.completions.create(
            model="gpt-5.2-chat-latest",
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": "What color is this image? One word."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{test_image}"}},
                ]},
            ],
            max_completion_tokens=20,
        )
        elapsed = time.time() - start
        content = r.choices[0].message.content or ""
        print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) -> '{content.strip()[:50]}'")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")

    # A4: Routing simulation (our actual use case)
    print("  [A4] Agent routing (default temp)...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.chat.completions.create(
            model="gpt-5.2-chat-latest",
            messages=[
                {"role": "system", "content": """You route user requests. Return JSON:
{"decision": "function_call"|"free_chat"|"clarify", "function": "process_receipt"|"run_auto_auth"|null, "reason": "brief"}"""},
                {"role": "user", "content": "@Andrew I have a receipt from Home Depot for $450, materials for project Sunrise"},
            ],
            max_completion_tokens=150,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start
        content = r.choices[0].message.content or ""
        try:
            parsed = json.loads(content)
            print(f"OK ({int(elapsed*1000)}ms)")
            print(f"         -> {json.dumps(parsed, indent=None)[:100]}")
        except:
            print(f"INVALID ({int(elapsed*1000)}ms) -> '{content[:80]}'")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")


# ── Test B: gpt-5.2-pro via responses API ────────────────────────
def test_pro():
    divider("gpt-5.2-pro (responses API)")

    # B1: Try the responses API
    print("\n  [B1] responses.create...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.responses.create(
            model="gpt-5.2-pro",
            input="Reply with exactly: OK",
        )
        elapsed = time.time() - start
        # responses API returns different structure
        content = r.output_text if hasattr(r, 'output_text') else str(r.output)[:100]
        print(f"OK ({int(elapsed*1000)}ms) -> '{str(content).strip()[:50]}'")
        if hasattr(r, 'usage'):
            u = r.usage
            print(f"         Tokens: in={u.input_tokens}, out={u.output_tokens}")
    except Exception as e:
        print(f"ERROR -> {str(e)[:120]}")

    # B2: JSON via responses API
    print("  [B2] responses.create JSON...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.responses.create(
            model="gpt-5.2-pro",
            input="Categorize 'drywall materials'. Return JSON: {\"action\": str, \"confidence\": float}",
        )
        elapsed = time.time() - start
        content = r.output_text if hasattr(r, 'output_text') else str(r.output)[:200]
        print(f"OK ({int(elapsed*1000)}ms) -> '{str(content).strip()[:80]}'")
    except Exception as e:
        print(f"ERROR -> {str(e)[:120]}")


# ── Test C: gpt-5.1 deeper analysis ─────────────────────────────
def test_51_deeper():
    divider("gpt-5.1 (deeper analysis)")

    # C1: Why does basic return empty but JSON works?
    # Try with explicit system prompt
    print("\n  [C1] Explicit system prompt...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Always respond to the user."},
                {"role": "user", "content": "Say hello"},
            ],
            temperature=0.4,
            max_completion_tokens=50,
        )
        elapsed = time.time() - start
        content = r.choices[0].message.content or ""
        print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) -> '{content.strip()[:50]}'")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")

    # C2: Try without temperature
    print("  [C2] No temperature param...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": "Reply with exactly: OK"},
                {"role": "user", "content": "Status check"},
            ],
            max_completion_tokens=10,
        )
        elapsed = time.time() - start
        content = r.choices[0].message.content or ""
        print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) -> '{content.strip()[:50]}'")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")

    # C3: JSON mode (confirmed working in Round 1)
    print("  [C3] JSON mode (confirmed)...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": "Return JSON: {\"status\": \"ok\"}"},
                {"role": "user", "content": "Check"},
            ],
            temperature=0.0,
            max_completion_tokens=50,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start
        content = r.choices[0].message.content or ""
        print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) -> '{content.strip()[:50]}'")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")

    # C4: responses API for gpt-5.1
    print("  [C4] responses API...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.responses.create(
            model="gpt-5.1",
            input="Reply with exactly: OK",
        )
        elapsed = time.time() - start
        content = r.output_text if hasattr(r, 'output_text') else str(r.output)[:100]
        print(f"OK ({int(elapsed*1000)}ms) -> '{str(content).strip()[:50]}'")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")


# ── Test D: gpt-5.2 via responses API (compare) ─────────────────
def test_52_responses():
    divider("gpt-5.2 via responses API (comparison)")

    print("\n  [D1] responses.create...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.responses.create(
            model="gpt-5.2",
            input="Reply with exactly: OK",
        )
        elapsed = time.time() - start
        content = r.output_text if hasattr(r, 'output_text') else str(r.output)[:100]
        print(f"OK ({int(elapsed*1000)}ms) -> '{str(content).strip()[:50]}'")
        if hasattr(r, 'usage'):
            u = r.usage
            print(f"         Tokens: in={u.input_tokens}, out={u.output_tokens}")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")


# ── Test E: gpt-5.2-chat-latest via responses API ───────────────
def test_chat_latest_responses():
    divider("gpt-5.2-chat-latest via responses API")

    print("\n  [E1] responses.create...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.responses.create(
            model="gpt-5.2-chat-latest",
            input="Reply with exactly: OK",
        )
        elapsed = time.time() - start
        content = r.output_text if hasattr(r, 'output_text') else str(r.output)[:100]
        print(f"OK ({int(elapsed*1000)}ms) -> '{str(content).strip()[:50]}'")
        if hasattr(r, 'usage'):
            u = r.usage
            print(f"         Tokens: in={u.input_tokens}, out={u.output_tokens}")
    except Exception as e:
        print(f"ERROR -> {str(e)[:100]}")

    # E2: With instructions (system-like)
    print("  [E2] responses with instructions...", end=" ", flush=True)
    try:
        start = time.time()
        r = client.responses.create(
            model="gpt-5.2-chat-latest",
            instructions="You route user requests. Return JSON with keys: decision, function, reason",
            input="@Andrew I have a receipt from Home Depot for $450",
        )
        elapsed = time.time() - start
        content = r.output_text if hasattr(r, 'output_text') else str(r.output)[:200]
        print(f"OK ({int(elapsed*1000)}ms)")
        print(f"         -> '{str(content).strip()[:100]}'")
    except Exception as e:
        print(f"ERROR -> {str(e)[:120]}")


# ── Run ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  GPT-5.2 Round 2 - Deep Validation")
    print("=" * 60)

    test_chat_latest()
    test_pro()
    test_51_deeper()
    test_52_responses()
    test_chat_latest_responses()

    divider("DONE")
