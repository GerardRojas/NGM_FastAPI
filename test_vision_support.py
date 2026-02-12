#!/usr/bin/env python3
"""
GPT-5 Vision Support Test
==========================
Test which GPT-5 models support vision/image processing (OCR capabilities).

This is critical for Andrew's receipt processing system.
"""

import os
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# Test image: Simple receipt in base64
# Small PNG image with text "RECEIPT - TOTAL: $100.00"
TEST_IMAGE_BASE64 = """data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="""
# This is a 1x1 pixel image (minimal for testing), in production we'd use actual receipt images

# Models to test
MODELS_TO_TEST = [
    ("gpt-5-nano", "GPT-5 Nano"),
    ("gpt-5-mini", "GPT-5 Mini"),
    ("gpt-5", "GPT-5 Base"),
    ("gpt-5.1", "GPT-5.1"),
    ("gpt-5.2", "GPT-5.2"),
]


async def test_vision_support(model_name: str, model_label: str):
    """Test if a model supports vision/image processing."""
    print(f"\n{'='*80}")
    print(f"Testing: {model_label} ({model_name})")
    print(f"{'='*80}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY not found")
        return False

    try:
        client = AsyncOpenAI(api_key=api_key)

        # Attempt to send an image with a prompt
        print(f"[>>] Sending image analysis request...")

        response = await client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "What do you see in this image? Describe it briefly."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": TEST_IMAGE_BASE64
                            }
                        }
                    ]
                }
            ],
            max_completion_tokens=200,
        )

        result = response.choices[0].message.content.strip()
        tokens = response.usage.total_tokens

        if result:
            print(f"[OK] Vision SUPPORTED")
            print(f"\nResponse preview:")
            print("-" * 80)
            print(result[:200] + ("..." if len(result) > 200 else ""))
            print("-" * 80)
            print(f"\n[STATS] Tokens used: {tokens}")
            return True
        else:
            print(f"[WARN] Model responded but with empty content")
            print(f"[STATS] Tokens used: {tokens}")
            return False

    except Exception as e:
        error_msg = str(e)

        # Check if it's a vision not supported error
        if "does not support" in error_msg.lower() or "vision" in error_msg.lower():
            print(f"[X] Vision NOT SUPPORTED")
            print(f"    Error: {error_msg[:150]}")
            return False
        else:
            print(f"[ERROR] {error_msg[:200]}")
            return False


async def test_receipt_ocr(model_name: str, model_label: str):
    """Test OCR on a receipt-like image (if vision is supported)."""
    print(f"\n{'='*80}")
    print(f"Receipt OCR Test: {model_label} ({model_name})")
    print(f"{'='*80}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return False

    try:
        client = AsyncOpenAI(api_key=api_key)

        # Receipt-specific prompt
        print(f"[>>] Testing receipt OCR capabilities...")

        response = await client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": """Analyze this receipt/invoice image and extract:
1. Vendor name
2. Total amount
3. Date
4. Line items (if visible)

Return ONLY a JSON object with this structure:
{
  "vendor": "...",
  "total": 0.00,
  "date": "...",
  "items": ["..."]
}"""
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": TEST_IMAGE_BASE64
                            }
                        }
                    ]
                }
            ],
            max_completion_tokens=300,
        )

        result = response.choices[0].message.content.strip()
        tokens = response.usage.total_tokens

        print(f"[OK] OCR Response:")
        print("-" * 80)
        print(result[:400] if len(result) > 400 else result)
        print("-" * 80)
        print(f"\n[STATS] Tokens used: {tokens}")
        return True

    except Exception as e:
        print(f"[ERROR] {str(e)[:200]}")
        return False


async def main():
    """Run all vision tests."""
    print("\n" + "="*80)
    print(" GPT-5 VISION SUPPORT TESTING")
    print(" Testing which models can process images (critical for receipt OCR)")
    print("="*80)

    results = {}

    # Test basic vision support for each model
    print("\n\n## PHASE 1: Basic Vision Support Test")
    print("="*80)

    for model_name, model_label in MODELS_TO_TEST:
        supported = await test_vision_support(model_name, model_label)
        results[model_name] = supported
        await asyncio.sleep(1)  # Brief pause between tests

    # Test receipt OCR on models that support vision
    print("\n\n## PHASE 2: Receipt OCR Test (only models with vision)")
    print("="*80)

    vision_models = [
        (name, label) for (name, label) in MODELS_TO_TEST
        if results.get(name, False)
    ]

    if vision_models:
        for model_name, model_label in vision_models:
            await test_receipt_ocr(model_name, model_label)
            await asyncio.sleep(1)
    else:
        print("\n[WARN] No models with vision support found. Skipping OCR test.")

    # Summary
    print("\n\n" + "="*80)
    print(" SUMMARY: Vision Support by Model")
    print("="*80)

    for model_name, model_label in MODELS_TO_TEST:
        status = "[OK] SUPPORTED" if results.get(model_name, False) else "[X] NOT SUPPORTED"
        print(f"  {model_label:20} ({model_name:15}) -> {status}")

    print("\n" + "="*80)
    print(" Testing Complete!")
    print("="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
