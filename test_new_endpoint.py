#!/usr/bin/env python3
"""
Test GPT-5 Models with NEW Endpoint
====================================
Test if gpt-5-nano, gpt-5-mini, gpt-5 work with the new responses.create() endpoint
instead of the old chat.completions.create() endpoint.
"""

import os
import json
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

async def test_new_endpoint():
    """Test gpt-5 models with the new responses.create() endpoint."""
    print("\n" + "="*80)
    print(" GPT-5 NEW ENDPOINT TEST")
    print(" Using client.responses.create() instead of chat.completions.create()")
    print("="*80 + "\n")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY not found")
        return

    client = AsyncOpenAI(api_key=api_key)

    # Models to test
    models_to_test = [
        ("gpt-5-nano", "GPT-5 Nano"),
        ("gpt-5-mini", "GPT-5 Mini"),
        ("gpt-5", "GPT-5 Base"),
    ]

    for model_name, model_label in models_to_test:
        print("\n" + "-"*80)
        print(f"Testing: {model_label} ({model_name})")
        print("-"*80)

        try:
            # Use the NEW endpoint: responses.create()
            response = await client.responses.create(
                model=model_name,
                input="Extract the project name from this text: 'pago de drywall para trasher'. Respond with ONLY a JSON object: {\"project\": \"...\", \"category\": \"...\"}",
            )

            # Check response structure
            print(f"\n[Response Type] {type(response)}")
            print(f"[Response Dir] {dir(response)}")

            # Try to access the content
            if hasattr(response, 'output'):
                result = response.output
                print(f"\n[Result] Length: {len(result) if result else 0} chars")
                if result:
                    print(f"[Content]\n{result[:300]}")
                else:
                    print("[Content] EMPTY")
            elif hasattr(response, 'text'):
                result = response.text
                print(f"\n[Result] Length: {len(result) if result else 0} chars")
                if result:
                    print(f"[Content]\n{result[:300]}")
                else:
                    print("[Content] EMPTY")
            else:
                print(f"\n[WARNING] Unknown response structure")
                print(f"[Full Response] {response}")

            # Try to get token usage
            if hasattr(response, 'usage'):
                print(f"[Tokens] {response.usage}")

        except AttributeError as e:
            print(f"\n[ERROR] AttributeError: {str(e)}")
            print("[INFO] The responses.create() endpoint might not exist")
            print("[INFO] Trying with 'beta.chat.completions.parse()' instead...")

            # Alternative: Try beta endpoint
            try:
                response = await client.beta.chat.completions.parse(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "Extract project name from: 'pago de drywall para trasher'. Return JSON: {\"project\": \"...\"}"}
                    ],
                    response_format={"type": "json_object"},
                )
                result = response.choices[0].message.content
                print(f"\n[BETA ENDPOINT SUCCESS]")
                print(f"[Result] Length: {len(result) if result else 0} chars")
                if result:
                    print(f"[Content]\n{result[:300]}")
                else:
                    print("[Content] EMPTY")
                print(f"[Tokens] {response.usage.total_tokens}")
            except Exception as beta_error:
                print(f"[BETA ERROR] {str(beta_error)[:200]}")

        except Exception as e:
            print(f"\n[ERROR] {type(e).__name__}: {str(e)[:300]}")

        await asyncio.sleep(1)

    print("\n" + "="*80)
    print(" Testing Complete")
    print("="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(test_new_endpoint())
