"""Chat tab widget and supporting chat input widget."""
from __future__ import annotations
import re
from typing import Optional

from aqt import mw
from aqt.qt import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QComboBox, QScrollArea, QGraphicsBlurEffect,
    Qt, QEvent,
)

from .bubbles import BlurOverlay, UserBubble, AiBubble
from .db import ChatDB
from .markdown import md_to_html


class ChatInput(QPlainTextEdit):
    """QPlainTextEdit that sends on Enter, inserts newline on Shift+Enter.

    Uses QPlainTextEdit instead of QLineEdit because macOS Dictation relies
    on the NSTextInputClient protocol, which Qt implements correctly only for
    multi-line text widgets.
    """

    return_pressed = None  # callable set by ChatTab

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setInputMethodHints(Qt.InputMethodHint.ImhNone)
        self.setFixedHeight(36)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(3)
        self.document().contentsChanged.connect(self._adjust_height)
        from aqt.qt import QPalette
        is_dark = mw.palette().color(QPalette.ColorRole.Window).lightness() < 128
        focus_color = "#ffffff" if is_dark else "#0b57d0"
        self.setStyleSheet(
            "QPlainTextEdit {"
            "  border: 1px solid #444;"
            "  border-radius: 16px;"
            "  padding: 6px 12px;"
            "  background: transparent;"
            "}"
            f"QPlainTextEdit:focus {{ border-color: {focus_color}; }}"
        )

    _MIN_H = 36
    _MAX_H = 160  # ~5 lines

    def _adjust_height(self):
        # Sum visual line counts across all blocks — blockCount() alone misses
        # wrapping within a single paragraph.
        visual_lines = 0
        block = self.document().begin()
        while block.isValid():
            layout = block.layout()
            visual_lines += layout.lineCount() if (layout and layout.lineCount() > 0) else 1
            block = block.next()
        line_h = self.fontMetrics().lineSpacing()
        # 16px = border (2) + stylesheet padding-top (6) + padding-bottom (6) + slack (2)
        h = max(self._MIN_H, min(max(1, visual_lines) * line_h + 16, self._MAX_H))
        if self.height() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()  # wrapping changes when panel width changes

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            elif self.return_pressed:
                self.return_pressed()
            return
        super().keyPressEvent(event)

    def event(self, event):
        # Only claim ShortcutOverride for text-editing keys.
        # All other Cmd/Ctrl+key combos (e.g. Cmd+Q, Cmd+W) must pass
        # through so the OS/app-level shortcuts can fire.
        if event.type() == QEvent.Type.ShortcutOverride:
            _editing = {
                Qt.Key.Key_A, Qt.Key.Key_C, Qt.Key.Key_V, Qt.Key.Key_X,
                Qt.Key.Key_Z, Qt.Key.Key_Y,
                Qt.Key.Key_Left, Qt.Key.Key_Right,
                Qt.Key.Key_Up, Qt.Key.Key_Down,
                Qt.Key.Key_Return, Qt.Key.Key_Enter,
                Qt.Key.Key_Backspace, Qt.Key.Key_Delete,
            }
            if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                    and event.key() not in _editing):
                event.ignore()
                return True
        return super().event(event)


class ChatTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)

        # Harness + model row
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        self.harness_label = QLabel()
        self.harness_label.setStyleSheet("color: #888; font-size: 10px;")
        top_row.addWidget(self.harness_label)
        top_row.addStretch()

        _MODEL_ITEMS = [
            ("Haiku",  "claude-haiku-4-5-20251001"),
            ("Sonnet", "claude-sonnet-4-6"),
            ("Opus",   "claude-opus-4-6"),
        ]
        self._model_ids = [m for _, m in _MODEL_ITEMS]
        self.model_combo = QComboBox()
        self.model_combo.setStyleSheet("font-size: 10px;")
        for label, _ in _MODEL_ITEMS:
            self.model_combo.addItem(label)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        top_row.addWidget(self.model_combo)
        layout.addLayout(top_row)

        self.on_model_change = None  # callable(model_id) set by ReviewPanel

        # Scrollable bubble area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameStyle(0)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical {"
            "  width: 6px; background: transparent; margin: 0;"
            "}"
            "QScrollBar::handle:vertical {"
            "  background: rgba(128,128,128,0.45);"
            "  border-radius: 3px; min-height: 20px;"
            "}"
            "QScrollBar::handle:vertical:hover {"
            "  background: rgba(128,128,128,0.7);"
            "}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
            "  height: 0; width: 0;"
            "}"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
            "  background: transparent;"
            "}"
        )
        self._scroll.viewport().setStyleSheet("background: transparent;")

        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        from aqt.qt import QVBoxLayout as _VBox
        self._bubbles = _VBox(self._inner)
        self._bubbles.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._bubbles.setSpacing(6)
        self._bubbles.setContentsMargins(4, 4, 4, 4)
        self._scroll.setWidget(self._inner)
        layout.addWidget(self._scroll)
        sb = self._scroll.verticalScrollBar()
        sb.rangeChanged.connect(self._on_range_changed)
        sb.valueChanged.connect(self._on_scroll_moved)
        self._stick_to_bottom = True

        # Overlay — shown during question phase, hidden when answer is revealed
        self._overlay = BlurOverlay(self)

        # Input
        self.input = ChatInput()
        self.input.return_pressed = self._on_send
        layout.addWidget(self.input)

        btn_row = QHBoxLayout()

        # Dynamic quick-action buttons (configurable via config.json quick_buttons)
        self._quick_btn_layout = QHBoxLayout()
        self._quick_btn_layout.setSpacing(4)
        self._quick_btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_row.addLayout(self._quick_btn_layout)

        btn_row.addStretch()

        self.stop_btn = QPushButton("⏹")
        self.stop_btn.setToolTip("Avbryt svar")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.stop_btn)

        layout.addLayout(btn_row)

        self.prompt_size_label = QLabel("")
        self.prompt_size_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self.prompt_size_label)

        # Per-card message history: {card_id: [(is_user, raw_text), ...]}
        self._history_store: dict[int, list[tuple[bool, str]]] = {}
        self._messages: list[tuple[bool, str]] = []
        self._current_card_id: Optional[int] = None
        self._current_ai_bubble: Optional[AiBubble] = None
        self._ai_raw: str = ""
        self._ai_msg_idx: int = 0

        self._next_update_card = False   # set True when "Svara" triggers the next message
        self._cancel_event = None        # threading.Event set when streaming is active

        # Set by ReviewPanel after DB is opened
        self._db: Optional[ChatDB] = None

        # Callbacks set by ReviewPanel
        self.on_send_message = None
        self.on_update_card = None       # called with raw markdown when update btn clicked
        self.on_create_card = None       # called with (front, back) when AI creates a card
        self.on_search_cards = None      # called with query string
        self.on_change_deck = None       # called with deck name string
        self.on_update_card_back = None  # called with new back content string
        self.on_create_cloze = None      # called with (text, extra)

    def set_prompt_size(self, text_bytes: int, images: list):
        def _fmt(n):
            return f"{n/1024:.1f} KB" if n >= 1024 else f"{n} B"
        parts = [f"text {_fmt(text_bytes)}"]
        if images:
            img_bytes = sum(len(img["data"]) * 3 // 4 for img in images)
            parts.append(f"{len(images)} bild{'er' if len(images) != 1 else ''} {_fmt(img_bytes)}")
        self.prompt_size_label.setText("Prompt: " + " + ".join(parts))

    def set_harness(self, harness: str):
        self.harness_label.setText(f"{harness}")

    def set_model(self, model_id: str):
        """Select the combobox entry matching model_id (no signal emitted)."""
        if model_id in self._model_ids:
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentIndex(self._model_ids.index(model_id))
            self.model_combo.blockSignals(False)

    def _on_model_changed(self, index: int):
        if self.on_model_change and 0 <= index < len(self._model_ids):
            self.on_model_change(self._model_ids[index])

    def set_quick_buttons(self, buttons: list[dict]):
        """Rebuild the quick-action button row from a list of
        {"label": str, "prompt": str, "update_card": bool} dicts."""
        # Clear existing dynamic buttons
        while self._quick_btn_layout.count():
            item = self._quick_btn_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for btn_def in buttons:
            label = btn_def.get("label", "")
            prompt = btn_def.get("prompt", "")
            update_card = bool(btn_def.get("update_card", False))
            if not label or not prompt:
                continue
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda _=False, p=prompt, u=update_card: self._send_default(p, update_card=u)
            )
            self._quick_btn_layout.addWidget(btn)

    def set_card(self, card_id: int):
        if self._current_card_id is not None:
            self._history_store[self._current_card_id] = list(self._messages)

        self._current_card_id = card_id
        self._clear_bubbles()

        # Load from DB if not already in memory cache
        if card_id not in self._history_store and self._db is not None:
            self._history_store[card_id] = self._db.load(card_id)

        for is_user, raw in self._history_store.get(card_id, []):
            if is_user:
                self._bubbles.addWidget(UserBubble(raw))
            else:
                b = AiBubble()
                b.set_html(md_to_html(raw), raw)
                self._bubbles.addWidget(b)

        self._messages = list(self._history_store.get(card_id, []))
        self._scroll_to_bottom()

    def add_user_message(self, text: str):
        self._messages.append((True, text))
        if self._db is not None and self._current_card_id is not None:
            self._db.append(self._current_card_id, len(self._messages) - 1, True, text)
        self._bubbles.addWidget(UserBubble(text))
        self._scroll_to_bottom()

    def add_ai_message_start(self):
        self._ai_raw = ""
        self._ai_msg_idx = len(self._messages)
        self._messages.append((False, ""))
        self._current_ai_bubble = AiBubble()
        self._bubbles.addWidget(self._current_ai_bubble)
        self._scroll_to_bottom()

    def append_ai_chunk(self, chunk: str):
        self._ai_raw += chunk
        if self._current_ai_bubble:
            self._current_ai_bubble.set_html(md_to_html(self._ai_raw), self._ai_raw)
            self._scroll_to_bottom()

    @staticmethod
    def _clean(raw: str) -> str:
        """Collapse 3+ consecutive newlines to 2, strip trailing whitespace."""
        return re.sub(r'\n{3,}', '\n\n', raw).strip()

    def end_ai_message(self):
        if self._current_ai_bubble:
            cleaned = self._clean(self._ai_raw)

            import json as _json

            for match in re.findall(r'<create_card>(.*?)</create_card>', cleaned, re.DOTALL):
                try:
                    data = _json.loads(match)
                    if self.on_create_card:
                        self.on_create_card(data.get("front", ""), data.get("back", ""))
                except (ValueError, KeyError):
                    pass

            for match in re.findall(r'<search_cards>(.*?)</search_cards>', cleaned, re.DOTALL):
                query = match.strip()
                if query and self.on_search_cards:
                    self.on_search_cards(query)

            for match in re.findall(r'<change_deck>(.*?)</change_deck>', cleaned, re.DOTALL):
                deck_name = match.strip()
                if deck_name and self.on_change_deck:
                    self.on_change_deck(deck_name)

            for match in re.findall(r'<update_card_back>(.*?)</update_card_back>', cleaned, re.DOTALL):
                content = match.strip()
                if content and self.on_update_card_back:
                    self.on_update_card_back(content)

            for match in re.findall(r'<create_cloze>(.*?)</create_cloze>', cleaned, re.DOTALL):
                try:
                    data = _json.loads(match)
                    if self.on_create_cloze:
                        self.on_create_cloze(data.get("text", ""), data.get("extra", ""))
                except (ValueError, KeyError):
                    pass

            cleaned = re.sub(
                r'\s*<(create_card|create_cloze|search_cards|change_deck|update_card_back)>.*?'
                r'</\1>',
                '', cleaned, flags=re.DOTALL,
            ).strip()

            self._ai_raw = cleaned
            self._current_ai_bubble.set_html(md_to_html(cleaned), cleaned)
            self._messages[self._ai_msg_idx] = (False, cleaned)
            if self._db is not None and self._current_card_id is not None:
                self._db.append(self._current_card_id, self._ai_msg_idx, False, cleaned)
            if self._next_update_card and self.on_update_card:
                self._current_ai_bubble.show_update_button(self.on_update_card)
            self._next_update_card = False
        self._current_ai_bubble = None
        self._cancel_event = None
        self.stop_btn.setEnabled(False)
        self._scroll_to_bottom()

    def start_streaming(self, cancel_event):
        self._cancel_event = cancel_event
        self.stop_btn.setEnabled(True)

    def _on_stop(self):
        if self._cancel_event:
            self._cancel_event.set()

    def add_status_message(self, text: str):
        """Add a standalone AI bubble without affecting the current streaming bubble."""
        b = AiBubble()
        b.set_html(md_to_html(text), text)
        self._messages.append((False, text))
        if self._db is not None and self._current_card_id is not None:
            self._db.append(self._current_card_id, len(self._messages) - 1, False, text)
        self._bubbles.addWidget(b)
        self._scroll_to_bottom()

    def fill_input_from_speech(self, text: str):
        self.input.setPlainText(text)
        self.input.setFocus()

    def _scroll_to_bottom(self):
        self._stick_to_bottom = True
        sb = self._scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_range_changed(self, _min, _max):
        """Fires after layout reflows — scroll to new bottom if pinned."""
        if self._stick_to_bottom:
            self._scroll.verticalScrollBar().setValue(_max)

    def _on_scroll_moved(self, value):
        """Un-pin if user scrolled up; re-pin if they scroll back to the bottom."""
        sb = self._scroll.verticalScrollBar()
        self._stick_to_bottom = (value >= sb.maximum() - 4)

    def _clear_bubbles(self):
        while self._bubbles.count():
            item = self._bubbles.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._messages = []
        self._current_ai_bubble = None
        self._ai_raw = ""

    def _send_default(self, text: str, update_card: bool = False):
        self._next_update_card = update_card
        if self.on_send_message:
            self.on_send_message(text)

    def _on_send(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        if text == "/clear":
            self._on_new_conversation()
            return
        if self.on_send_message:
            self.on_send_message(text)

    def _on_new_conversation(self):
        self._clear_bubbles()
        if self._current_card_id is not None:
            self._history_store.pop(self._current_card_id, None)
            if self._db is not None:
                self._db.delete(self._current_card_id)

    # ------------------------------------------------------------------
    # Blur overlay (question phase)
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_overlay()

    def _reposition_overlay(self):
        self._overlay.setGeometry(self._scroll.geometry())

    def show_blur(self):
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(32)
        self._scroll.setGraphicsEffect(effect)
        self._reposition_overlay()
        self._overlay.show()
        self._overlay.raise_()

    def hide_blur(self):
        self._scroll.setGraphicsEffect(None)
        self._overlay.hide()
