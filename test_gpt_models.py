#!/usr/bin/env python3
"""
Manual GPT Model Testing Script
================================
Test the different GPT model tiers used in Andrew agent system.

Usage:
  python test_gpt_models.py

Model Tiers:
  - Internal (gpt-5-nano): NLU, parsing, fuzzy matching
  - Chat (gpt-5-mini): Personality, smart layers, conversation
  - Medium (gpt-5-1): OCR, categorization, brain routing
  - Heavy (gpt-5-2): Mismatch reconciliation, duplicate resolution
"""

import os
import json
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============================================================================
# Test Prompts (based on real Andrew use cases)
# ============================================================================

TESTS = {
    "internal": {
        "model": "gpt-5-nano",  # GPT-5 Nano - fastest, cheapest
        "description": "Internal tier - Context extraction from user message",
        "prompt": """You are analyzing a user's message that accompanies a receipt/invoice upload.
Extract structured intent from their text. The user may write in English or Spanish.

Current project: Trasher Remodel

User message: "pago de drywall para trasher, mitad para este proyecto y mitad para main street"

Extract a JSON object with these fields (use null for anything not mentioned):

{
  "project_decision": "all_this_project" | "split" | null,
  "split_projects": [
    {"name": "<project name or 'this_project'>", "portion": "<half/third/etc or null>", "amount": <number or null>}
  ] or null,
  "category_hints": ["<account/category names mentioned>"] or null,
  "vendor_hint": "<vendor name>" or null,
  "amount_hint": <number> or null,
  "date_hint": "<date string>" or null
}

Return ONLY the JSON object. No explanation.""",
        "temperature": 1,  # GPT-5-nano only supports default temperature=1
        "max_tokens": 250,
    },

    "chat": {
        "model": "gpt-5-mini",  # GPT-5 Mini - fast and cost-efficient
        "description": "Chat tier - Personality wrapper for Andrew",
        "system": """You are Andrew, the Receipt Processing AI agent for NGM Construction.

Your personality:
- Helpful and professional
- Proactive about catching errors
- Clear communicator who explains accounting concepts simply
- Uses occasional light humor but stays focused on the task
- Addresses users by name when known
- Uses markdown for formatting

Your communication style:
- Be concise but friendly
- Use bullet points for clarity
- Bold important amounts or decisions
- Ask clarifying questions when needed
- Confirm actions before executing

Respond to this message with Andrew's personality:""",
        "user_message": "Great! All the categories look correct. Please create the expenses.",
        "temperature": 1,  # GPT-5-mini only supports default temperature=1
        "max_tokens": 300,
    },

    "medium": {
        "model": "gpt-5",  # GPT-5 base model
        "description": "Medium tier - Brain routing decision",
        "prompt": """You are the routing brain for Andrew, the Receipt Processing AI agent.

You are receiving a message from a user in a project channel.
Your job: decide what to do with it.

## Your capabilities
1. process_receipt - Process uploaded receipt/invoice files (PDF, images)
2. check_receipt_status - Look up status of pending receipts
3. explain_categorization - Explain why an expense got a certain category
4. check_budget - Budget vs actuals report for the project

## Instructions
Analyze the user's message and respond with a JSON object (no markdown fences):

1. If the user wants you to execute one of your functions:
   {"action": "function_call", "function": "<function_name>", "parameters": {...}, "ack_message": "<short acknowledgment>"}

2. If the user is just chatting, asking a question about you, or saying hello:
   {"action": "free_chat", "response": "<your conversational reply>"}

3. If you need more information to proceed:
   {"action": "clarify", "question": "<what you need to know>"}

## Context
- Project: Trasher Remodel (ID: abc-123)
- User: German
- Channel: receipts
- Attachments:
  - receipt_march_05.pdf (application/pdf)

## Recent conversation
[German]: @Andrew here's the Home Depot receipt from last week
[Andrew]: Receipt scanned: **Home Depot** -- $1,234.56

Respond with ONLY the JSON object. No explanation, no markdown.

User message: "@Andrew process this receipt"
""",
        "temperature": 1,  # GPT-5 base only supports temperature=1
        "max_tokens": 300,
    },

    "heavy": {
        "model": "gpt-5.2",  # GPT-5.2 - flagship for coding and agentic tasks
        "description": "Heavy tier - Mismatch reconciliation",
        "prompt": """You are Andrew's mismatch reconciliation specialist.

A bill has been scanned but the sum of individual expenses doesn't match the receipt total.

Receipt details:
- Vendor: Lowe's
- Receipt total: $1,048.05
- Receipt date: 2024-03-15
- Receipt items from OCR:
  1. Lumber 2x4x8 (10 pcs) - $47.90
  2. Nails 3" box - $12.50
  3. Drywall sheets (15) - $187.50
  4. Joint compound - $18.75
  5. Screws assorted - $24.30
  6. Paint primer gal - $31.20

Expenses in database:
1. Lumber materials - $500.00 (TxnDate: 2024-03-15, Vendor: Lowe's)
2. Drywall supplies - $250.00 (TxnDate: 2024-03-15, Vendor: Lowe's)
3. Paint & finishing - $100.00 (TxnDate: 2024-03-15, Vendor: Lowe's)

Database total: $850.00
Receipt total: $1,048.05
Difference: -$198.05

Analyze this mismatch and provide a structured reconciliation in JSON:

{
  "issue_type": "sum_mismatch" | "line_item_mismatch" | "duplicate" | "missing_items",
  "confidence": <0-100>,
  "explanation": "<what went wrong>",
  "recommended_action": "adjust" | "split" | "flag_for_review" | "merge_duplicates",
  "suggested_correction": {
    "action": "...",
    "details": "..."
  }
}

Return ONLY the JSON object.""",
        "temperature": 0.2,
        "max_tokens": 500,
    },
}


