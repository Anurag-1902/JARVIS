"""Local document index + vector search over the workdir.

Two backends, picked automatically:
  1. Real embeddings via Ollama's nomic-embed-text (if pulled) — semantic search.
  2. Pure-Python TF-IDF vectors — zero dependencies, no extra RAM, still useful.

Index persists to data/index.json. Rebuild with the `index` command.
The agent gets a `search_docs` tool backed by this.
"""
import json
import math
import os
import re
from collections import Counter

EXTS = {".md", ".txt", ".py", ".go", ".rs", ".js", ".ts", ".java", ".c", ".cc",
        ".cpp", ".h", ".yaml", ".yml", ".toml", ".sh", ".dockerfile"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
             "build", "target", ".idea", ".vscode", "data"}
CHUNK_LINES = 40
MAX_FILES = 400
MAX_FILE_BYTES = 200_000

TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


def _cosine(a: dict | list, b: dict | list) -> float:
    if isinstance(a, list):  # dense embedding vectors
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1e-9
        nb = math.sqrt(sum(x * x for x in b)) or 1e-9
        return dot / (na * nb)
    # sparse tf-idf dicts
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values())) or 1e-9
    nb = math.sqrt(sum(v * v for v in b.values())) or 1e-9
    return dot / (na * nb)


class DocIndex:
    def __init__(self, cfg: dict, llm=None):
        self.workdir = os.path.expanduser(cfg["agent"]["workdir"])
        self.path = os.path.join(cfg["memory"]["dir"], "index.json")
        self.llm = llm
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"backend": None, "chunks": [], "idf": {}}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f)

    # ---- building -------------------------------------------------------
    def _collect_chunks(self):
        chunks = []
        n_files = 0
        for root, dirs, files in os.walk(self.workdir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in EXTS and fn != "Dockerfile":
                    continue
                p = os.path.join(root, fn)
                try:
                    if os.path.getsize(p) > MAX_FILE_BYTES:
                        continue
                    with open(p, errors="replace") as f:
                        lines = f.read().splitlines()
                except OSError:
                    continue
                rel = os.path.relpath(p, self.workdir)
                for i in range(0, len(lines), CHUNK_LINES):
                    body = "\n".join(lines[i:i + CHUNK_LINES]).strip()
                    if len(body) > 40:
                        chunks.append({"file": rel, "line": i + 1, "text": body})
                n_files += 1
                if n_files >= MAX_FILES:
                    return chunks
        return chunks

    def build(self) -> str:
        chunks = self._collect_chunks()
        if not chunks:
            return "No indexable files found in workdir."

        # try real embeddings first (Ollama nomic-embed-text)
        vecs = None
        if self.llm is not None:
            texts = [c["text"][:1500] for c in chunks]
            vecs = self._embed_batched(texts)

        if vecs:
            for c, v in zip(chunks, vecs):
                c["vec"] = v
            self.data = {"backend": "embeddings", "chunks": chunks, "idf": {}}
        else:
            # TF-IDF fallback: no model, no deps
            docs_tokens = [_tokens(c["text"]) for c in chunks]
            df = Counter()
            for toks in docs_tokens:
                df.update(set(toks))
            n = len(chunks)
            idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
            for c, toks in zip(chunks, docs_tokens):
                tf = Counter(toks)
                c["vec"] = {t: (cnt / len(toks)) * idf[t] for t, cnt in tf.items()}
            self.data = {"backend": "tfidf", "chunks": chunks, "idf": idf}

        self._save()
        return (f"Indexed {len(chunks)} chunks from workdir "
                f"(backend: {self.data['backend']}).")

    def _embed_batched(self, texts, batch=32):
        vecs = []
        for i in range(0, len(texts), batch):
            out = self.llm.embed(texts[i:i + batch])
            if not out:
                return None
            vecs.extend(out)
        return vecs

    # ---- querying -------------------------------------------------------
    def search(self, query: str, k: int = 4) -> str:
        if not self.data["chunks"]:
            return "Index is empty — say 'index' to build it first."
        backend = self.data["backend"]
        if backend == "embeddings":
            qv = self.llm.embed([query]) if self.llm else None
            if not qv:
                return "Embedding backend unavailable — rebuild index with 'index'."
            qvec = qv[0]
        else:
            toks = _tokens(query)
            tf = Counter(toks)
            idf = self.data["idf"]
            qvec = {t: (c / max(len(toks), 1)) * idf.get(t, 1.0) for t, c in tf.items()}

        scored = sorted(self.data["chunks"],
                        key=lambda c: _cosine(qvec, c["vec"]), reverse=True)[:k]
        out = []
        for c in scored:
            out.append(f"--- {c['file']}:{c['line']} ---\n{c['text'][:800]}")
        return "\n".join(out)
