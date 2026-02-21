"""SQLite-backed persistence for per-card chat history."""
from __future__ import annotations


class ChatDB:
    """SQLite wrapper; table chat_messages(nid, seq, is_user, text)."""

    def __init__(self, path: str):
        import sqlite3
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                nid     INTEGER NOT NULL,
                seq     INTEGER NOT NULL,
                is_user INTEGER NOT NULL,
                text    TEXT NOT NULL,
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
