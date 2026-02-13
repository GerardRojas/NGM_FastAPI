"""
Test cheap/fast model alternatives for lightweight tasks.
Goal: Find a model cheaper than gpt-5.2 ($1.75/$14.00) for simple tasks
like personality wrapping, simple classification, chat responses.
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
    print(f"\n{'=' * 65}")
    print(f"  {title}")
    print(f"{'=' * 65}")


# ── Candidate models ─────────────────────────────────────────────
CANDIDATES = [
    # From user's documentation
    "gpt-5.2-instant",
    "gpt-5.2-chat-latest",
    # Potentially cheaper models (older gen, mini variants)
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1",
    # Current baseline for comparison
    "gpt-5.2",
]


# ── Test via chat.completions ────────────────────────────────────
def test_chat_completions():
    divider("chat.completions API")

    for model in CANDIDATES:
        print(f"\n  [{model}]")

        # Basic
        print(f"    basic...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Reply with exactly: OK"},
                    {"role": "user", "content": "Status"},
                ],
                max_completion_tokens=10,
            )
            elapsed = time.time() - start
            content = r.choices[0].message.content or ""
            usage = r.usage
            in_t = usage.prompt_tokens if usage else "?"
            out_t = usage.completion_tokens if usage else "?"
            print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) tok={in_t}/{out_t} -> '{content.strip()[:30]}'")
        except Exception as e:
            err = str(e)
            if "404" in err:
                print(f"404 NOT FOUND")
            elif "temperature" in err.lower():
                print(f"TEMP ERROR (retry without temp)...")
                # Retry without temperature
                try:
                    start = time.time()
                    r = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": "Reply with exactly: OK"},
                            {"role": "user", "content": "Status"},
                        ],
                        max_completion_tokens=10,
                    )
                    elapsed = time.time() - start
                    content = r.choices[0].message.content or ""
                    print(f"              -> {'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) -> '{content.strip()[:30]}'")
                except Exception as e2:
                    print(f"              -> ERROR: {str(e2)[:80]}")
            else:
                print(f"ERROR: {err[:80]}")
            continue

        # JSON mode
        print(f"    json...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Return JSON: {\"intent\": str, \"confidence\": float}"},
                    {"role": "user", "content": "show expenses for project Alpha"},
                ],
                max_completion_tokens=100,
                response_format={"type": "json_object"},
            )
            elapsed = time.time() - start
            content = r.choices[0].message.content or ""
            try:
                parsed = json.loads(content)
                print(f"OK ({int(elapsed*1000)}ms) -> {json.dumps(parsed)[:60]}")
            except:
                print(f"{'EMPTY' if not content.strip() else 'BAD JSON'} ({int(elapsed*1000)}ms)")
        except Exception as e:
            print(f"ERROR: {str(e)[:60]}")

        # Personality wrap (our key cheap use case)
        print(f"    personality...", end=" ", flush=True)
        try:
            start = time.time()
            r = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are Andrew, a friendly accounting assistant. Rewrite this response in your personality: 'Receipt processed. 3 expenses created totaling $450.'"},
                    {"role": "user", "content": "Wrap this message"},
                ],
                temperature=0.4,
                max_completion_tokens=150,
            )
            elapsed = time.time() - start
            content = r.choices[0].message.content or ""
            print(f"{'OK' if content.strip() else 'EMPTY'} ({int(elapsed*1000)}ms) -> '{content.strip()[:60]}'")
        except Exception as e:
            err = str(e)
            if "temperature" in err.lower():
                print(f"NO TEMP CONTROL")
            else:
                print(f"ERROR: {err[:60]}")


# ── Test via responses API ───────────────────────────────────────
def test_responses_api():
    divider("responses API")

    for model in CANDIDATES:
        print(f"\n  [{model}]", end=" ", flush=True)
        try:
            start = time.time()
            r = client.responses.create(
                model=model,
                input="Reply with exactly: OK",
            )
            elapsed = time.time() - start
            content = r.output_text if hasattr(r, 'output_text') else str(r)[:100]
            usage = r.usage if hasattr(r, 'usage') else None
            in_t = usage.input_tokens if usage and hasattr(usage, 'input_tokens') else "?"
            out_t = usage.output_tokens if usage and hasattr(usage, 'output_tokens') else "?"
            print(f"OK ({int(elapsed*1000)}ms) tok={in_t}/{out_t} -> '{str(content).strip()[:30]}'")
        except Exception as e:
            err = str(e)
            if "404" in err or "does not exist" in err:
                print(f"NOT FOUND")
            elif "not a chat model" in err.lower():
                print(f"NOT CHAT MODEL (responses only?)")
            else:
                print(f"ERROR: {err[:80]}")


# ── Summary ──────────────────────────────────────────────────────
def print_summary():
    divider("PRICING REFERENCE (from OpenAI docs)")
    print("""
  Model               Input/1M    Output/1M   Cached/1M   Notes
  ------------------- ----------  ----------  ----------  --------
  gpt-4o-mini         $0.15       $0.60       $0.075      OLD gen
  gpt-4.1-nano        $0.10       $0.40       $0.025      Cheapest
  gpt-4.1-mini        $0.40       $1.60       $0.10       Good value
  gpt-4.1             $2.00       $8.00       $0.50       Full 4.1
  gpt-4o              $2.50       $10.00      $1.25       OLD gen
  gpt-5.2             $1.75       $14.00      $0.175      Current
  gpt-5.2-pro         $21.00      $168.00     ???         Heavy

  * Prices from official OpenAI docs. May vary.
  * gpt-4.1-nano at $0.10/$0.40 = 17.5x cheaper input, 35x cheaper output vs gpt-5.2
""")


if __name__ == "__main__":
    print("=" * 65)
    print("  Cheap Model Alternatives for NGM HUB")
    print(f"  OpenAI SDK: {__import__('openai').__version__}")
    print("=" * 65)

    test_chat_completions()
    test_responses_api()
    print_summary()
