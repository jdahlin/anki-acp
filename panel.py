"""
Main side panel — three-tab QDockWidget:
  Tab 1 (Speech):     quick voice Q&A about current card
  Tab 2 (Chat):       persistent chat per card via AI harness
  Tab 3 (Resources):  images, YouTube videos, educational links
"""

from __future__ import annotations
import html as html_module
import re
import urllib.parse
from typing import Optional

from aqt import mw
from aqt.qt import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTextEdit, QTextBrowser, QLineEdit, QPlainTextEdit, QPushButton, QTabWidget,
    QComboBox, Qt, QTimer, QDesktopServices, QScrollArea, QFrame, QSizePolicy,
    QPainter, QGraphicsBlurEffect,
)


def _cfg():
    return mw.addonManager.getConfig(__name__) or {}


try:
    import markdown as _markdown_lib
    _md_renderer = _markdown_lib.Markdown(extensions=["extra"])

    _CHAT_CSS = (
        "<style>"
        "h1 { font-size: 1.15em; margin: 4px 0; }"
        "h2 { font-size: 1.05em; margin: 4px 0; }"
        "h3, h4, h5, h6 { font-size: 1em; margin: 3px 0; }"
        "</style>"
    )

    def _md_to_html(text: str) -> str:
        _md_renderer.reset()
        return _CHAT_CSS + _md_renderer.convert(text)

    def _md_to_card_html(text: str) -> str:
        """Compact HTML for storing in an Anki card field.
        Replaces block <p> wrappers with <br> separation so the card
        doesn't render with large paragraph gaps."""
        _md_renderer.reset()
        html = _md_renderer.convert(text)
        # Replace </p><p> boundaries with a line break, then strip bare <p>/<\/p>
        html = re.sub(r'</p>\s*<p>', '<br>', html)
        html = re.sub(r'</?p>', '', html)
        return html.strip()

except ImportError:
    def _md_to_html(text: str) -> str:
        """Fallback: minimal regex markdown."""
        t = html_module.escape(text)
        t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t, flags=re.DOTALL)
        t = re.sub(r'\*(.+?)\*', r'<i>\1</i>', t, flags=re.DOTALL)
        t = re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
        t = t.replace('\n', '<br>')
        return t



