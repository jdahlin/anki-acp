"""
Resource fetching — images, YouTube videos, educational links.
Image search: Google Images with cookie auth (google_cookies.json),
falling back to DuckDuckGo if no cookies are present.
"""

from __future__ import annotations
import json
import re
import time
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

_BRAVE_COOKIES_DB = Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies"

# Rate-limit Google Images to one request every 4 seconds
_google_lock = threading.Lock()
_google_last_request: float = 0.0
_GOOGLE_MIN_INTERVAL = 4.0


def search_lectures(
    query: str,
    card_html: str,
    on_slides: Callable[[list[dict]], None],
    on_error: Callable[[str], None],
):
    """Search local lecture index. Detects MCQ and uses RRF fusion if applicable."""
    def _run():
        try:
            from .lecture_search import search_mcq, search_slides, parse_mcq
            mcq = parse_mcq(card_html) if card_html else None
            if mcq:
                question, answers = mcq
                results = search_mcq(question, answers, limit=8)
            else:
                results = search_slides(query, limit=8)
            on_slides(results)
        except Exception as e:
            on_error(f"Föreläsningssökning misslyckades: {e}")

    threading.Thread(target=_run, daemon=True).start()


def search_all(
    query: str,
    on_images: Callable[[list[dict]], None],
    on_videos: Callable[[list[dict]], None],
    on_links: Callable[[list[dict]], None],
    on_error: Callable[[str], None],
    youtube_api_key: str = "",
    google_cse_api_key: str = "",
    google_cse_cx: str = "",
):
    """
    Start background searches for images, videos, and educational links.
    Results delivered via callbacks (called from background threads —
    callers must dispatch to main thread via QTimer.singleShot).

    Each image dict: {"url": str, "title": str, "source": str}
    Each video dict: {"url": str, "title": str, "thumbnail": str}
    Each link  dict: {"url": str, "title": str, "snippet": str}
    """
    if _BRAVE_COOKIES_DB.exists():
        threading.Thread(
            target=_fetch_google_images_cookies,
            args=(query, on_images, on_error),
            daemon=True,
        ).start()
    elif google_cse_api_key and google_cse_cx:
        threading.Thread(
            target=_fetch_google_cse_images,
            args=(query, google_cse_api_key, google_cse_cx, on_images, on_error),
            daemon=True,
        ).start()
    else:
        threading.Thread(
            target=_fetch_ddg_images,
            args=(query, on_images, on_error),
            daemon=True,
        ).start()

    threading.Thread(
        target=_fetch_youtube,
        args=(query, youtube_api_key, on_videos, on_error),
        daemon=True,
    ).start()

    threading.Thread(
        target=_fetch_educational_links,
        args=(query, on_links, on_error),
        daemon=True,
    ).start()


# ------------------------------------------------------------------
# Google Images via exported cookies (Cookie-Editor JSON format)
# ------------------------------------------------------------------

_VENV_PYTHON = Path(__file__).parent / ".venv/lib"


def _load_brave_google_cookies() -> str:
    """Read Google cookies from Brave via browser_cookie3 in the project venv."""
    import subprocess, json as _json, glob

    # Find venv python
    venv_pythons = glob.glob(str(Path("/Users/johandahlin/dev/ankihack") / ".venv/bin/python*"))
    python = next((p for p in venv_pythons if not p.endswith("-config")), None)
    if not python:
        return ""

    script = (
        "import browser_cookie3, json; "
        "jar = browser_cookie3.brave(domain_name='.google.com'); "
        "print(json.dumps({c.name: c.value for c in jar}))"
    )
    out = subprocess.check_output([python, "-c", script], stderr=subprocess.DEVNULL, timeout=10)
    cookies = _json.loads(out)
    parts = []
    for name, val in cookies.items():
        try:
            f"{name}={val}".encode("latin-1")
            parts.append(f"{name}={val}")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return "; ".join(parts)