# ============================================================================
# Test Runner
# ============================================================================

async def test_model(test_name: str, config: dict):
    """Run a single model test and display results."""
    print(f"\n{'='*80}")
    print(f"Testing: {test_name.upper()} TIER")
    print(f"Model: {config['model']}")
    print(f"Description: {config['description']}")
    print(f"{'='*80}\n")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ ERROR: OPENAI_API_KEY not found in environment")
        return

    try:
        client = AsyncOpenAI(api_key=api_key)

        # Build messages based on test type
        if "system" in config:
            # Chat-style test (system + user)
            messages = [
                {"role": "system", "content": config["system"]},
                {"role": "user", "content": config["user_message"]},
            ]
        else:
            # Single prompt test
            messages = [
                {"role": "system", "content": config["prompt"]},
            ]

        print("[>>] Sending request to OpenAI...")
        print(f"     Temperature: {config['temperature']}")
        print(f"     Max tokens: {config['max_tokens']}\n")

        response = await client.chat.completions.create(
            model=config["model"],
            messages=messages,
            temperature=config["temperature"],
            max_completion_tokens=config["max_tokens"],
        )

        result = response.choices[0].message.content.strip()

        print("[OK] Response received:\n")
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
        print(f"\n[STATS] Token usage:")
        print(f"        Prompt: {usage.prompt_tokens}")
        print(f"        Completion: {usage.completion_tokens}")
        print(f"        Total: {usage.total_tokens}")

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()


async def run_all_tests():
    """Run all model tier tests sequentially."""
    print("\n" + "="*80)
    print(" GPT MODEL TIER TESTING - Andrew Agent System")
    print("="*80)

    # Test each tier
    for test_name in ["internal", "chat", "medium", "heavy"]:
        await test_model(test_name, TESTS[test_name])

        # Small pause between tests (no manual input needed)
        if test_name != "heavy":
            print("\n" + "="*80 + "\n")
            await asyncio.sleep(1)  # Brief pause to read output

    print("\n" + "="*80)
    print(" Testing complete!")
    print("="*80 + "\n")


async def interactive_mode():
    """Interactive mode - let user choose which test to run."""
    print("\n" + "="*80)
    print(" GPT MODEL TIER TESTING - Interactive Mode")
    print("="*80)

    while True:
        print("\nAvailable tests:")
        print("  1. Internal tier (gpt-5-nano) - Context extraction")
        print("  2. Chat tier (gpt-5-mini) - Personality wrapper")
        print("  3. Medium tier (gpt-5-1) - Brain routing")
        print("  4. Heavy tier (gpt-5-2) - Mismatch reconciliation")
        print("  5. Run all tests")
        print("  0. Exit")

        choice = input("\nSelect test (0-5): ").strip()

        if choice == "0":
            print("\nGoodbye!\n")
            break
        elif choice == "1":
            await test_model("internal", TESTS["internal"])
        elif choice == "2":
            await test_model("chat", TESTS["chat"])
        elif choice == "3":
            await test_model("medium", TESTS["medium"])
        elif choice == "4":
            await test_model("heavy", TESTS["heavy"])
        elif choice == "5":
            await run_all_tests()
        else:
            print("❌ Invalid choice. Please select 0-5.")


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point."""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        # Run all tests without interaction
        asyncio.run(run_all_tests())
    else:
        # Interactive mode
        asyncio.run(interactive_mode())


if __name__ == "__main__":
    main()
