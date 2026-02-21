"""
ACP (Agent Client Protocol) client — pure threading, no asyncio.

Uses subprocess.Popen + background reader threads to talk to claude-agent-acp
over stdin/stdout JSON-RPC 2.0. No asyncio subprocess issues in Qt.

Protocol reference: https://agentclientprotocol.com
"""

from __future__ import annotations
import json
import logging
import os
import subprocess
import threading
from typing import Callable, Optional

log = logging.getLogger("ankihack.acp")

_STOP = object()


class ACPClient:
    def __init__(self, binary: str, env_extra: dict | None = None, args: list | None = None):
        self._binary = binary
        self._env_extra = env_extra or {}
        self._args = args or []
        self._proc: Optional[subprocess.Popen] = None
        self._req_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}          # id -> {event, result, error}
        self._callbacks: dict[str, Callable] = {}    # session_id -> on_chunk
        self._sessions: dict[str, str] = {}          # session_key -> session_id
        self._supports_images: bool = False          # set from initialize response

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> Optional[str]:
        """Spawn the binary and handshake. Returns error string or None."""
        env = {**os.environ, **self._env_extra}
        # Anki.app launched from /Applications doesn't inherit the shell PATH,
        # so Homebrew binaries aren't found. Prepend the common install locations.
        extra_paths = ["/opt/homebrew/bin", "/usr/local/bin"]
        current_path = env.get("PATH", "")
        env["PATH"] = ":".join(p for p in extra_paths if p not in current_path) + ":" + current_path
        try:
            self._proc = subprocess.Popen(
                [self._binary] + self._args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            return f"ACP binary not found: {self._binary}"
        except Exception as e:
            return str(e)

        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._stderr_loop, daemon=True).start()

        result, error = self._call("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "ankihack", "title": "Ankihack", "version": "1.0.0"},
        })
        if error:
            return f"initialize failed: {error}"

        caps = (result or {}).get("agentCapabilities", {})
        prompt_caps = caps.get("promptCapabilities", {})
        self._supports_images = bool(prompt_caps.get("image", False))
        log.debug("[ACP] ready binary=%s supports_images=%s", self._binary, self._supports_images)
        return None

    def get_or_create_session(self, session_key: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """Return (session_id, error). Reuses cached session for same key."""
        if session_key and session_key in self._sessions:
            return self._sessions[session_key], None

        result, error = self._call("session/new", {
            "cwd": os.path.expanduser("~"),
            "mcpServers": [],
        })
        if error:
            return None, f"session/new failed: {error}"

        session_id = result.get("sessionId") if result else None
        if not session_id:
            return None, "No sessionId in session/new response"

        if session_key:
            self._sessions[session_key] = session_id
        log.debug("[ACP] new session=%s key=%s", session_id, session_key)
        return session_id, None

    def send_prompt(
        self,
        session_id: str,
        text: str,
        on_chunk: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
        images: list | None = None,
    ):
        """Send prompt non-blocking. Streams chunks via on_chunk."""
        self._callbacks[session_id] = on_chunk

        def _worker():
            req_id = self._next_id()
            event = threading.Event()
            self._pending[req_id] = {"event": event, "result": None, "error": None}

            prompt_blocks = []
            if self._supports_images:
                for img in (images or []):
                    prompt_blocks.append({
                        "type": "image",
                        "mimeType": img["media_type"],
                        "data": img["data"],
                    })
            prompt_blocks.append({"type": "text", "text": text})

            self._send({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "session/prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": prompt_blocks,
                },
            })
            log.debug("[ACP] prompt sent session=%s req=%s", session_id, req_id)

            timed_out = not event.wait(timeout=180)
            self._callbacks.pop(session_id, None)
            entry = self._pending.pop(req_id, {})

            if timed_out:
                on_error("Timeout waiting for response")
            elif entry.get("error"):
                on_error(str(entry["error"]))
            else:
                on_done()

        threading.Thread(target=_worker, daemon=True).start()

    def close(self):
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        with self._lock:
            self._req_id += 1
            return self._req_id

    def _send(self, msg: dict):
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(json.dumps(msg) + "\n")
                self._proc.stdin.flush()
            except Exception as e:
                log.error("[ACP] send error: %s", e)

    def _call(self, method: str, params: dict) -> tuple[Optional[dict], Optional[str]]:
        """Synchronous JSON-RPC call — blocks until response or timeout."""
        req_id = self._next_id()
        event = threading.Event()
        self._pending[req_id] = {"event": event, "result": None, "error": None}
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        event.wait(timeout=30)
        entry = self._pending.pop(req_id, {})
        return entry.get("result"), entry.get("error")

    def _read_loop(self):
        """Background thread: read stdout and dispatch."""
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("[ACP] bad JSON: %s", line[:100])
                    continue
                self._dispatch(msg)
        except Exception as e:
            log.error("[ACP] read_loop: %s", e)

    def _stderr_loop(self):
        """Background thread: log stderr."""
        try:
            for line in self._proc.stderr:
                line = line.rstrip()
                if line:
                    log.warning("[ACP stderr] %s", line)
        except Exception:
            pass

    def _dispatch(self, msg: dict):
        msg_id = msg.get("id")
        method = msg.get("method")

        # Streaming notification
        if method == "session/update":
            params = msg.get("params", {})
            session_id = params.get("sessionId")
            update = params.get("update", {})
            update_type = update.get("sessionUpdate")

            if update_type == "agent_message_chunk":
                content = update.get("content", {})
                text = content.get("text", "") if isinstance(content, dict) else ""
                if text:
                    cb = self._callbacks.get(session_id)
                    if cb:
                        cb(text)
            return

        # Response to a request
        if msg_id is not None and msg_id in self._pending:
            entry = self._pending[msg_id]
            if "error" in msg:
                entry["error"] = msg["error"].get("message", str(msg["error"]))
            else:
                entry["result"] = msg.get("result")
            entry["event"].set()