def _fetch_google_images_cookies(query: str, on_images, on_error):
    global _google_last_request
    try:
        cookie_header = _load_brave_google_cookies()

        with _google_lock:
            wait = _GOOGLE_MIN_INTERVAL - (time.time() - _google_last_request)
            if wait > 0:
                time.sleep(wait)
            _google_last_request = time.time()

        safe_q = urllib.parse.quote(query)
        url = f"https://www.google.com/search?q={safe_q}&tbm=isch&hl=sv&safe=off&num=16"
        req = urllib.request.Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Cookie": cookie_header,
            "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.google.com/",
        })

        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode(errors="replace")

        # Google embeds full-size image URLs as ["https://...",width,height] in JS
        # Skip encrypted-tbn thumbnails — we want the real source images
        matches = re.findall(
            r'\["(https?://(?!encrypted-tbn)[^"]{10,}\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"'
            r',(\d+),(\d+)\]',
            html,
            re.IGNORECASE,
        )

        seen: set[str] = set()
        candidates = []
        for img_url, w, h in matches:
            if img_url in seen:
                continue
            seen.add(img_url)
            candidates.append(img_url)
            if len(candidates) >= 12:
                break

        if not candidates:
            fallback = re.findall(
                r'(https?://(?!encrypted-tbn)[^"\'<>\s]{15,}\.(?:jpg|jpeg|png|webp))',
                html, re.IGNORECASE,
            )
            candidates = list(dict.fromkeys(fallback))[:12]

        # Download images to temp files so QTextBrowser can display them inline
        results = _download_images(candidates, limit=6)
        on_images(results)

    except Exception as e:
        on_error(f"Google Images misslyckades: {e}")


# ------------------------------------------------------------------
# Image download helper — saves URLs to temp files for inline display
# ------------------------------------------------------------------

import tempfile as _tempfile
import os as _os

_IMG_TMPDIR = _tempfile.mkdtemp(prefix="ankihack_imgs_")
_IMG_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _download_images(urls: list[str], limit: int = 6) -> list[dict]:
    """Download image URLs to temp files; return dicts with local_path set."""
    results = []
    for i, url in enumerate(urls):
        if len(results) >= limit:
            break
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _IMG_UA})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = r.read()
            ext = (url.split(".")[-1].split("?")[0] or "jpg")[:4].lower()
            if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
                ext = "jpg"
            path = _os.path.join(_IMG_TMPDIR, f"img_{id(url)}_{i}.{ext}")
            with open(path, "wb") as f:
                f.write(data)
            results.append({"url": url, "local_path": path, "title": "", "source": url})
        except Exception:
            pass
    return results


# ------------------------------------------------------------------
# DuckDuckGo image search (no API key, uses their instant answer API)
# ------------------------------------------------------------------

def _fetch_ddg_images(query: str, on_images, on_error):
    try:
        # Step 1: get vqd token (needed for image search)
        safe_q = urllib.parse.quote(query)
        init_url = f"https://duckduckgo.com/?q={safe_q}&iax=images&ia=images"
        req = urllib.request.Request(
            init_url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode(errors="replace")

        vqd_match = re.search(r'vqd=(["\'])([^"\']+)\1', html)
        if not vqd_match:
            vqd_match = re.search(r'"vqd":"([^"]+)"', html)
        if not vqd_match:
            on_error("DuckDuckGo: kunde inte hämta vqd-token")
            return
        vqd = vqd_match.group(2) if vqd_match.lastindex >= 2 else vqd_match.group(1)

        # Step 2: fetch images
        img_url = (
            f"https://duckduckgo.com/i.js"
            f"?q={safe_q}&o=json&p=1&s=0&u=bing&f=,,,,,&l=sv-se&vqd={urllib.parse.quote(vqd)}"
        )
        req2 = urllib.request.Request(
            img_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://duckduckgo.com/",
            },
        )
        with urllib.request.urlopen(req2, timeout=10) as resp2:
            data = json.loads(resp2.read())

        results = []
        for item in data.get("results", [])[:8]:
            results.append({
                "url": item.get("image", ""),
                "title": item.get("title", ""),
                "source": item.get("url", ""),
            })

        on_images(results)

    except Exception as e:
        on_error(f"Bildssökning misslyckades: {e}")


# ------------------------------------------------------------------
# YouTube search
# ------------------------------------------------------------------

def _fetch_youtube(query: str, api_key: str, on_videos, on_error):
    try:
        if api_key:
            _fetch_youtube_api(query, api_key, on_videos, on_error)
        else:
            _fetch_youtube_scrape(query, on_videos, on_error)
    except Exception as e:
        on_error(f"YouTube-sökning misslyckades: {e}")


