"""
AI calls via ACP (Agent Client Protocol).
ask_ai_async() is the single entry point used by panel.py.
Supported harnesses: "claude-acp", "codex-acp".
"""
from __future__ import annotations
import threading
from typing import Callable

_acp_clients: dict[str, object] = {}   # cache_key -> ACPClient


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
    cancel_event=None,
):
    """
    Route to the ACP backend based on config["harness"].
    All callbacks are called from a background thread.
    session_key: reuses ACP sessions across calls for the same card.
    cancel_event: threading.Event — set to abort mid-stream.
    """
    _ask_via_acp(
        system_prompt, card_context, user_question, config,
        on_chunk, on_done, on_error,
        session_key=session_key,
        images=images or [],
        cancel_event=cancel_event,
    )


def _ask_via_acp(system_prompt, card_context, user_question, config,
                 on_chunk, on_done, on_error, session_key=None, images=None,
                 cancel_event=None):
    from .acp import ACPClient

    harness = config.get("harness", "claude-acp")
    extra_args = []

    if harness == "codex-acp":
        binary = config.get("codex_acp_binary", "codex-acp")
    else:  # claude-acp (default)
        binary = config.get("acp_binary", "claude-agent-acp")
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