class _ChatDB:
    """SQLite-backed persistence for per-card chat history."""

    def __init__(self, path: str):
        import sqlite3
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                nid   INTEGER NOT NULL,
                seq   INTEGER NOT NULL,
                is_user INTEGER NOT NULL,
                text  TEXT NOT NULL,
                PRIMARY KEY (nid, seq)
            )
        """)
        self._conn.commit()

    def load(self, nid: int) -> list:
        rows = self._conn.execute(
            "SELECT is_user, text FROM chat_messages WHERE nid=? ORDER BY seq",
            (nid,),
        ).fetchall()
        return [(bool(r[0]), r[1]) for r in rows]

    def append(self, nid: int, seq: int, is_user: bool, text: str):
        self._conn.execute(
            "INSERT OR REPLACE INTO chat_messages (nid, seq, is_user, text) VALUES (?,?,?,?)",
            (nid, seq, int(is_user), text),
        )
        self._conn.commit()

    def delete(self, nid: int):
        self._conn.execute("DELETE FROM chat_messages WHERE nid=?", (nid,))
        self._conn.commit()

    def close(self):
        self._conn.close()


class _BlurOverlay(QWidget):
    """Semi-opaque overlay covering the chat scroll area.

    Shown automatically when a new card question is displayed so the user
    can answer before seeing AI responses. Click anywhere to dismiss early;
    also dismissed automatically when Anki reveals the card answer.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hide()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.addStretch()

        self._label = QLabel("Svara på kortet\nKlicka för att visa AI-konversationen")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            "font-size: 12px; font-weight: 600;"
            "color: white;"
            "background: rgba(0,0,0,0.55);"
            "border-radius: 10px;"
            "padding: 10px 16px;"
        )
        layout.addWidget(self._label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

    def paintEvent(self, event):
        # Fully transparent — the blur effect on the scroll area does all the work.
        pass

    def mousePressEvent(self, event):
        self.hide()
        event.accept()


class _UserBubble(QWidget):
    """Right-aligned blue bubble for user messages."""
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(40, 2, 6, 2)

        label = QLabel(html_module.escape(text))
        label.setWordWrap(True)
        label.setStyleSheet(
            "background: #0b57d0; color: #fff;"
            "border-radius: 14px; padding: 8px 12px;"
        )
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        outer.addStretch()
        outer.addWidget(label)


class _AiBubble(QWidget):
    """Left-aligned bubble for AI messages with full markdown HTML support."""
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 2, 40, 2)

        frame = QFrame()
        from aqt.qt import QPalette
        from aqt import mw
        alt = mw.palette().color(QPalette.ColorRole.AlternateBase).name()
        frame.setStyleSheet(f"QFrame {{ background: {alt}; border-radius: 14px; }}")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(10, 4, 10, 8)
        frame_layout.setSpacing(2)

        # Top bar with copy button
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        self._copy_btn = QPushButton("⎘")
        self._copy_btn.setFixedSize(22, 22)
        self._copy_btn.setToolTip("Kopiera")
        self._copy_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: gray; font-size: 14px; }"
            "QPushButton:hover { color: white; }"
        )
        self._copy_btn.clicked.connect(self._copy_text)
        top_row.addStretch()
        top_row.addWidget(self._copy_btn)
        frame_layout.addLayout(top_row)

        self._browser = QTextBrowser()
        self._browser.setReadOnly(True)
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._on_link_clicked)
        self._browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._browser.setFrameShape(QFrame.Shape.NoFrame)
        self._browser.setStyleSheet("background: transparent; border: none;")
        self._browser.viewport().setStyleSheet("background: transparent;")
        self._browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._browser.document().contentsChanged.connect(self._fit_height)

        frame_layout.addWidget(self._browser)

        # Optional "update card" button shown at the bottom of the bubble
        self._update_btn = QPushButton("Uppdatera kort")
        self._update_btn.setVisible(False)
        self._update_btn.setStyleSheet(
            "QPushButton { background: #0b57d0; color: white; border-radius: 8px; padding: 4px 10px; }"
            "QPushButton:hover { background: #1a73e8; }"
        )
        frame_layout.addWidget(self._update_btn, alignment=Qt.AlignmentFlag.AlignRight)

        outer.addWidget(frame)

        self._raw = ""
        self._on_update_card = None

    def show_update_button(self, callback):
        self._on_update_card = callback
        self._update_btn.clicked.connect(self._do_update)
        self._update_btn.setVisible(True)

    def _do_update(self):
        if self._on_update_card:
            self._on_update_card(self._raw)
            self._update_btn.setText("✓ Uppdaterat")
            self._update_btn.setEnabled(False)

    def set_html(self, html: str, raw: str = ""):
        self._raw = raw
        self._browser.setHtml(html)

    def _copy_text(self):
        from aqt.qt import QApplication
        QApplication.clipboard().setText(self._raw)
        self._copy_btn.setText("✓")
        QTimer.singleShot(1500, lambda: self._copy_btn.setText("⎘"))

    def _on_link_clicked(self, url):
        if url.scheme() == "anki" and url.host() == "note":
            try:
                nid = int(url.path().lstrip("/"))
                from aqt import dialogs
                browser = dialogs.open("Browser", mw)
                browser.search_for(f"nid:{nid}")
            except Exception:
                pass
        else:
            QDesktopServices.openUrl(url)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self):
        # Derive text width from the bubble's own width minus all layout margins:
        #   outer HBoxLayout: left=6, right=40
        #   frame VBoxLayout: left=10, right=10  → total = 66
        bw = self.width()
        if bw <= 0:
            return
        text_width = bw - 66
        if text_width <= 0:
            return
        doc = self._browser.document()
        doc.setTextWidth(text_width)
        h = int(doc.size().height()) + 4
        self._browser.setFixedHeight(max(h, 24))


