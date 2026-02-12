# services/agent_persona.py
# ================================
# Shared Agent Personality Service
# ================================
# Lightweight personality layer for Andrew and Daneel.
# Arturito has its own full persona system (services/arturito/persona.py).
#
# Usage (sync - from regular def):
#   from services.agent_persona import personalize
#   content = personalize("andrew", raw_content)
#
# Usage (async - from async def):
#   from services.agent_persona import personalize_async
#   content = await personalize_async("andrew", raw_content)

import os
from openai import OpenAI, AsyncOpenAI

AGENT_PERSONAS = {
    "andrew": {
        "name": "Andrew",
        "system_prompt": (
            "You are Andrew, a receipt processing agent for a construction company. "
            "Your tone is dry and slightly witty. You are efficient and competent -- "
            "you don't waste words, but you allow yourself the occasional wry observation "
            "or deadpan comment. Think: competent accountant who's seen it all.\n\n"
            "RULES:\n"
            "- Preserve ALL dollar amounts, dates, vendor names, percentages, "
            "markdown formatting (**bold**, *italic*), and links EXACTLY as given.\n"
            "- Preserve all instructional content (examples, formats, numbered lists) EXACTLY.\n"
            "- Only adjust the conversational wrapper/tone around the data.\n"
            "- One personality touch per message max. Do not overdo it.\n"
            "- Keep messages concise. Never add fluff or filler.\n"
            "- Respond in English.\n"
            "- Return ONLY the reworded message. No preamble, no quotes."
        ),
    },
    "daneel": {
        "name": "Daneel",
        "system_prompt": (
            "You are Daneel, a budget monitoring agent for a construction company. "
            "Your tone is that of a watchful guardian -- serene, observant, calm but firm. "
            "You notice everything. You communicate with quiet authority. "
            "Think: vigilant sentinel who protects the company's finances.\n\n"
            "RULES:\n"
            "- Preserve ALL dollar amounts, percentages, account names, "
            "and structured data EXACTLY as given.\n"
            "- Only adjust the conversational framing around the data.\n"
            "- Keep the gravity appropriate to the alert severity.\n"
            "- One personality touch per message max. Do not overdo it.\n"
            "- Keep messages concise. Never add fluff or filler.\n"
            "- Respond in English.\n"
            "- Return ONLY the reworded message. No preamble, no quotes."
        ),
    },
}


def _call_gpt(persona: dict, raw_content: str) -> str:
    """Sync GPT call for personality. Returns raw_content on failure."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return raw_content
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": persona["system_prompt"]},
                {"role": "user", "content": raw_content},
            ],
            temperature=0.4,
            max_tokens=300,
        )
        result = response.choices[0].message.content.strip()
        return result if result else raw_content
    except Exception as e:
        print(f"[AgentPersona] personalize failed, using raw: {e}")
        return raw_content


async def _call_gpt_async(persona: dict, raw_content: str) -> str:
    """Async GPT call for personality. Returns raw_content on failure."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return raw_content
    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": persona["system_prompt"]},
                {"role": "user", "content": raw_content},
            ],
            temperature=0.4,
            max_tokens=300,
        )
        result = response.choices[0].message.content.strip()
        return result if result else raw_content
    except Exception as e:
        print(f"[AgentPersona] personalize_async failed, using raw: {e}")
        return raw_content


def personalize(agent: str, raw_content: str) -> str:
    """
    Sync: Pass a message through gpt-4o-mini to add personality flavor.
    Falls back to raw_content on any error.
    """
    persona = AGENT_PERSONAS.get(agent)
    if not persona:
        return raw_content
    return _call_gpt(persona, raw_content)


async def personalize_async(agent: str, raw_content: str) -> str:
    """
    Async: Pass a message through gpt-4o-mini to add personality flavor.
    Falls back to raw_content on any error.
    """
    persona = AGENT_PERSONAS.get(agent)
    if not persona:
        return raw_content
    return await _call_gpt_async(persona, raw_content)
