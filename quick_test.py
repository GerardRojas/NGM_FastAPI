#!/usr/bin/env python3
"""
Quick GPT Test - Prueba rápida de un modelo específico con tu propio prompt

Usage:
  python quick_test.py nano "Extract intent from: pago de pintura $500"
  python quick_test.py mini "Respond friendly: The receipt is ready"
  python quick_test.py medium "Route this: check the budget"
  python quick_test.py heavy "Analyze mismatch: receipt=$1000, db=$850"
"""

import os
import sys
import json
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# Model mapping (using GPT-5 models - 2026)
MODELS = {
    "nano": "gpt-5-nano",       # GPT-5 Nano: fastest, cheapest
    "mini": "gpt-5-mini",       # GPT-5 Mini: fast and cost-efficient
    "medium": "gpt-5",          # GPT-5: base model
    "heavy": "gpt-5.2",         # GPT-5.2: flagship for agentic tasks
}

# Default temperatures per tier
# Note: gpt-5-nano and gpt-5-mini may only support temperature=1 (default)
TEMPS = {
    "nano": 1,      # GPT-5-nano: testing with default
    "mini": 1,      # GPT-5-mini: testing with default
    "medium": 0.1,  # GPT-5: testing custom temperature
    "heavy": 0.2,   # GPT-5.2: supports custom temperatures
}


async def quick_test(tier: str, user_prompt: str, temperature: float = None):
    """Run a quick test with custom prompt."""
    if tier not in MODELS:
        print(f"[ERROR] Invalid tier: {tier}")
        print(f"   Valid options: {', '.join(MODELS.keys())}")
        sys.exit(1)

    model = MODELS[tier]
    temp = temperature if temperature is not None else TEMPS[tier]

    print(f"\n{'='*80}")
    print(f"Quick Test - {tier.upper()} tier")
    print(f"Model: {model} | Temperature: {temp}")
    print(f"{'='*80}\n")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] ERROR: OPENAI_API_KEY not found in environment")
        sys.exit(1)

    print(f"[>>] Your prompt:\n   {user_prompt}\n")
    print("[WAIT] Waiting for response...\n")

    try:
        client = AsyncOpenAI(api_key=api_key)

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": user_prompt},
            ],
            temperature=temp,
            max_completion_tokens=500,
        )

        result = response.choices[0].message.content.strip()

        print("[OK] Response:\n")
        print("-" * 80)

        # Try to parse as JSON for pretty printing
        try:
            parsed = json.loads(result)
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            # Not JSON, print as-is
            print(result)

        print("-" * 80)

        # Show token usage
        usage = response.usage
        print(f"\n[STATS] Tokens: {usage.prompt_tokens} prompt + {usage.completion_tokens} completion = {usage.total_tokens} total\n")

    except Exception as e:
        print(f"[ERROR] ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def show_usage():
    """Show usage examples."""
    print("""
Quick GPT Test - Test any GPT tier with your own prompt

Usage:
  python quick_test.py <tier> "<your_prompt>" [temperature]

Tiers:
  nano    - gpt-5-nano (Internal: parsing, NLU)
  mini    - gpt-5-mini (Chat: personality, conversation)
  medium  - gpt-5-1 (Medium: routing, categorization)
  heavy   - gpt-5-2 (Heavy: complex analysis, reconciliation)

Examples:
  # Test context extraction (nano)
  python quick_test.py nano "Extract intent: mitad para este proyecto"

  # Test personality (mini)
  python quick_test.py mini "Respond as Andrew: The receipt looks good"

  # Test routing decision (medium)
  python quick_test.py medium "Route this message: check budget for project"

  # Test mismatch analysis (heavy)
  python quick_test.py heavy "Analyze: receipt total $1000, db has $850"

  # Custom temperature
  python quick_test.py mini "Be creative!" 0.8

Tips:
  - Use quotes around your prompt if it has spaces
  - nano/medium work best with structured outputs (JSON)
  - mini/heavy can handle more open-ended questions
  - Lower temperature = more deterministic, higher = more creative
    """)


def main():
    if len(sys.argv) < 3:
        show_usage()
        sys.exit(1)

    tier = sys.argv[1].lower()
    prompt = sys.argv[2]

    # Optional temperature
    temp = None
    if len(sys.argv) >= 4:
        try:
            temp = float(sys.argv[3])
        except ValueError:
            print(f"[ERROR] Invalid temperature: {sys.argv[3]}")
            sys.exit(1)

    asyncio.run(quick_test(tier, prompt, temp))


if __name__ == "__main__":
    main()
