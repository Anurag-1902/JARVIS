"""Persistent chat history in SQLite. Every turn is recorded; searchable via
the search_history tool or the web UI History panel.
"""
import os
import sqlite3
import threading
import time


class ChatHistory:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS chats(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, project TEXT, kind TEXT,
            user_input TEXT, sentry_output TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_ts ON chats(ts)")
        self.db.commit()

    def record(self, user_input: str, output: str, project: str, kind: str):
        with self.lock:
            self.db.execute(
                "INSERT INTO chats(ts, project, kind, user_input, sentry_output) VALUES(?,?,?,?,?)",
                (time.strftime("%Y-%m-%d %H:%M"), project, kind,
                 user_input[:2000], output[:4000]))
            self.db.commit()

    def search(self, query: str, k: int = 5) -> str:
        """Case-insensitive substring search over both sides of the conversation."""
        q = f"%{query.strip()}%"
        with self.lock:
            rows = self.db.execute(
                """SELECT ts, project, kind, user_input, sentry_output FROM chats
                   WHERE user_input LIKE ? COLLATE NOCASE
                      OR sentry_output LIKE ? COLLATE NOCASE
                   ORDER BY id DESC LIMIT ?""", (q, q, k)).fetchall()
        if not rows:
            return f"No past conversations matching '{query}'."
        out = []
        for ts, proj, kind, ui, so in rows:
            out.append(f"[{ts} · {proj} · {kind}]\nYOU: {ui[:300]}\nSENTRY: {so[:500]}")
        return "\n---\n".join(out)

    def search_rows(self, query: str, k: int = 20) -> list[dict]:
        """Structured results for the web UI."""
        q = f"%{query.strip()}%"
        with self.lock:
            rows = self.db.execute(
                """SELECT ts, project, kind, user_input, sentry_output FROM chats
                   WHERE user_input LIKE ? COLLATE NOCASE
                      OR sentry_output LIKE ? COLLATE NOCASE
                   ORDER BY id DESC LIMIT ?""", (q, q, k)).fetchall()
        return [{"ts": r[0], "project": r[1], "kind": r[2],
                 "you": r[3], "sentry": r[4]} for r in rows]

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
