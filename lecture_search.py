"""
Local lecture slide search — queries the SQLite FTS5 index built by index.py.
Supports single keyword search and MCQ card search (RRF fusion).
"""
from __future__ import annotations
import re
import sqlite3
from pathlib import Path

DB_PATH = Path("/Users/johandahlin/dev/ankihack") / "search.db"

# RRF smoothing constant
_RRF_K = 60

# Lecture name substrings that are never useful as search results
_EXCLUDE = ("instudering", "seminarieuppgift", "seminareuppgift", "seminaruppgift")


def _excluded(slide: dict) -> bool:
    lecture = (slide.get("lecture") or "").lower()
    return any(x in lecture for x in _EXCLUDE)


def _sanitize(text: str, max_tokens: int = 6) -> str:
    text = re.sub(r'[^\w\såäöÅÄÖ]', ' ', text, flags=re.UNICODE)
    stops = {
        # Swedish function words
        'och', 'att', 'det', 'den', 'en', 'ett', 'är', 'av', 'om', 'för',
        'på', 'med', 'som', 'till', 'från', 'kan', 'de', 'i', 'vad', 'har',
        'var', 'sig', 'men', 'så', 'när', 'hur', 'där', 'här', 'inte',
        'alla', 'also', 'bara', 'dels', 'dock', 'även', 'samt', 'utan',
        'eller', 'sedan', 'inom', 'över', 'under', 'efter', 'igen', 'deras',
        'dess', 'vid', 'mot', 'hos', 'via', 'sina', 'sitt', 'hela', 'just',
        'ofta', 'vilken', 'vilket', 'vilka', 'sant', 'falskt',
        # Generic verbs/adverbs that carry no medical meaning
        'gör', 'göra', 'leder', 'sker', 'finns', 'inga', 'olika', 'andra',
        'detta', 'dessa', 'istället', 'iväg', 'skickas', 'stannar',
        # English stop words
        'the', 'of', 'in', 'a', 'an', 'or', 'and', 'is', 'are', 'that',
        'this', 'with', 'from', 'which', 'what', 'when', 'where', 'how',
    }
    tokens = []
    for t in text.split():
        tl = t.lower()
        if tl in stops:
            continue
        # Keep short all-uppercase tokens — these are medical abbreviations (ER, ATP, DNA, mRNA…)
        if t.isupper() and len(t) >= 2:
            tokens.append(t)
        elif len(t) > 3:
            tokens.append(t)
    tokens = sorted(set(tokens), key=lambda t: -len(t))[:max_tokens]
    return ' OR '.join(tokens)


def _run_query(cur: sqlite3.Cursor, query: str, limit: int) -> list:
    clean = _sanitize(query)
    if not clean:
        return []
    try:
        cur.execute("""
            SELECT
                s.id, s.del, s.block, s.lecture, s.slide_num,
                s.slide_txt, s.ai_txt, s.key_terms, s.png_path,
                bm25(slides_fts, 0, 0, 0, 5.0, 1.0, 50.0) AS score
            FROM slides_fts
            JOIN slides s ON slides_fts.rowid = s.id
            WHERE slides_fts MATCH ?
            ORDER BY score
            LIMIT ?
        """, (clean, limit))
        return cur.fetchall()
    except sqlite3.OperationalError:
        return []


def _rrf_merge(query_results: list[tuple[str, list]]) -> list[dict]:
    scores: dict[int, float] = {}
    data: dict[int, dict] = {}
    matched: dict[int, list[str]] = {}

    for label, rows in query_results:
        for rank, row in enumerate(rows):
            sid = row[0]
            scores[sid] = scores.get(sid, 0.0) + 1.0 / (_RRF_K + rank + 1)
            matched.setdefault(sid, []).append(label)
            if sid not in data:
                data[sid] = dict(
                    id=row[0], del_=row[1], block=row[2], lecture=row[3],
                    slide_num=row[4], slide_txt=row[5], ai_txt=row[6],
                    key_terms=row[7], png_path=row[8],
                )

    results = []
    for sid, rrf in sorted(scores.items(), key=lambda x: -x[1]):
        d = dict(data[sid])
        d["rrf_score"] = rrf
        d["matched_by"] = matched[sid]
        results.append(d)
    return results


def search_slides(query: str, limit: int = 8) -> list[dict]:
    """Single-query search. Returns list of slide dicts sorted by relevance."""
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    rows = _run_query(cur, query, limit)
    con.close()
    results = [
        dict(id=r[0], del_=r[1], block=r[2], lecture=r[3], slide_num=r[4],
             slide_txt=r[5], ai_txt=r[6], key_terms=r[7], png_path=r[8],
             rrf_score=None, matched_by=["query"])
        for r in rows
    ]
    return [s for s in results if not _excluded(s)]


def search_mcq(question: str, answers: list[str], limit: int = 8) -> list[dict]:
    """MCQ search: fuse results from question + each answer via RRF."""
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    per_query = max(limit, 20)
    query_results = []
    for label, text in [("question", question)] + [(f"answer_{i+1}", a) for i, a in enumerate(answers)]:
        rows = _run_query(cur, text, per_query)
        if rows:
            query_results.append((label, rows))
    con.close()
    merged = _rrf_merge(query_results)
    return [s for s in merged if not _excluded(s)][:limit]


def parse_mcq(card_html: str) -> tuple[str, list[str]] | None:
    """
    Try to parse an MCQ card from HTML.
    Returns (question, [answer_a, answer_b, ...]) or None if not MCQ format.
    """
    # Replace block-level tags with newlines to preserve structure
    text = re.sub(r'<(?:br|p|div|li|tr)[^>]*>', '\n', card_html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    # Match option markers: a) b) c) / A. B. / 1) 2) at line start or after newline
    option_re = re.compile(r'(?:^|\n)\s*([a-dA-D1-4])[).]\s+(.+)', re.MULTILINE)
    matches = option_re.findall(text)
    if len(matches) < 2:
        return None

    # Question = everything before the first option
    first_match = option_re.search(text)
    question = text[:first_match.start()].replace('\n', ' ').strip()
    if not question:
        return None

    answers = [m[1].strip() for m in matches]
    return question, answers
