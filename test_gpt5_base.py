#!/usr/bin/env python3
"""
GPT-5 Base Model Test
=====================
Test specifically the gpt-5 base model (not 5.1 or 5.2)
"""

import os
import json
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

async def test_gpt5_base():
    """Test gpt-5 base model with multiple scenarios."""
    print("\n" + "="*80)
    print(" GPT-5 BASE MODEL TEST (not 5.1 or 5.2)")
    print("="*80 + "\n")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY not found")
        return

    client = AsyncOpenAI(api_key=api_key)
    model = "gpt-5"

    # Test 1: Simple JSON extraction
    print("\n" + "-"*80)
    print("TEST 1: JSON Routing Decision")
    print("-"*80)

    try:
        response1 = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": """You are a routing agent. Respond with ONLY a JSON object:
{"action": "function_call", "function": "process_receipt"}

User message: "@Andrew process this receipt"
"""
                }
            ],
            temperature=1,
            max_completion_tokens=100
        )

        result1 = response1.choices[0].message.content
        tokens1 = response1.usage.total_tokens

        print(f"[Result] Length: {len(result1) if result1 else 0} chars")
        print(f"[Tokens] {tokens1}")
        if result1:
            print(f"[Content Preview]\n{result1[:200]}")
        else:
            print("[Content] EMPTY RESPONSE")

    except Exception as e:
        print(f"[ERROR] {str(e)[:200]}")

    # Test 2: Context extraction
    print("\n" + "-"*80)
    print("TEST 2: Context Extraction")
    print("-"*80)

    try:
        response2 = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": """Extract project name from: "pago de drywall para trasher"

Return JSON: {"project": "...", "category": "..."}
"""
                }
            ],
            temperature=1,
            max_completion_tokens=100
        )

        result2 = response2.choices[0].message.content
        tokens2 = response2.usage.total_tokens

        print(f"[Result] Length: {len(result2) if result2 else 0} chars")
        print(f"[Tokens] {tokens2}")
        if result2:
            print(f"[Content Preview]\n{result2[:200]}")
        else:
            print("[Content] EMPTY RESPONSE")

    except Exception as e:
        print(f"[ERROR] {str(e)[:200]}")

    # Test 3: Conversational
    print("\n" + "-"*80)
    print("TEST 3: Conversational Response")
    print("-"*80)

    try:
        response3 = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are Andrew, a helpful AI assistant. Respond to: 'Hello Andrew, how are you?'"
                }
            ],
            temperature=1,
            max_completion_tokens=100
        )

        result3 = response3.choices[0].message.content
        tokens3 = response3.usage.total_tokens

        print(f"[Result] Length: {len(result3) if result3 else 0} chars")
        print(f"[Tokens] {tokens3}")
        if result3:
            print(f"[Content Preview]\n{result3[:200]}")
        else:
            print("[Content] EMPTY RESPONSE")

    except Exception as e:
        print(f"[ERROR] {str(e)[:200]}")

    # Summary
    print("\n" + "="*80)
    print(" SUMMARY: gpt-5 Base Model Testing")
    print("="*80)
    print("\nConclusion:")
    print("  - Model: gpt-5 (base, not 5.1 or 5.2)")
    print("  - Temperature: 1 (default)")
    print("  - All tests completed above")
    print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(test_gpt5_base())
