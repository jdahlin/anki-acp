"""Markdown → HTML helpers for chat bubbles and card fields."""
from __future__ import annotations
import html as html_module
import re

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

    def md_to_html(text: str) -> str:
        _md_renderer.reset()
        return _CHAT_CSS + _md_renderer.convert(text)

    def md_to_card_html(text: str) -> str:
        """Compact HTML for storing in an Anki card field."""
        _md_renderer.reset()
        html = _md_renderer.convert(text)
        html = re.sub(r'</p>\s*<p>', '<br>', html)
        html = re.sub(r'</?p>', '', html)
        return html.strip()

except ImportError:
    def md_to_html(text: str) -> str:
        """Fallback: minimal regex markdown."""
        t = html_module.escape(text)
        t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t, flags=re.DOTALL)
        t = re.sub(r'\*(.+?)\*', r'<i>\1</i>', t, flags=re.DOTALL)
        t = re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
        t = t.replace('\n', '<br>')
        return t

    def md_to_card_html(text: str) -> str:
        return md_to_html(text)
