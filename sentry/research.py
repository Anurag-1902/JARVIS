"""Web research. Searches DuckDuckGo Lite (no API key needed), fetches the top
results, strips them to readable text, and returns a source-cited digest the
model uses to answer with current information.

Auto-triggers on queries that likely need fresh/external knowledge
("best practices", "latest", "tutorial", ...) and is also callable explicitly
as the `research` tool.
"""
import html
import re
import time
import urllib.parse

import requests

TRIGGERS = ("best practice", "best practices", "tutorial", "how do i", "how to",
            "latest", "newest", "current version", "documentation for",
            "docs for", "recommended way", "state of the art", "compare",
            "vs ", " 2025", " 2026")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) sentry-agent/0.3"}
TAG = re.compile(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", re.S | re.I)
ANYTAG = re.compile(r"<[^>]+>")
WS = re.compile(r"[ \t]{2,}")
NL = re.compile(r"\n{3,}")

# DDG lite result anchors: <a rel="nofollow" href="URL" class='result-link'>Title</a>
LITE_LINK = re.compile(
    r'<a[^>]+href="(?P<url>[^"]+)"[^>]*class=[\'"]result-link[\'"][^>]*>(?P<title>.*?)</a>',
    re.S | re.I)
# fallback: any external anchor in the results table
ANY_LINK = re.compile(r'<a[^>]+href="(?P<url>https?://[^"]+)"[^>]*>(?P<title>[^<]{10,120})</a>')


def should_research(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in TRIGGERS)


def _clean_url(u: str) -> str:
    # DDG sometimes wraps urls as //duckduckgo.com/l/?uddg=<encoded>
    if "uddg=" in u:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
        if "uddg" in qs:
            return qs["uddg"][0]
    if u.startswith("//"):
        u = "https:" + u
    return u


def _to_text(page_html: str) -> str:
    t = TAG.sub(" ", page_html)
    t = ANYTAG.sub(" ", t)
    t = html.unescape(t)
    t = WS.sub(" ", t)
    t = NL.sub("\n\n", t)
    return t.strip()


class Researcher:
    def __init__(self, get=None):
        self.get = get or (lambda url, **kw: requests.get(url, headers=HEADERS,
                                                          timeout=20, **kw))

    def search(self, query: str, k: int = 3) -> list[dict]:
        url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query)
        r = self.get(url)
        r.raise_for_status()
        seen, results = set(), []
        matches = list(LITE_LINK.finditer(r.text)) or list(ANY_LINK.finditer(r.text))
        for m in matches:
            u = _clean_url(m.group("url"))
            title = ANYTAG.sub("", m.group("title")).strip()
            if not u.startswith("http") or "duckduckgo.com" in u or u in seen:
                continue
            seen.add(u)
            results.append({"title": title[:120], "url": u})
            if len(results) >= k:
                break
        return results

    def fetch_readable(self, url: str, max_chars: int = 1800) -> str:
        try:
            r = self.get(url)
        except requests.RequestException as e:
            return f"(couldn't access this resource: {type(e).__name__})"
        if r.status_code in (401, 402, 403):
            return "(couldn't access this resource — paywalled or blocked)"
        if r.status_code != 200:
            return f"(couldn't access this resource — HTTP {r.status_code})"
        text = _to_text(r.text)
        if len(text) < 200:
            return "(page had no readable text — likely JS-rendered)"
        return text[:max_chars]

    def research(self, query: str) -> str:
        """Search + fetch + return a source-cited digest for the model."""
        try:
            hits = self.search(query)
        except requests.RequestException as e:
            return (f"Web search unavailable ({type(e).__name__}) — "
                    "answer from local knowledge and say the info may be dated.")
        if not hits:
            return "Search returned no usable results — answer from local knowledge."
        today = time.strftime("%Y-%m-%d")
        parts = [f"WEB RESEARCH for '{query}' (fetched {today}). "
                 "Cite sources by number, e.g. 'according to [1]':"]
        for i, h in enumerate(hits, 1):
            body = self.fetch_readable(h["url"])
            parts.append(f"[{i}] {h['title']}\n    {h['url']}\n{body}")
        return "\n\n".join(parts)
