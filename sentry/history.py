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

    STOP = {"what", "did", "we", "do", "about", "the", "a", "an", "to", "of",
            "in", "on", "for", "and", "or", "is", "was", "it", "that", "this",
            "how", "when", "where", "why", "have", "has", "had", "you", "i",
            "me", "my", "our", "us", "with", "last", "time", "previously",
            "earlier", "remember", "again", "were", "does", "can"}

    @classmethod
    def _terms(cls, query: str) -> list:
        words = [w.strip(".,!?:;'\"()").lower() for w in query.split()]
        return [w for w in words if len(w) > 2 and w not in cls.STOP][:8]

    def _scored_rows(self, query: str, k: int):
        """Keyword search: a row matches if it contains ANY term (prefix-tolerant,
        so 'crash' matches 'crashing'). Ranked by #terms matched, then recency."""
        terms = self._terms(query)
        if not terms:
            terms = [query.strip().lower()[:40]] if query.strip() else []
        if not terms:
            return []
        with self.lock:
            rows = self.db.execute(
                "SELECT ts, project, kind, user_input, sentry_output FROM chats "
                "ORDER BY id DESC LIMIT 800").fetchall()
        scored = []
        for idx, r in enumerate(rows):
            text = (r[3] + " " + r[4]).lower()
            score = sum(1 for t in terms if t in text)
            if score:
                scored.append((-score, idx, r))  # more terms first, then newest
        scored.sort()
        return [r for _, _, r in scored[:k]]

    def search(self, query: str, k: int = 5) -> str:
        rows = self._scored_rows(query, k)
        if not rows:
            return f"No past conversations matching '{query}'."
        out = []
        for ts, proj, kind, ui, so in rows:
            out.append(f"[{ts} · {proj} · {kind}]\nYOU: {ui[:300]}\nSENTRY: {so[:500]}")
        return "\n---\n".join(out)

    def search_rows(self, query: str, k: int = 20) -> list:
        """Structured results for the web UI."""
        return [{"ts": r[0], "project": r[1], "kind": r[2],
                 "you": r[3], "sentry": r[4]} for r in self._scored_rows(query, k)]

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
