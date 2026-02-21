"""Resources tab widget — lecture slides, images, videos, links."""
from __future__ import annotations
import html as html_module
import re

from aqt import mw
from aqt.qt import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextBrowser, Qt,
)


def _cfg():
    return mw.addonManager.getConfig(__name__) or {}


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
            from ..resources import search_all, search_lectures
            _dbg("imports OK")
        except Exception as exc:
            _dbg(f"import error: {exc}")
            self.results.setHtml(f"<p style='color:red;'>Import-fel: {html_module.escape(str(exc))}</p>")
            return

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