class _ChatInput(QPlainTextEdit):
    """QPlainTextEdit that sends on Enter, inserts newline on Shift+Enter.
    Uses QPlainTextEdit instead of QLineEdit because macOS Dictation relies
    on the NSTextInputClient protocol, which Qt implements correctly only for
    multi-line text widgets."""

    return_pressed = None  # callable set by ChatTab

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setInputMethodHints(Qt.InputMethodHint.ImhNone)
        self.setFixedHeight(36)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(3)
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

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            elif self.return_pressed:
                self.return_pressed()
            return
        super().keyPressEvent(event)

    def event(self, event):
        from aqt.qt import QEvent
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
        self._bubbles = QVBoxLayout(self._inner)
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
        self._overlay = _BlurOverlay(self)

        # Input
        self.input = _ChatInput()
        self.input.return_pressed = self._on_send
        layout.addWidget(self.input)

        btn_row = QHBoxLayout()

        self.explain_btn = QPushButton("Förklara")
        self.explain_btn.clicked.connect(lambda: self._send_default("Förklara kortet för mig.", update_card=False))
        btn_row.addWidget(self.explain_btn)

        self.answer_btn = QPushButton("Svara")
        self.answer_btn.clicked.connect(lambda: self._send_default("Vad är svaret på frågorna på kortet?", update_card=True))
        btn_row.addWidget(self.answer_btn)

        self.new_btn = QPushButton("Ny")
        self.new_btn.setToolTip("Ny konversation")
        self.new_btn.clicked.connect(self._on_new_conversation)
        btn_row.addWidget(self.new_btn)

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
        self._current_ai_bubble: Optional[_AiBubble] = None
        self._ai_raw: str = ""
        self._ai_msg_idx: int = 0

        self._next_update_card = False   # set True when "Svara" triggers the next message
        self._cancel_event = None        # threading.Event set when streaming is active

        # Set by ReviewPanel after DB is opened
        self._db: Optional[_ChatDB] = None

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
        self.prompt_size_label.setText("Prompt: " + " + " .join(parts))

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
                self._bubbles.addWidget(_UserBubble(raw))
            else:
                b = _AiBubble()
                b.set_html(_md_to_html(raw), raw)
                self._bubbles.addWidget(b)

        self._messages = list(self._history_store.get(card_id, []))
        self._scroll_to_bottom()

    def add_user_message(self, text: str):
        self._messages.append((True, text))
        if self._db is not None and self._current_card_id is not None:
            self._db.append(self._current_card_id, len(self._messages) - 1, True, text)
        self._bubbles.addWidget(_UserBubble(text))
        self._scroll_to_bottom()

    def add_ai_message_start(self):
        self._ai_raw = ""
        self._ai_msg_idx = len(self._messages)
        self._messages.append((False, ""))
        self._current_ai_bubble = _AiBubble()
        self._bubbles.addWidget(self._current_ai_bubble)
        self._scroll_to_bottom()

    def append_ai_chunk(self, chunk: str):
        self._ai_raw += chunk
        if self._current_ai_bubble:
            self._current_ai_bubble.set_html(_md_to_html(self._ai_raw), self._ai_raw)
            self._scroll_to_bottom()

    @staticmethod
    def _clean(raw: str) -> str:
        """Collapse 3+ consecutive newlines to 2, strip trailing whitespace."""
        return re.sub(r'\n{3,}', '\n\n', raw).strip()

    def end_ai_message(self):
        if self._current_ai_bubble:
            cleaned = self._clean(self._ai_raw)

            # Parse and strip tool blocks the AI may have emitted
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
            self._current_ai_bubble.set_html(_md_to_html(cleaned), cleaned)
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
        b = _AiBubble()
        b.set_html(_md_to_html(text), text)
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


class ResourcesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Search row
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Sök föreläsningar, bilder, videos...")
        self.search_input.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.search_input.setInputMethodHints(Qt.InputMethodHint.ImhNone)
        self.search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self.search_input)
        self.search_btn = QPushButton("Sök")
        self.search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self.status_label)

        # Results area — QTextBrowser supports clickable links and inline images
        self.results = QTextBrowser()
        self.results.setOpenExternalLinks(True)
        self.results.setOpenLinks(False)  # handle internally for file:// links
        self.results.anchorClicked.connect(self._on_link_clicked)
        # Allow loading local file:// images
        self.results.document().setMetaInformation(
            self.results.document().MetaInformation.DocumentUrl, "file:///"
        )
        self.results.setPlaceholderText(
            "Sökresultat visas här.\n\nKlicka på länkarna för att öppna."
        )
        layout.addWidget(self.results)

        self._images: list[dict] = []
        self._videos: list[dict] = []
        self._links: list[dict] = []
        self._slides: list[dict] = []
        self._card_html: str = ""
        self._pending: int = 0  # counts running searches

    def set_query(self, query: str, card_html: str = ""):
        """Pre-fill search box and store raw card HTML for MCQ detection."""
        self.search_input.setText(query)
        self._card_html = card_html

    def _on_search(self):
        import os
        _log_path = os.path.expanduser("~/ankihack_debug.log")
        def _dbg(msg):
            with open(_log_path, "a") as f:
                f.write(f"[search] {msg}\n")

        query = self.search_input.text().strip()
        _dbg(f"_on_search called, query={query!r}")
        if not query:
            return
        self._images = []
        self._videos = []
        self._links = []
        self._slides = []
        self.results.clear()
        self.status_label.setText("Söker...")
        self._pending = 4  # lectures + images + videos + links

        try:
            from .resources import search_all, search_lectures
            _dbg("imports OK")
        except Exception as exc:
            _dbg(f"import error: {exc}")
            self.results.setHtml(f"<p style='color:red;'>Import-fel: {html_module.escape(str(exc))}</p>")
            return

        # mw.taskman.run_on_main() is the correct way to dispatch from background threads in Anki
        def _main(fn):
            mw.taskman.run_on_main(fn)

        def _slides_cb(s):
            _dbg(f"slides callback: {len(s)} results")
            _main(lambda: self._on_slides(s))

        def _err_cb(e):
            _dbg(f"error callback: {e}")
            _main(lambda: self._on_error(e))

        search_lectures(
            query=query,
            card_html=self._card_html,
            on_slides=_slides_cb,
            on_error=_err_cb,
        )
        _dbg("search_lectures launched")

        cfg = _cfg()
        search_all(
            query=query,
            on_images=lambda imgs: _main(lambda: self._on_images(imgs)),
            on_videos=lambda vids: _main(lambda: self._on_videos(vids)),
            on_links=lambda lnks: _main(lambda: self._on_links(lnks)),
            on_error=lambda err: _main(lambda: self._on_error(err)),
            youtube_api_key=cfg.get("youtube_api_key", ""),
            google_cse_api_key=cfg.get("google_cse_api_key", ""),
            google_cse_cx=cfg.get("google_cse_cx", ""),
        )
        _dbg("search_all launched")

    def _render(self):
        import os, traceback
        _log_path = os.path.expanduser("~/ankihack_debug.log")
        try:
            self._render_inner()
        except Exception as exc:
            with open(_log_path, "a") as f:
                f.write(f"[render] EXCEPTION: {exc}\n{traceback.format_exc()}\n")

    def _render_inner(self):
        import os
        _log_path = os.path.expanduser("~/ankihack_debug.log")
        with open(_log_path, "a") as f:
            f.write(f"[render] _render_inner: slides={len(self._slides)} imgs={len(self._images)} vids={len(self._videos)} links={len(self._links)}\n")
        html = ""

        if self._slides:
            html += "<h3 style='margin:6px 0 2px;'>🎓 Föreläsningsbilder</h3>"
            for s in self._slides:
                block = html_module.escape(s.get("block", "").replace("_", " "))
                lecture = html_module.escape(s.get("lecture", "").replace("_", " "))
                slide_num = s.get("slide_num", "?")
                png = s.get("png_path", "")
                matched = html_module.escape(", ".join(s.get("matched_by", [])))
                rrf = s.get("rrf_score")
                rrf_str = f"{rrf:.3f}" if rrf else ""

                # AI excerpt — first 200 chars, strip markdown
                ai = re.sub(r'[#*_`]', '', s.get("ai_txt", "") or s.get("slide_txt", ""))
                ai = ai.replace("\n", " ").strip()[:200]
                ai_esc = html_module.escape(ai)

                file_url = f"file://{png}" if png else ""
                img_tag = (
                    f'<a href="{html_module.escape(file_url)}">'
                    f'<img src="{html_module.escape(file_url)}" width="200" '
                    f'style="float:left; margin:0 8px 4px 0; border:1px solid #444;"/></a>'
                ) if png else ""
                meta = block + (f' · {matched}' if matched else '') + (f' · rrf={rrf_str}' if rrf_str else '')
                html += (
                    f'<div style="margin:8px 0; padding:6px; border-top:1px solid #333; overflow:hidden;">'
                    + img_tag
                    + f'<b>Bild {slide_num} — {lecture}</b>'
                    f'<br><small style="color:#888;">{meta}</small>'
                    f'<br><small>{ai_esc}…</small>'
                    f'<div style="clear:both;"></div>'
                    f'</div>'
                )

        if self._images:
            html += "<h3 style='margin:6px 0 2px;'>🖼 Bilder</h3>"
            html += "<div style='display:flex; flex-wrap:wrap; gap:6px;'>"
            for img in self._images:
                local = img.get("local_path", "")
                src_url = html_module.escape(img.get("source", img.get("url", "")))
                if local:
                    file_url = html_module.escape(f"file://{local}")
                    html += (
                        f'<a href="{src_url}">'
                        f'<img src="{file_url}" width="160" '
                        f'style="border:1px solid #444; margin:2px;"/></a>'
                    )
                else:
                    title = html_module.escape(img.get("title", "Bild") or "Bild")
                    html += f'<a href="{src_url}">{title}</a> '
            html += "</div>"

        if self._videos:
            html += "<h3 style='margin:6px 0 2px;'>▶ YouTube-videos</h3><ul>"
            for vid in self._videos:
                title = html_module.escape(vid.get("title", "Video"))
                url = html_module.escape(vid["url"])
                html += f'<li><a href="{url}">{title}</a></li>'
            html += "</ul>"

        if self._links:
            html += "<h3 style='margin:6px 0 2px;'>📚 Utbildningslänkar</h3><ul>"
            for lnk in self._links[:6]:
                title = html_module.escape(lnk.get("title", lnk["url"]))
                url = html_module.escape(lnk["url"])
                snippet = html_module.escape(lnk.get("snippet", ""))[:150]
                html += f'<li><a href="{url}">{title}</a>'
                if snippet:
                    html += f'<br><small style="color:#888;">{snippet}</small>'
                html += "</li>"
            html += "</ul>"

        self.results.setHtml(html or "<p style='color:#888;'>Inga resultat än.</p>")

    def _on_link_clicked(self, url):
        from aqt.qt import QUrl, QDesktopServices
        QDesktopServices.openUrl(QUrl(url.toString()))

    def _on_slides(self, slides):
        import os
        with open(os.path.expanduser("~/ankihack_debug.log"), "a") as f:
            f.write(f"[render] _on_slides called, {len(slides)} slides\n")
        self._slides = slides
        self._render()
        with open(os.path.expanduser("~/ankihack_debug.log"), "a") as f:
            f.write(f"[render] _render() done, html len={len(self.results.toHtml())}\n")
        self._check_done()

    def _on_images(self, imgs):
        self._images = imgs
        self._render()
        self._check_done()

    def _on_videos(self, vids):
        self._videos = vids
        self._render()
        self._check_done()

    def _on_links(self, lnks):
        self._links = lnks
        self._render()
        self._check_done()

    def _on_error(self, err: str):
        self.results.append(f"<p style='color:#f44;'>{html_module.escape(err)}</p>")
        self._check_done()

    def _check_done(self):
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.status_label.setText("")


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

        # Wire up
        self.chat_tab.on_send_message = self._on_chat_send
        self.chat_tab.on_update_card = self._on_update_card
        self.chat_tab.on_model_change = self._on_model_change

        from .tools import ToolHandler
        self._tool_handler = ToolHandler(
            mw=mw,
            get_current_card=lambda: self._current_card,
            chat_tab=self.chat_tab,
            md_to_card_html=_md_to_card_html,
        )
        self.chat_tab.on_create_card = self._tool_handler.create_card
        self.chat_tab.on_search_cards = self._tool_handler.search_cards
        self.chat_tab.on_change_deck = self._tool_handler.change_deck
        self.chat_tab.on_update_card_back = self._tool_handler.update_card_back
        self.chat_tab.on_create_cloze = self._tool_handler.create_cloze
        self._image_sent_sessions: set = set()  # session keys that already had images attached

        # Open persistent chat DB alongside the Anki collection
        import os
        try:
            db_path = os.path.join(os.path.dirname(mw.col.path), "ankihack_chat.db")
        except Exception:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ankihack_chat.db")
        try:
            self.chat_tab._db = _ChatDB(db_path)
        except Exception:
            pass  # if DB fails, run without persistence

        self._current_card = None

        cfg = _cfg()
        self.chat_tab.set_harness(cfg.get("harness", "?"))
        self.chat_tab.set_model(cfg.get("claude_acp_model", "claude-haiku-4-5-20251001"))

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
        # type 0 = standard, type 1 = cloze — both are editable.
        # type 4 = Image Occlusion (Anki 2.1.68+); any other unknown type — skip.
        supported_types = {0, 1}
        if model.get("type", 0) not in supported_types:
            return False
        # Also catch IO add-on cards by model name
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

        # Prompt size hint — compute text + image sizes for the card
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

    def on_answer_shown(self):
        self.chat_tab.hide_blur()

    def on_review_ended(self):
        self._current_card = None
        self.chat_tab.hide_blur()

    # ------------------------------------------------------------------
    # Chat tab
    # ------------------------------------------------------------------

    def _on_model_change(self, model_id: str):
        """Persist the new model and drop the cached ACP client so the next
        request starts a fresh process with --model MODEL."""
        cfg = _cfg()
        cfg["claude_acp_model"] = model_id
        mw.addonManager.writeConfig(__name__, cfg)
        # Invalidate cached ACP clients so they restart with the new model
        from . import direct_ai
        direct_ai._acp_clients.clear()

    def _on_chat_send(self, text: str):
        card = self._current_card
        note = card.note() if card else None
        front = note.fields[0] if (note and note.fields) else ""
        back = note.fields[1] if (note and len(note.fields) > 1) else ""
        card_context = f"Framsida: {front}\nBaksida: {back}" if card else ""
        nid = card.nid if card else None

        # Attach card images on the first message of each session (transparent to user)
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

        from .direct_ai import ask_ai_async
        cfg = _cfg()

        def on_chunk(chunk: str):
            mw.taskman.run_on_main(lambda: self.chat_tab.append_ai_chunk(chunk))

        def on_done():
            mw.taskman.run_on_main(self.chat_tab.end_ai_message)

        def on_error(err: str):
            mw.taskman.run_on_main(lambda: self.chat_tab.append_ai_chunk(f"[Fel: {err}]"))
            mw.taskman.run_on_main(self.chat_tab.end_ai_message)

        def on_tool_use(tool_name: str, tool_input: dict):
            mw.taskman.run_on_main(lambda: self._tool_handler.dispatch(tool_name, tool_input))

        from .tools import SYSTEM_PROMPT
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

        # Pick the target field index
        if is_cloze:
            # Cloze: fields[0] holds {{c1::...}} syntax — don't touch it.
            # Look for a "Back Extra" / "Extra" field, else use the last field.
            idx = next(
                (i for i, n in enumerate(field_names)
                 if any(kw in n.lower() for kw in ("extra", "back"))),
                len(field_names) - 1 if len(field_names) > 1 else None,
            )
        else:
            # Standard: look for a field named back/answer/svar/baksida, else fields[1]
            idx = next(
                (i for i, n in enumerate(field_names)
                 if any(kw in n.lower() for kw in ("back", "answer", "svar", "baksida"))),
                1 if len(field_names) > 1 else None,
            )

        if idx is None:
            return

        note.fields[idx] = _md_to_card_html(raw_markdown)
        mw.col.update_note(note)

