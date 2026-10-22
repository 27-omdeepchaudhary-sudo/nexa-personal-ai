"""NEXA local-first conversation engine."""
import os
import re
from urllib.parse import quote

import requests


SYSTEM_PROMPT = """
You are NEXA, a warm, intelligent, polished female AI companion.

LANGUAGE AND NATURAL FLOW:
- CRITICAL: Choose the reply language primarily from the user's CURRENT message.
- If the current message is in English, reply entirely in English.
- If the current message is in Hindi, reply in natural everyday Indian Hindi.
- If the current message mixes Hindi and English, reply in natural Hinglish.
- Do not let earlier conversation messages override the current message's language.
- Never answer an English question in Hindi just because earlier messages were in Hindi.
- For short greetings such as "How are you?", reply in English when asked in English.
- Avoid overly formal, literary, translated, or textbook-style Hindi.
- Use familiar Indian expressions only when they fit naturally.
- Keep sentences smooth, clear, and comfortable to hear aloud.
- Use natural punctuation and sentence breaks to support pauses.
- Never spell ordinary words letter by letter.
- Do not speak punctuation names, markdown symbols, or formatting instructions.
- Never repeat greetings or introductions unnecessarily.

PERSONALITY:
- Be warm, attentive, confident, respectful, and naturally conversational.
- Sound polished and composed, like a well-produced voice assistant.
- Be playful or witty when the conversation invites it, without forcing it.
- Be empathetic when the user shares a problem or feels unwell.
- Never assume the user's emotions without evidence.
- Do not repeatedly call the user Boss or use excessive pet names.
- Do not address the user by name unless it is useful.
- If the name is needed, use "Omdeep" consistently. Never invent nicknames
  or split the name into forms such as "Om" or "Om Dee".

ANSWER QUALITY:
- Answer the actual question directly.
- For casual conversation, greetings, and simple confirmations, be concise.
- For academic, business, technical, and detailed questions, explain properly.
- Use examples, headings, or bullets when they genuinely improve clarity.
- For spoken answers, prefer natural sentences over long, dense paragraphs.
- Do not remove important detail just because the answer may be spoken.
- For shayari, create original poetry when requested.
- For translations, translate only when requested.
- Explain medical topics generally and do not claim to diagnose.
- Admit uncertainty instead of inventing facts.

CONVERSATION CONTEXT:
- Use the recent conversation and supplied history to understand follow-ups.
- Resolve references such as "it", "that", and "those" from context when clear.
- Build on previous answers instead of starting over.
- Ask one short clarifying question only when the meaning is genuinely unclear.
- Never invent personal memories or claim to remember unavailable conversations.

VOICE-FRIENDLY OUTPUT:
- Make spoken replies sound natural, with varied but comfortable sentence lengths.
- Use commas and full stops where a speaker would naturally pause.
- Avoid unnecessary emoji, decorative symbols, excessive markdown, and tables
  in casual spoken replies.
- Do not include stage directions or describe how your voice should sound.
- Keep the wording easy to pronounce in the selected voice.

ACCURACY AND ACTIONS:
- Explain science, technology, business, operations, and everyday topics clearly.
- Never claim an action succeeded unless it actually succeeded.
- Never claim to see the user's face or know their mood unless an active feature
  has supplied that information.
"""


SESSION = requests.Session()


def dictionary_lookup(word):
    """Look up a single English word; return None if unavailable."""
    word = word.strip(" \t'\".,!?;:")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z'-]{0,45}", word):
        return None

    try:
        url = (
            "https://api.dictionaryapi.dev/api/v2/entries/en/"
            + quote(word, safe="")
        )
        response = SESSION.get(url, timeout=3.5)
        if response.status_code != 200:
            return None

        entries = response.json()
        for entry in entries:
            meanings = entry.get("meanings", [])
            for meaning in meanings:
                definitions = meaning.get("definitions", [])
                if not definitions:
                    continue

                definition = definitions[0].get("definition", "").strip()
                if not definition:
                    continue

                part = meaning.get("partOfSpeech", "")
                example = definitions[0].get("example", "")
                answer = f"{word.capitalize()} means: {definition}"
                if part:
                    answer = f"{word.capitalize()} ({part}) means: {definition}"
                if example:
                    answer += f" Example: {example}"
                return answer

    except (requests.RequestException, ValueError, TypeError):
        return None

    return None


def definition_target(message):
    """Recognize straightforward requests for a word's meaning."""
    text = message.strip().strip(" ?.!")

    patterns = (
        r"^(?:please\s+)?define\s+(.+)$",
        r"^(?:what is the meaning of|meaning of)\s+(.+)$",
        r"^what does\s+(.+?)\s+mean$",
        r"^(?:what is|what's)\s+(?:an?\s+|the\s+)?"
        r"([a-zA-Z][a-zA-Z'-]{0,45})$",
    )

    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" \t'\".,!?;:")

    return None


def ask_nexa(message, history=None):
    history = history or []

    # Try the free dictionary first for direct single-word definitions.
    target = definition_target(message)
    if target:
        direct = dictionary_lookup(target)
        if direct:
            return direct

    # Optional paid API mode; disabled unless explicitly enabled.
    if (
        os.getenv("NEXA_USE_OPENAI", "0").strip() == "1"
        and os.getenv("OPENAI_API_KEY", "").strip()
    ):
        try:
            from openai import OpenAI

            client = OpenAI(timeout=25.0, max_retries=0)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            messages += [
                item for item in history[-16:]
                if isinstance(item, dict)
                and item.get("role") in ("user", "assistant")
            ]
            messages.append({"role": "user", "content": message})

            result = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=messages,
                temperature=0.4,
                max_tokens=600,
            )
            answer = result.choices[0].message.content
            if answer and answer.strip():
                return answer.strip()

        except Exception as exc:
            print(
                f"[NEXA] Optional online AI unavailable; "
                f"using Ollama ({type(exc).__name__})."
            )

    # General questions and dictionary misses go to local Ollama.
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [
            {"role": item["role"], "content": item["content"]}
            for item in history[-16:]
            if isinstance(item, dict)
            and item.get("role") in ("user", "assistant")
        ]
        messages.append({"role": "user", "content": message})

        response = SESSION.post(
            os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat"),
            json={
                "model": os.getenv("OLLAMA_MODEL", "qwen3:8b"),
                "stream": False,
                "think": False,
                "keep_alive": "10m",
                "options": {
                    "temperature": 0.45,
                    "num_ctx": 8192,
                    "num_predict": 600,
                },
                "messages": messages,
            },
            timeout=90,
        )
        response.raise_for_status()
        answer = response.json().get("message", {}).get("content", "").strip()

        if "</think>" in answer:
            answer = answer.rsplit("</think>", 1)[-1].strip()

        return answer or "I couldn't prepare a useful answer just now. Please try again."

    except Exception as exc:
        print(f"[NEXA] Local Ollama request failed: {type(exc).__name__}: {exc}")
        return (
            "I can't reach the local AI model. Please check that Ollama "
            "is running and the selected model is available."
        )
