"""Persistent chat history in SQLite. Every turn is recorded; searchable via
the search_history tool or the web UI History panel.
"""
import json
import os
import sqlite3
import threading
import time


class ChatHistory:
    def __init__(self, db_path: str, embed_fn=None):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.lock = threading.Lock()
        self.embed_fn = embed_fn          # optional: texts -> list[vector]
        self._embed_ok = embed_fn is not None
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS chats(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, project TEXT, kind TEXT,
            user_input TEXT, sentry_output TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_ts ON chats(ts)")
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(chats)")]
        if "emb" not in cols:  # migration for pre-semantic databases
            self.db.execute("ALTER TABLE chats ADD COLUMN emb TEXT")
        self.db.commit()

    def _embed(self, texts):
        """Embed with graceful permanent fallback if the backend is unavailable."""
        if not self._embed_ok:
            return None
        try:
            return self.embed_fn(texts)
        except Exception:
            self._embed_ok = False   # don't retry every turn
            return None

    def record(self, user_input: str, output: str, project: str, kind: str):
        vec = self._embed([f"{user_input}\n{output}"[:800]])
        emb = json.dumps([round(x, 5) for x in vec[0]]) if vec else None
        with self.lock:
            self.db.execute(
                "INSERT INTO chats(ts, project, kind, user_input, sentry_output, emb) "
                "VALUES(?,?,?,?,?,?)",
                (time.strftime("%Y-%m-%d %H:%M"), project, kind,
                 user_input[:2000], output[:4000], emb))
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
        """Hybrid search: keyword term-matching (always) blended with embedding
        cosine similarity (when an embedder is available). Semantic scoring lets
        'containers' find conversations that only say 'docker'."""
        terms = self._terms(query)
        if not terms and query.strip():
            terms = [query.strip().lower()[:40]]
        qvec = None
        v = self._embed([query[:300]]) if query.strip() else None
        if v:
            qvec = v[0]
        with self.lock:
            rows = self.db.execute(
                "SELECT ts, project, kind, user_input, sentry_output, emb FROM chats "
                "ORDER BY id DESC LIMIT 800").fetchall()
        scored = []
        for idx, r in enumerate(rows):
            text = (r[3] + " " + r[4]).lower()
            kw = sum(1 for t in terms if t in text)
            sem = 0.0
            if qvec and r[5]:
                try:
                    e = json.loads(r[5])
                    num = sum(a * b for a, b in zip(qvec, e))
                    den = (sum(a * a for a in qvec) ** .5) * (sum(b * b for b in e) ** .5)
                    sem = num / den if den else 0.0
                except (json.JSONDecodeError, TypeError):
                    pass
            score = kw + 2.0 * max(sem, 0)   # semantic similarity weighted in
            if score > (0.9 if not kw else 0):  # semantic-only hits need decent sim
                scored.append((-score, idx, r[:5]))
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
