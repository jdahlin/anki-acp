"""
AI Chat & Resources Review Assistant for Anki
"""

import logging
import os

from aqt import mw, gui_hooks
from aqt.qt import Qt, QObject, QEvent

_panel = None

_LOG_PATH = os.path.expanduser("~/ankihack_debug.log")

def _log(msg):
    with open(_LOG_PATH, "a") as f:
        f.write(f"[ankihack] {msg}\n")

# Route Python logging from ankihack.* to the debug log file
logging.getLogger("ankihack").addHandler(
    logging.FileHandler(_LOG_PATH, encoding="utf-8")
)
logging.getLogger("ankihack").setLevel(logging.DEBUG)


# ------------------------------------------------------------------
# Panel
# ------------------------------------------------------------------

class _KeyFilter(QObject):
    """
    Accept ShortcutOverride events only when the chat text-input has focus,
    so that Anki's reviewer shortcuts (Space/digits/Enter) still fire whenever
    focus is on any other panel widget (buttons, labels, scroll area, etc.).
    """
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ShortcutOverride and _panel is not None:
            from .qtui.chat_tab import ChatInput
            if isinstance(mw.focusWidget(), ChatInput):
                event.accept()
                return True
        return False


_key_filter = _KeyFilter()


def _get_panel():
    global _panel
    if _panel is None:
        from .qtui import ReviewPanel
        _panel = ReviewPanel()
        mw.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, _panel)
        mw.installEventFilter(_key_filter)
        # Thin, subtle dock separator
        mw.setStyleSheet(mw.styleSheet() + """
            QMainWindow::separator {
                background: transparent;
                width: 1px;
                height: 1px;
            }
            QMainWindow::separator:hover {
                background: rgba(128, 128, 128, 0.3);
            }
        """)
        _log("Panel created")
    return _panel


# ------------------------------------------------------------------
# Hooks
# ------------------------------------------------------------------

def _on_reviewer_did_show_question(card):
    panel = _get_panel()
    panel.setVisible(True)
    panel.on_new_card(card)

def _on_reviewer_did_show_answer(card):
    if _panel is not None:
        _panel.on_answer_shown()

def _on_reviewer_will_end():
    global _panel
    if _panel is not None:
        _panel.on_review_ended()

def _setup():
    _log("_setup() called")
    gui_hooks.reviewer_did_show_question.append(_on_reviewer_did_show_question)
    gui_hooks.reviewer_did_show_answer.append(_on_reviewer_did_show_answer)
    gui_hooks.reviewer_will_end.append(_on_reviewer_will_end)

    from .qtui.settings_dialog import show as _show_settings
    mw.addonManager.setConfigAction(__name__, _show_settings)

    _log("_setup() complete")

gui_hooks.profile_did_open.append(_setup)
