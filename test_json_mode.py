#!/usr/bin/env python3
"""
Test GPT-5 Models with JSON Mode
=================================
Test if gpt-5-nano, gpt-5-mini, gpt-5 work with response_format JSON mode
"""

import os
import json
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

async def test_json_mode():
    """Test gpt-5 models with JSON response format."""
    print("\n" + "="*80)
    print(" GPT-5 JSON MODE TEST")
    print(" Using chat.completions.create() with response_format JSON")
    print("="*80 + "\n")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY not found")
        return

    client = AsyncOpenAI(api_key=api_key)

    models_to_test = [
        ("gpt-5-nano", "GPT-5 Nano"),
        ("gpt-5-mini", "GPT-5 Mini"),
        ("gpt-5", "GPT-5 Base"),
        ("gpt-5.1", "GPT-5.1 - Control"),
        ("gpt-5.2", "GPT-5.2 - Control"),
    ]

    for model_name, model_label in models_to_test:
        print("\n" + "-"*80)
        print(f"Testing: {model_label} ({model_name})")
        print("-"*80)

        # Test 1: WITH response_format JSON
        print("\n[TEST 1] WITH response_format JSON")
        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": 'Extract project name from: "pago de drywall para trasher". Return ONLY JSON: {"project": "..."}'
                    }
                ],
                response_format={"type": "json_object"},
                temperature=1,
                max_completion_tokens=100,
            )

            result = response.choices[0].message.content
            tokens = response.usage.total_tokens

            print(f"[Result] Length: {len(result) if result else 0} chars")
            print(f"[Tokens] {tokens}")
            if result:
                print(f"[Content] {result[:200]}")
            else:
                print("[Content] EMPTY RESPONSE")

        except Exception as e:
            print(f"[ERROR] {str(e)[:200]}")

        # Test 2: WITHOUT response_format (normal mode - like our current code)
        print("\n[TEST 2] WITHOUT response_format (current production mode)")
        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": 'Extract project name from: "pago de drywall para trasher". Return ONLY JSON: {"project": "..."}'
                    }
                ],
                # NO response_format parameter
                temperature=1,
                max_completion_tokens=100,
            )

            result = response.choices[0].message.content
            tokens = response.usage.total_tokens

            print(f"[Result] Length: {len(result) if result else 0} chars")
            print(f"[Tokens] {tokens}")
            if result:
                print(f"[Content] {result[:200]}")
            else:
                print("[Content] EMPTY RESPONSE")

        except Exception as e:
            print(f"[ERROR] {str(e)[:200]}")

        await asyncio.sleep(0.5)

    print("\n" + "="*80)
    print(" SUMMARY")
    print("="*80)
    print("\nIf nano/mini/base work WITH json_mode but NOT without it,")
    print("then the fix is to add response_format={'type': 'json_object'}")
    print("to all GPT calls that expect JSON responses.")
    print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(test_json_mode())
