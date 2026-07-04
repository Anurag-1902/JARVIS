"""GitHub repo understanding. Paste any public repo URL and Sentry fetches its
metadata, README, file tree, and key config files, building a digest the model
uses to explain the project and produce exact step-by-step run commands.

Uses the public GitHub API (no token needed; 60 requests/hour unauthenticated).
Digests are cached to data/repos/ so re-pasting a URL costs nothing.
"""
import base64
import json
import os
import re
import time

import requests

URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+?)(?:\.git)?(?:[/#?]\S*)?(?:\s|$)")

KEY_FILES = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
             "package.json", "requirements.txt", "Makefile", "go.mod",
             "pyproject.toml", "setup.py", ".env.example")

HEADERS = {"Accept": "application/vnd.github+json",
           "User-Agent": "sentry-agent/0.4"}


def find_repo_url(text: str):
    m = URL_RE.search(text or "")
    return (m.group(1), m.group(2)) if m else None


class RepoInspector:
    def __init__(self, cache_dir: str, get=None):
        self.cache_dir = os.path.join(cache_dir, "repos")
        os.makedirs(self.cache_dir, exist_ok=True)
        headers = dict(HEADERS)
        tok = os.environ.get("GITHUB_TOKEN")
        if tok:  # optional: raises rate limit from 60/hr to 5000/hr
            headers["Authorization"] = f"Bearer {tok}"
        self.get = get or (lambda url: requests.get(url, headers=headers, timeout=20))

    def _api(self, path: str):
        r = self.get(f"https://api.github.com{path}")
        if r.status_code == 403 and "rate limit" in r.text.lower():
            raise RuntimeError("GitHub API rate limit hit (60/hr unauthenticated) — try again later.")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def inspect(self, owner: str, repo: str) -> str:
        cache = os.path.join(self.cache_dir, f"{owner}--{repo}.json")
        if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 86400:
            with open(cache) as f:
                return json.load(f)["digest"]

        meta = self._api(f"/repos/{owner}/{repo}")
        if meta is None:
            return (f"Repo github.com/{owner}/{repo} not found — it may be private, "
                    "renamed, or the URL has a typo.")
        branch = meta.get("default_branch", "main")

        # README
        readme = ""
        rd = self._api(f"/repos/{owner}/{repo}/readme")
        if rd and rd.get("content"):
            try:
                readme = base64.b64decode(rd["content"]).decode("utf-8", errors="replace")
            except Exception:
                readme = ""

        # file tree (top levels)
        tree_txt, key_present = "", []
        tr = self._api(f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
        if tr and tr.get("tree"):
            paths = [t["path"] for t in tr["tree"] if t["type"] == "blob"]
            key_present = [p for p in paths if os.path.basename(p) in KEY_FILES
                           and p.count("/") <= 1][:10]
            top = sorted({p.split("/")[0] + ("/" if "/" in p else "") for p in paths})
            tree_txt = ", ".join(top[:30]) + (" …" if len(top) > 30 else "")
            if tr.get("truncated"):
                tree_txt += " (tree truncated — large repo)"

        # key config files (raw)
        configs = {}
        for p in key_present[:5]:
            r = self.get(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{p}")
            if r.status_code == 200:
                configs[p] = r.text[:1500]

        lang = meta.get("language") or "unspecified"
        digest = (
            f"ACTIVE GITHUB REPO: {owner}/{repo} (branch {branch})\n"
            f"Description: {meta.get('description') or '—'}\n"
            f"Primary language: {lang} | Stars: {meta.get('stargazers_count', 0)} "
            f"| License: {(meta.get('license') or {}).get('spdx_id', '—')}\n"
            f"Clone: git clone https://github.com/{owner}/{repo}.git\n"
            f"Top-level contents: {tree_txt or '—'}\n"
        )
        if configs:
            digest += "\nKEY CONFIG FILES:\n"
            for p, body in configs.items():
                digest += f"--- {p} ---\n{body}\n"
        if readme:
            digest += f"\nREADME (may be truncated):\n{readme[:5000]}"
        digest = digest[:9000]

        with open(cache, "w") as f:
            json.dump({"digest": digest, "ts": time.time()}, f)
        return digest
