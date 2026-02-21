"""
Direct AI calls — Claude API and OpenAI API.
Also handles ACP harness routing.
ask_ai_async() is the single entry point used by panel.py.
"""

from __future__ import annotations
import json
import threading
import urllib.request
import urllib.error
from typing import Callable


def ask_ai_async(
    system_prompt: str,
    card_context: str,
    user_question: str,
    config: dict,
    on_chunk: Callable[[str], None],
    on_done: Callable[[], None],
    on_error: Callable[[str], None],
    session_key: str | None = None,
    images: list | None = None,
    on_tool_use: Callable[[str, dict], None] | None = None,
    cancel_event=None,
):
    """
    Route to the right backend based on config["harness"].
    All callbacks are called from a background thread;
    caller must dispatch to main thread if needed (panel.py uses QTimer.singleShot).
    session_key: opaque string (e.g. card ID) used to reuse ACP sessions across calls.
    cancel_event: threading.Event — set it to abort the stream early.
    """
    harness = config.get("harness", "claude-api")

    imgs = images or []

    if harness in ("claude-acp", "codex-acp"):
        _ask_via_acp(system_prompt, card_context, user_question, config,
                     on_chunk, on_done, on_error, session_key=session_key,
                     images=imgs, cancel_event=cancel_event)
    elif harness == "openai-api":
        threading.Thread(
            target=_ask_openai,
            args=(system_prompt, card_context, user_question, config,
                  on_chunk, on_done, on_error, imgs, cancel_event),
            daemon=True,
        ).start()
    else:  # default: claude-api
        threading.Thread(
            target=_ask_claude,
            args=(system_prompt, card_context, user_question, config,
                  on_chunk, on_done, on_error, imgs, on_tool_use, cancel_event),
            daemon=True,
        ).start()


# ------------------------------------------------------------------
# Claude API (streaming SSE)
# ------------------------------------------------------------------

_CREATE_CARD_TOOL = {
    "name": "create_card",
    "description": (
        "Create a new Anki flashcard in the user's current deck. "
        "Use this when the user asks you to create a card, or when you identify "
        "something worth memorising that would make a good flashcard."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "front": {
                "type": "string",
                "description": "The question or prompt on the front of the card.",
            },
            "back": {
                "type": "string",
                "description": "The answer on the back of the card. Plain text or simple HTML.",
            },
        },
        "required": ["front", "back"],
    },
}

_SEARCH_CARDS_TOOL = {
    "name": "search_cards",
    "description": "Search the user's Anki collection and return matching cards as clickable links.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Anki search query, e.g. a topic keyword or 'tag:x'.",
            },
        },
        "required": ["query"],
    },
}

_CHANGE_DECK_TOOL = {
    "name": "change_deck",
    "description": "Move the current card to a different deck.",
    "input_schema": {
        "type": "object",
        "properties": {
            "deck_name": {
                "type": "string",
                "description": "Name of the target deck (partial match is fine).",
            },
        },
        "required": ["deck_name"],
    },
}

_UPDATE_CARD_BACK_TOOL = {
    "name": "update_card_back",
    "description": "Replace the back/answer field of the current card with new content.",
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "New content for the back field. Plain text or markdown.",
            },
        },
        "required": ["content"],
    },
}

_CREATE_CLOZE_TOOL = {
    "name": "create_cloze",
    "description": (
        "Create a new cloze-deletion Anki card in the current deck. "
        "Embed the blanks using standard Anki cloze syntax: {{c1::term}}, {{c2::term}}, etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Full cloze text with {{c1::...}} markers, e.g. 'The mitochondria is the {{c1::powerhouse}} of the cell.'",
            },
            "extra": {
                "type": "string",
                "description": "Optional extra/hint text for the back of the card.",
            },
        },
        "required": ["text"],
    },
}

_ALL_TOOLS = [_CREATE_CARD_TOOL, _CREATE_CLOZE_TOOL, _SEARCH_CARDS_TOOL, _CHANGE_DECK_TOOL, _UPDATE_CARD_BACK_TOOL]


