"""Settings dialog for AI Studieassistent."""
from __future__ import annotations

from aqt import mw
from aqt.qt import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget, Qt,
)


def show() -> None:
    dlg = SettingsDialog(mw)
    dlg.exec()


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Studieassistent — Inställningar")
        self.setMinimumWidth(420)

        self._cfg = mw.addonManager.getConfig(__name__) or {}

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        tabs.addTab(self._make_acp_tab(), "ACP")
        tabs.addTab(self._make_keys_tab(), "API-nycklar")
        tabs.addTab(self._make_resources_tab(), "Resurser")
        tabs.addTab(self._make_difficulty_tab(), "Svårighetsdetektering")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _make_acp_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._harness = QComboBox()
        self._harness.addItems(["claude-acp", "codex-acp"])
        current = self._cfg.get("harness", "claude-acp")
        if current in ("claude-acp", "codex-acp"):
            self._harness.setCurrentText(current)
        form.addRow("Harness:", self._harness)

        self._acp_binary = self._line(self._cfg.get("acp_binary", "claude-agent-acp"))
        form.addRow("Claude ACP binary:", self._acp_binary)

        self._codex_binary = self._line(self._cfg.get("codex_acp_binary", "codex-acp"))
        form.addRow("Codex ACP binary:", self._codex_binary)

        self._model = QComboBox()
        _models = [
            ("Haiku",  "claude-haiku-4-5-20251001"),
            ("Sonnet", "claude-sonnet-4-6"),
            ("Opus",   "claude-opus-4-6"),
        ]
        for label, mid in _models:
            self._model.addItem(label, mid)
        current_model = self._cfg.get("claude_acp_model", "claude-haiku-4-5-20251001")
        for i, (_, mid) in enumerate(_models):
            if mid == current_model:
                self._model.setCurrentIndex(i)
                break
        form.addRow("Claude-modell:", self._model)

        return w

    def _make_keys_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._anthropic_key = self._password(self._cfg.get("claude_api_key", ""))
        form.addRow("Anthropic API-nyckel:", self._anthropic_key)

        self._openai_key = self._password(self._cfg.get("openai_api_key", ""))
        form.addRow("OpenAI API-nyckel:", self._openai_key)

        note = QLabel("API-nycklarna skickas som miljövariabler till ACP-binären.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow(note)

        return w

    def _make_resources_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._yt_key = self._password(self._cfg.get("youtube_api_key", ""))
        form.addRow("YouTube API-nyckel:", self._yt_key)

        self._cse_key = self._password(self._cfg.get("google_cse_api_key", ""))
        form.addRow("Google CSE API-nyckel:", self._cse_key)

        self._cse_cx = self._line(self._cfg.get("google_cse_cx", ""))
        form.addRow("Google CSE CX:", self._cse_cx)

        return w

    def _make_difficulty_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self._min_reps = QSpinBox()
        self._min_reps.setRange(1, 50)
        self._min_reps.setValue(self._cfg.get("difficulty_min_reps", 5))
        self._min_reps.setSuffix(" reviews")
        form.addRow("Minsta antal repetitioner:", self._min_reps)

        self._d_threshold = QDoubleSpinBox()
        self._d_threshold.setRange(1.0, 10.0)
        self._d_threshold.setSingleStep(0.5)
        self._d_threshold.setDecimals(1)
        self._d_threshold.setValue(self._cfg.get("difficulty_fsrs_d_threshold", 6.0))
        self._d_threshold.setSuffix(" / 10")
        form.addRow("FSRS D-tröskel:", self._d_threshold)

        note = QLabel(
            "Kortet flaggas som svårt när FSRS-svårighet ≥ tröskel "
            "och antal repetitioner ≥ minimum."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow(note)

        return w

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _line(self, value: str) -> QLineEdit:
        w = QLineEdit(value)
        return w

    def _password(self, value: str) -> QLineEdit:
        w = QLineEdit(value)
        w.setEchoMode(QLineEdit.EchoMode.Password)
        return w

    def _save(self):
        cfg = mw.addonManager.getConfig(__name__) or {}
        cfg["harness"] = self._harness.currentText()
        cfg["acp_binary"] = self._acp_binary.text().strip()
        cfg["codex_acp_binary"] = self._codex_binary.text().strip()
        cfg["claude_acp_model"] = self._model.currentData()
        cfg["claude_api_key"] = self._anthropic_key.text().strip()
        cfg["openai_api_key"] = self._openai_key.text().strip()
        cfg["youtube_api_key"] = self._yt_key.text().strip()
        cfg["google_cse_api_key"] = self._cse_key.text().strip()
        cfg["google_cse_cx"] = self._cse_cx.text().strip()
        cfg["difficulty_min_reps"] = self._min_reps.value()
        cfg["difficulty_fsrs_d_threshold"] = self._d_threshold.value()
        mw.addonManager.writeConfig(__name__, cfg)
        self.accept()
