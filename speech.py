"""
Speech input via macOS system Dictation.

macOS Dictation is a system-level text input service (NSTextInputClient).
Any focused QLineEdit or QTextEdit receives dictation text automatically —
no SFSpeechRecognizer or AVAudioEngine needed.

This module just provides a helper to focus the right input field and
optionally auto-submit after a short idle period.
"""

from aqt.qt import QLineEdit, QTimer


def attach_dictation_autosend(
    input_widget: QLineEdit,
    on_submit,
    idle_ms: int = 1800,
):
    """
    After the user stops changing text for `idle_ms` milliseconds,
    call on_submit(text) automatically.

    The user can also just press Enter to submit immediately.

    Usage:
        attach_dictation_autosend(my_line_edit, my_callback)
    """
    timer = QTimer()
    timer.setSingleShot(True)

    def _on_text_changed(text):
        if text.strip():
            timer.start(idle_ms)
        else:
            timer.stop()

    def _on_timeout():
        text = input_widget.text().strip()
        if text:
            input_widget.clear()
            on_submit(text)

    timer.timeout.connect(_on_timeout)
    input_widget.textChanged.connect(_on_text_changed)
    input_widget.returnPressed.connect(lambda: (
        timer.stop(),
        on_submit(input_widget.text().strip()) if input_widget.text().strip() else None,
        input_widget.clear(),
    ))

    return timer  # caller should keep a reference
