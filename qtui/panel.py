"""ReviewPanel — the main QDockWidget that hosts Chat and Resources tabs."""
from __future__ import annotations
import re

from aqt import mw
from aqt.qt import QDockWidget, QWidget, QVBoxLayout, QTabWidget, Qt

from .chat_tab import ChatTab
from .db import ChatDB
from .markdown import md_to_card_html
from .resources_tab import ResourcesTab


class ReviewPanel(QDockWidget):
    def __init__(self):
        super().__init__("AI Studieassistent", mw)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetClosable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setMinimumWidth(260)

        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.chat_tab = ChatTab()
        self.resources_tab = ResourcesTab()
        self.tabs.addTab(self.chat_tab, "Chatt")
        self.tabs.addTab(self.resources_tab, "Resurser")
        main_layout.addWidget(self.tabs)

        self.setWidget(container)

        # Wire up callbacks
        self.chat_tab.on_send_message = self._on_chat_send
        self.chat_tab.on_update_card = self._on_update_card
        self.chat_tab.on_model_change = self._on_model_change

        from ..tools import ToolHandler
        self._tool_handler = ToolHandler(
            mw=mw,
            get_current_card=lambda: self._current_card,
            chat_tab=self.chat_tab,
            md_to_card_html=md_to_card_html,
        )
        self.chat_tab.on_create_card = self._tool_handler.create_card
        self.chat_tab.on_search_cards = self._tool_handler.search_cards
        self.chat_tab.on_change_deck = self._tool_handler.change_deck
        self.chat_tab.on_update_card_back = self._tool_handler.update_card_back
        self.chat_tab.on_create_cloze = self._tool_handler.create_cloze
        self._image_sent_sessions: set = set()

        # Open persistent chat DB alongside the Anki collection
        import os
        try:
            db_path = os.path.join(os.path.dirname(mw.col.path), "ankihack_chat.db")
        except Exception:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ankihack_chat.db")
        try:
            self.chat_tab._db = ChatDB(db_path)
        except Exception:
            pass  # if DB fails, run without persistence

        self._current_card = None

        cfg = self._cfg()
        self.chat_tab.set_harness(cfg.get("harness", "?"))
        self.chat_tab.set_model(cfg.get("claude_acp_model", "claude-haiku-4-5-20251001"))
        self.chat_tab.set_quick_buttons(cfg.get("quick_buttons", []))

    @staticmethod
    def _cfg():
        return mw.addonManager.getConfig(__name__) or {}

    # ------------------------------------------------------------------
    # Called from __init__.py hooks
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_card_images(card, max_images: int = 4) -> list:
        """Return base64 image dicts for all images in the card's fields."""
        import base64, os
        if not card:
            return []
        media_dir = mw.col.media.dir()
        ext_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
        results = []
        for field in card.note().fields:
            for src in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', field, re.I):
                path = os.path.join(media_dir, src)
                if not os.path.isfile(path):
                    continue
                media_type = ext_map.get(os.path.splitext(src)[1].lower(), "image/jpeg")
                with open(path, "rb") as f:
                    results.append({"media_type": media_type,
                                    "data": base64.b64encode(f.read()).decode()})
                if len(results) >= max_images:
                    return results
        return results

    @staticmethod
    def _note_supports_update(note) -> bool:
        model = note.note_type()
        supported_types = {0, 1}
        if model.get("type", 0) not in supported_types:
            return False
        if "image occlusion" in model.get("name", "").lower():
            return False
        return True

    def on_new_card(self, card):
        self._current_card = card
        front = card.note().fields[0] if card.note().fields else ""
        self.chat_tab.on_update_card = (
            self._on_update_card if self._note_supports_update(card.note()) else None
        )
        self.chat_tab.set_card(card.nid)
        if self.chat_tab._messages:
            self.chat_tab.show_blur()

        from ..difficulty import is_difficult, difficulty_label
        if is_difficult(card, self._cfg()):
            self.chat_tab.show_difficulty_hint(difficulty_label(card))
        else:
            self.chat_tab.hide_difficulty_hint()

        note = card.note()
        card_text = "\n".join(note.fields)
        text_bytes = len(card_text.encode("utf-8"))
        images = self._extract_card_images(card)
        self.chat_tab.set_prompt_size(text_bytes, images)

        import re as _re
        plain_front = _re.sub(r'<[^>]+>', '', front)
        plain_front = plain_front.replace('&nbsp;', ' ').replace('&amp;', '&') \
                                 .replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
        plain_front = plain_front.strip()[:120]
        self.resources_tab.set_query(plain_front, card_html=front)

    def keyPressEvent(self, event):
        """Forward unhandled key presses to Anki's main window."""
        from aqt.qt import QApplication
        QApplication.sendEvent(mw, event)

    def on_answer_shown(self):
        self.chat_tab.hide_blur()

    def on_review_ended(self):
        self._current_card = None
        self.chat_tab.hide_blur()

    # ------------------------------------------------------------------
    # Chat tab callbacks
    # ------------------------------------------------------------------

    def _on_model_change(self, model_id: str):
        """Persist the new model and drop cached ACP clients."""
        cfg = self._cfg()
        cfg["claude_acp_model"] = model_id
        mw.addonManager.writeConfig(__name__, cfg)
        from .. import direct_ai
        direct_ai._acp_clients.clear()

    def _on_chat_send(self, text: str):
        card = self._current_card
        note = card.note() if card else None
        front = note.fields[0] if (note and note.fields) else ""
        back = note.fields[1] if (note and len(note.fields) > 1) else ""
        card_context = f"Framsida: {front}\nBaksida: {back}" if card else ""
        nid = card.nid if card else None

        session_key = f"chat:{nid}" if nid else None
        is_first = session_key not in self._image_sent_sessions
        images = self._extract_card_images(card) if (card and is_first) else []
        if images and session_key:
            self._image_sent_sessions.add(session_key)

        self.chat_tab.add_user_message(text)
        self.chat_tab.add_ai_message_start()

        import threading
        cancel_event = threading.Event()
        self.chat_tab.start_streaming(cancel_event)

        from ..direct_ai import ask_ai_async
        from ..tools import SYSTEM_PROMPT
        cfg = self._cfg()

        def on_chunk(chunk: str):
            mw.taskman.run_on_main(lambda: self.chat_tab.append_ai_chunk(chunk))

        def on_done():
            mw.taskman.run_on_main(self.chat_tab.end_ai_message)

        def on_error(err: str):
            mw.taskman.run_on_main(lambda: self.chat_tab.append_ai_chunk(f"[Fel: {err}]"))
            mw.taskman.run_on_main(self.chat_tab.end_ai_message)

        def on_tool_use(tool_name: str, tool_input: dict):
            mw.taskman.run_on_main(lambda: self._tool_handler.dispatch(tool_name, tool_input))

        ask_ai_async(
            system_prompt=SYSTEM_PROMPT,
            card_context=card_context,
            user_question=text,
            config=cfg,
            on_chunk=on_chunk,
            on_done=on_done,
            on_error=on_error,
            images=images if images else None,
            session_key=session_key,
            on_tool_use=on_tool_use,
            cancel_event=cancel_event,
        )

    def _on_update_card(self, raw_markdown: str):
        card = self._current_card
        if not card:
            return
        note = card.note()
        model = note.note_type()
        field_names = [f["name"] for f in model["flds"]]
        is_cloze = model.get("type", 0) == 1

        if is_cloze:
            idx = next(
                (i for i, n in enumerate(field_names)
                 if any(kw in n.lower() for kw in ("extra", "back"))),
                len(field_names) - 1 if len(field_names) > 1 else None,
            )
        else:
            idx = next(
                (i for i, n in enumerate(field_names)
                 if any(kw in n.lower() for kw in ("back", "answer", "svar", "baksida"))),
                1 if len(field_names) > 1 else None,
            )

        if idx is None:
            return

        note.fields[idx] = md_to_card_html(raw_markdown)
        mw.col.update_note(note)
