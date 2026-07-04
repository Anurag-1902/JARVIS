"""GitHub repo understanding. Paste any github.com URL and Sentry inspects it
via the GitHub API — no clone needed: metadata, languages, file tree, README,
and its Dockerfile/compose/Makefile/package.json parsed with the same
config_parser used for local projects.

`clone <url>` additionally clones it into ~/sentry-repos/<name> and creates a
workspace, unlocking every local feature (RAG index, plans, execution) on it.

Unauthenticated GitHub API allows 60 requests/hour. Set GITHUB_TOKEN in the
environment to raise that to 5000/hour.
"""
import base64
import json
import os
import re
import tempfile

import requests

from .config_parser import parse_all, summarize

GH_URL = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([\w.\-]+)/([\w.\-]+?)(?:\.git)?(?:/tree/([\w.\-/%]+))?(?:[/?#\s]|$)")

INTERESTING = ("dockerfile", "docker-compose", "compose.y", "makefile",
               "package.json", "go.mod", "requirements", "setup.py",
               "pyproject", "cargo.toml", "pom.xml", "main.", "app.",
               "readme", ".github/workflows", "src/", "cmd/")

CONFIG_FILES = ("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                "compose.yaml", "Makefile", "package.json", "go.mod")


def find_github_url(text: str):
    m = GH_URL.search(text)
    if not m:
        return None
    return {"owner": m.group(1), "repo": m.group(2),
            "branch": (m.group(3) or "").split("/")[0] or None,
            "url": f"https://github.com/{m.group(1)}/{m.group(2)}"}


class RepoInspector:
    def __init__(self, get=None):
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "sentry-agent"}
        tok = os.environ.get("GITHUB_TOKEN")
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        self.get = get or (lambda url: requests.get(url, headers=headers, timeout=20))

    def _json(self, url):
        r = self.get(url)
        if r.status_code == 404:
            return {"__error__": "not found (private repo or bad URL?)"}
        if r.status_code == 403:
            return {"__error__": "GitHub API rate limit hit — set GITHUB_TOKEN or try later"}
        if r.status_code != 200:
            return {"__error__": f"GitHub API HTTP {r.status_code}"}
        return r.json()

    def inspect(self, url: str) -> str:
        ref = find_github_url(url)
        if not ref:
            return "That doesn't look like a GitHub repo URL."
        o, rname = ref["owner"], ref["repo"]
        api = f"https://api.github.com/repos/{o}/{rname}"

        meta = self._json(api)
        if "__error__" in meta:
            return f"Couldn't inspect {o}/{rname}: {meta['__error__']}"
        branch = ref["branch"] or meta.get("default_branch", "main")

        langs = self._json(f"{api}/languages")
        lang_str = ", ".join(list(langs.keys())[:5]) if "__error__" not in langs else "?"

        tree = self._json(f"{api}/git/trees/{branch}?recursive=1")
        paths, truncated = [], False
        if "__error__" not in tree:
            truncated = tree.get("truncated", False)
            paths = [t["path"] for t in tree.get("tree", []) if t["type"] == "blob"]

        interesting = [p for p in paths if any(k in p.lower() for k in INTERESTING)][:40]

        # README (first part)
        readme = ""
        rd = self._json(f"{api}/readme")
        if "__error__" not in rd and rd.get("content"):
            try:
                readme = base64.b64decode(rd["content"]).decode("utf-8", "replace")[:1500]
            except Exception:
                pass

        # fetch root config files and run them through the SAME parser as local projects
        cfg_summary = ""
        present = [p for p in paths if p in CONFIG_FILES]
        if present:
            with tempfile.TemporaryDirectory() as td:
                for fn in present:
                    raw = self.get(
                        f"https://raw.githubusercontent.com/{o}/{rname}/{branch}/{fn}")
                    if raw.status_code == 200:
                        with open(os.path.join(td, fn), "w", errors="replace") as f:
                            f.write(raw.text)
                cfg_summary = summarize(parse_all(td))

        parts = [
            f"GITHUB REPO {o}/{rname} (branch {branch})",
            f"About: {meta.get('description') or '(no description)'}",
            f"Languages: {lang_str} | ⭐ {meta.get('stargazers_count', '?')} | "
            f"forks {meta.get('forks_count', '?')} | "
            f"license {(meta.get('license') or {}).get('spdx_id', 'none')} | "
            f"updated {str(meta.get('pushed_at', ''))[:10]}",
            f"Files ({len(paths)} total{', tree truncated' if truncated else ''}), "
            f"notable: {', '.join(interesting) or '(none matched)'}",
        ]
        if cfg_summary:
            parts.append(cfg_summary)
        if readme:
            parts.append("README (start):\n" + readme)
        parts.append(f"To work on it locally: clone {ref['url']}")
        return "\n".join(parts)
