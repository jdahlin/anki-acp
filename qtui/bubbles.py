"""Chat bubble widgets: BlurOverlay, UserBubble, AiBubble."""
from __future__ import annotations
import html as html_module

from aqt import mw
from aqt.qt import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QTextBrowser,
    QPushButton, QFrame, QSizePolicy, Qt, QTimer, QDesktopServices,
)

from .markdown import md_to_html


class BlurOverlay(QWidget):
    """Transparent overlay covering the chat scroll area.

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
        # Fully transparent — the blur effect on the scroll area does the work.
        pass

    def mousePressEvent(self, event):
        self.hide()
        event.accept()


class UserBubble(QWidget):
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


class AiBubble(QWidget):
    """Left-aligned bubble for AI messages with full markdown HTML support."""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 2, 40, 2)

        frame = QFrame()
        from aqt.qt import QPalette
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