def _fetch_youtube_api(query: str, api_key: str, on_videos, on_error):
    safe_q = urllib.parse.quote(query)
    url = (
        f"https://www.googleapis.com/youtube/v3/search"
        f"?part=snippet&q={safe_q}&type=video&maxResults=5"
        f"&relevanceLanguage=sv&key={api_key}"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read())

    results = []
    for item in data.get("items", []):
        vid_id = item["id"].get("videoId", "")
        snippet = item.get("snippet", {})
        results.append({
            "url": f"https://www.youtube.com/watch?v={vid_id}",
            "title": snippet.get("title", ""),
            "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
        })
    on_videos(results)


def _fetch_youtube_scrape(query: str, on_videos, on_error):
    """Scrape YouTube search results (no API key needed)."""
    safe_q = urllib.parse.quote(query + " förklaring")
    url = f"https://www.youtube.com/results?search_query={safe_q}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                 "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode(errors="replace")

    # Extract video IDs and titles from ytInitialData
    match = re.search(r'var ytInitialData = ({.+?});</script>', html, re.DOTALL)
    if not match:
        on_error("Kunde inte tolka YouTube-svar")
        return

    try:
        yt_data = json.loads(match.group(1))
    except json.JSONDecodeError:
        on_error("YouTube JSON parse-fel")
        return

    results = []
    # Navigate the nested structure to find videoRenderers
    contents = (
        yt_data
        .get("contents", {})
        .get("twoColumnSearchResultsRenderer", {})
        .get("primaryContents", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
    )
    for section in contents:
        items = (
            section
            .get("itemSectionRenderer", {})
            .get("contents", [])
        )
        for item in items:
            vr = item.get("videoRenderer", {})
            if not vr:
                continue
            vid_id = vr.get("videoId", "")
            title_runs = vr.get("title", {}).get("runs", [])
            title = title_runs[0].get("text", "") if title_runs else ""
            thumbs = vr.get("thumbnail", {}).get("thumbnails", [])
            thumb = thumbs[0].get("url", "") if thumbs else ""
            if vid_id:
                results.append({
                    "url": f"https://www.youtube.com/watch?v={vid_id}",
                    "title": title,
                    "thumbnail": thumb,
                })
            if len(results) >= 5:
                break
        if len(results) >= 5:
            break

    on_videos(results)


# ------------------------------------------------------------------
# Educational links via DuckDuckGo text search
# (Wikipedia, Khan Academy, Britannica, NE.se, etc.)
# ------------------------------------------------------------------

_EDU_SITES = [
    "wikipedia.org",
    "ne.se",              # Nationalencyklopedin (Swedish)
    "khanacademy.org",
    "britannica.com",
    "coursera.org",
    "edx.org",
]

def _fetch_educational_links(query: str, on_links, on_error):
    try:
        site_filter = " OR ".join(f"site:{s}" for s in _EDU_SITES)
        full_query = f"{query} ({site_filter})"
        safe_q = urllib.parse.quote(full_query)

        url = f"https://api.duckduckgo.com/?q={safe_q}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        results = []

        # Abstract (top result)
        if data.get("AbstractURL"):
            results.append({
                "url": data["AbstractURL"],
                "title": data.get("Heading", data["AbstractURL"]),
                "snippet": data.get("AbstractText", "")[:200],
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:6]:
            if "FirstURL" in topic:
                results.append({
                    "url": topic["FirstURL"],
                    "title": topic.get("Text", topic["FirstURL"])[:80],
                    "snippet": topic.get("Text", "")[:200],
                })

        on_links(results if results else [])

    except Exception as e:
        on_error(f"Länksökning misslyckades: {e}")


# ------------------------------------------------------------------
# Google Custom Search (images) — optional, needs API key + CX
# Free tier: 100 queries/day
# Setup: https://programmablesearchengine.google.com/
# ------------------------------------------------------------------

def _fetch_google_cse_images(query: str, api_key: str, cx: str, on_images, on_error):
    try:
        safe_q = urllib.parse.quote(query)
        url = (
            f"https://www.googleapis.com/customsearch/v1"
            f"?key={api_key}&cx={cx}&q={safe_q}&searchType=image&num=8&lr=lang_sv"
        )
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())

        results = []
        for item in data.get("items", []):
            results.append({
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "source": item.get("image", {}).get("contextLink", ""),
            })
        on_images(results)

    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        on_error(f"Google CSE HTTP {e.code}: {body[:200]}")
    except Exception as e:
        on_error(f"Google-bildsökning misslyckades: {e}")