def _ask_claude(system_prompt, card_context, user_question, config,
                on_chunk, on_done, on_error, images=None, on_tool_use=None,
                cancel_event=None):
    api_key = config.get("claude_api_key", "")
    if not api_key:
        on_error("Claude API-nyckel saknas. Öppna Verktyg > Tillägg > Config.")
        return

    model = config.get("claude_model", "claude-sonnet-4-6")
    text = f"{card_context}\n\nFråga: {user_question}" if card_context else user_question

    if images:
        user_content = [
            {"type": "image", "source": {"type": "base64",
             "media_type": img["media_type"], "data": img["data"]}}
            for img in images
        ] + [{"type": "text", "text": text}]
    else:
        user_content = text

    payload_dict = {
        "model": model,
        "max_tokens": 1024,
        "stream": True,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
        "tools": _ALL_TOOLS,
    }
    payload = json.dumps(payload_dict).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2024-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            _tool_name = None
            _tool_json_parts: list[str] = []

            for line in resp:
                if cancel_event and cancel_event.is_set():
                    break
                line = line.decode().strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                evt = data.get("type")
                if evt == "content_block_start":
                    block = data.get("content_block", {})
                    if block.get("type") == "tool_use":
                        _tool_name = block.get("name")
                        _tool_json_parts = []
                elif evt == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        on_chunk(delta.get("text", ""))
                    elif delta.get("type") == "input_json_delta":
                        _tool_json_parts.append(delta.get("partial_json", ""))
                elif evt == "content_block_stop":
                    if _tool_name and _tool_json_parts and on_tool_use:
                        try:
                            tool_input = json.loads("".join(_tool_json_parts))
                            on_tool_use(_tool_name, tool_input)
                        except json.JSONDecodeError:
                            pass
                    _tool_name = None
                    _tool_json_parts = []

        on_done()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        on_error(f"Claude HTTP {e.code}: {body[:200]}")
    except Exception as e:
        on_error(str(e))


# ------------------------------------------------------------------
# OpenAI API (streaming SSE)
# ------------------------------------------------------------------

def _ask_openai(system_prompt, card_context, user_question, config,
                on_chunk, on_done, on_error, images=None, cancel_event=None):
    api_key = config.get("openai_api_key", "")
    if not api_key:
        on_error("OpenAI API-nyckel saknas. Öppna Verktyg > Tillägg > Config.")
        return

    model = config.get("openai_model", "gpt-4o")
    text = f"{card_context}\n\nFråga: {user_question}" if card_context else user_question

    if images:
        user_content = [{"type": "text", "text": text}] + [
            {"type": "image_url",
             "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"}}
            for img in images
        ]
    else:
        user_content = text

    payload = json.dumps({
        "model": model,
        "stream": True,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            for line in resp:
                if cancel_event and cancel_event.is_set():
                    break
                line = line.decode().strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        on_chunk(text)
        on_done()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        on_error(f"OpenAI HTTP {e.code}: {body[:200]}")
    except Exception as e:
        on_error(str(e))


# ------------------------------------------------------------------
# ACP harness
# ------------------------------------------------------------------

_acp_clients: dict[str, object] = {}   # binary -> ACPClient


def _ask_via_acp(system_prompt, card_context, user_question, config,
                 on_chunk, on_done, on_error, session_key=None, images=None,
                 cancel_event=None):
    from .acp_client import ACPClient

    harness = config.get("harness", "claude-acp")
    binary = config.get("acp_binary", "claude-agent-acp")
    extra_args = []
    if harness == "codex-acp":
        binary = config.get("codex_acp_binary", "codex-acp")
    elif harness == "claude-acp":
        model = config.get("claude_acp_model", "claude-haiku-4-5-20251001")
        extra_args = ["--model", model]

    env_extra = {}
    if harness == "claude-acp":
        key = config.get("claude_api_key", "")
        if key:
            env_extra["ANTHROPIC_API_KEY"] = key
    elif harness == "codex-acp":
        key = config.get("openai_api_key", "")
        if key:
            env_extra["OPENAI_API_KEY"] = key

    cache_key = f"{binary}:{':'.join(extra_args)}"

    def _worker():
        client = _acp_clients.get(cache_key)
        if client is None:
            client = ACPClient(binary, env_extra, args=extra_args)
            err = client.start()
            if err:
                on_error(err)
                return
            _acp_clients[cache_key] = client

        # First message for this session_key includes context; subsequent ones don't
        existing = session_key in client._sessions if session_key else False

        session_id, err = client.get_or_create_session(session_key)
        if err:
            on_error(err)
            return

        if not existing and (system_prompt or card_context):
            full_prompt = system_prompt + "\n\n"
            if card_context:
                full_prompt += card_context + "\n\n"
            full_prompt += f"Fråga: {user_question}"
        else:
            full_prompt = f"Fråga: {user_question}"

        # Images are only sent on the first message (when context is included)
        prompt_images = images if (not existing and images) else None

        def _on_chunk_guarded(chunk):
            if cancel_event and cancel_event.is_set():
                return
            on_chunk(chunk)

        client.send_prompt(
            session_id=session_id,
            text=full_prompt,
            on_chunk=_on_chunk_guarded,
            on_done=on_done,
            on_error=on_error,
            images=prompt_images,
        )

    threading.Thread(target=_worker, daemon=True).start()
