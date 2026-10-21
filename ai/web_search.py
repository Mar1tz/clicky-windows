"""
Real-time web search for Tora — tutor-grade grounding layer.

19 Oct 2026, the owner: "it's still very slow". Every question that searched
took 12-17 s to its first word: the free search below took 9-12 s (one
search engine after another, most of them answering nothing from this PC),
and the voice then spent 5-8 s on a 4,000-character result. Now, in order:

  1. Google Search through Gemini (the fast model with the google_search
     tool): a short, dated answer with its sources, 1.9-2.3 s measured.
     On a HeyTora plan the server must allow it (docs/SERVER_SPEED.md);
     refused once, it is not asked again this run.
  2. The free engines all at once, the first with results wins, capped at
     QUICK_BUDGET_S, from their own snippets - no pages fetched.
  3. What follows below, the old deep search, only when both gave nothing -
     and given DEEP_BUDGET_S at most: it once took 15 s on its own.

Strategy (free path, no API key required):
  1. Expand the user's question into 1-2 focused sub-queries.
  2. Hit DuckDuckGo HTML search for each to collect top result URLs.
  3. Fetch the top pages concurrently and strip to plain text.
  4. Assemble a compact, source-cited context block for the LLM.

If TAVILY_API_KEY is set, Tavily's deep-search is used instead (higher
signal-to-noise, faster, cleaner summaries).
"""

from __future__ import annotations

import asyncio
import re
from html import unescape
from typing import List, Tuple
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import httpx

from config import cfg


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_PAGES = 3
PAGE_CHAR_BUDGET = 1400            # per-page excerpt size
OVERALL_CHAR_BUDGET = 5500         # cap on the whole context block
FETCH_TIMEOUT = 6.0


# ── Public API ────────────────────────────────────────────────────────────────

# Explicit signs the user wants the WEB, as opposed to the screen in front
# of them. Deliberately conservative: this is a screen assistant, and the
# live log showed the cascade path searching the web for "Hello.", for a
# chess position and for "how do I add people" in Google Meet — 5-20 s of
# scraping per turn, all of it irrelevant. Saying "search for X" always works.
_SEARCH_EXPLICIT = re.compile(
    r"\b(search|look\s*(it\s*)?up|google\s+(it|for|that|how|what|where|why|who|when|if)|bing|"
    r"(find|check|see)\s+(it\s+)?(online|on\s+the\s+(web|internet|net))|"
    r"on\s+the\s+(web|internet)|web\s+search|"
    r"latest|newest|news|headlines|breaking|"
    r"price\s+of|how\s+much\s+(is|does|do|are)\s+(a|an|the)\b|stock\s+price|"
    r"weather|forecast|exchange\s+rate|"
    r"who\s+(won|is\s+the\s+(ceo|president|prime\s+minister))|"
    r"when\s+(is|was|does|did)\s+.*(release|come\s+out|launch|start|end)|"
    r"release\s+date|what\s+year|current(ly)?\s+(version|price|status)|"
    r"score\s+of|results?\s+of\s+the)\b",
    re.I,
)
# Things that are never a web search, however they are phrased.
_SEARCH_NEVER = re.compile(
    r"^\s*(hi|hello|hey|thanks?|thank\s+you|ok(ay)?|yes|no|yo|cheers)\b(?!.*\b(search|look\s*up|google)\b)|"
    r"\b(on\s+(my|the)\s+screen|this\s+(screen|window|app|page|tab|button|"
    r"menu|dialog|game|board|photo|image|chart|code|file)|"
    r"where\s+(is|are|'s)|where'?s|show\s+me|point\s+(at|to)|click|"
    r"how\s+do\s+i\b|walk\s+me|guide\s+me|next\s+step|"
    r"best\s+move|my\s+(chess|game|screen|code|document))\b",
    re.I,
)


def wants_search(text: str) -> bool:
    """Should this question go to the web at all?

    Only an explicit ask does. Everything about the screen, the app, a game
    or a walkthrough is answered from what Tora can already see.
    """
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if _SEARCH_NEVER.search(t):
        return False
    return bool(_SEARCH_EXPLICIT.search(t))


async def search(query: str, max_results: int = MAX_PAGES) -> str:
    """Return a plain-text, source-cited search context."""
    query = query.strip()
    if not query:
        return ""

    if cfg.search_provider() == "tavily":
        try:
            return await _tavily(query, max_results)
        except Exception:
            pass  # fall through to free path

    found = await _google(query)
    if found:
        return found
    found = await _quick(query)
    if found:
        return found
    try:
        return await asyncio.wait_for(_free_deep_search(query, max_results), DEEP_BUDGET_S)
    except asyncio.TimeoutError:
        return ""


# ── 1. Google Search, through Gemini ──────────────────────────────────────────

GOOGLE_TIMEOUT_S = 8.0
_google_refused = False          # the plan's server does not forward it (yet): not asked again


async def _google(query: str) -> str:
    global _google_refused
    if _google_refused:
        return ""
    from datetime import date
    from ai import gemini
    ask = (f"Today is {date.today():%A %d %B %Y}. Search the web and answer in at most three short "
           f"sentences, with the date of anything that changes (prices, scores, weather, news). "
           f"Question: {query}")
    body = {"contents": [{"role": "user", "parts": [{"text": ask}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 400, "temperature": 0.2}}
    try:
        r = await asyncio.wait_for(gemini._post(gemini._url(gemini.Models.VISION_FAST, "generateContent"),
                                                body, GOOGLE_TIMEOUT_S, busy_retries=False), GOOGLE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - slow or offline: the free engines
        return ""
    if r.status_code != 200:
        if r.status_code in (400, 403) and ("not forwarded" in r.text or "google_search" in r.text):
            _google_refused = True
        return ""
    data = r.json()
    text = gemini.first_text(data).strip()
    if not text:
        return ""
    meta = (data.get("candidates") or [{}])[0].get("groundingMetadata") or {}
    sources = []
    for chunk in (meta.get("groundingChunks") or [])[:3]:
        web = chunk.get("web") or {}
        if web.get("title"):
            sources.append(web["title"])
    return text + (("\nSources: " + "; ".join(sources)) if sources else "")


# ── 2. The free engines, together ─────────────────────────────────────────────

QUICK_BUDGET_S = 7.0             # Bing, the one that answers most, took up to 6.7 s (19 Oct)
DEEP_BUDGET_S = 5.0              # the old deep search, last: it took 15 s on its own
QUICK_BACKENDS = ("bing", "yahoo", "duckduckgo", "brave", "google", "mojeek")
QUICK_CHARS = 1500


async def _quick(query: str) -> str:
    """The first engine with results, from their snippets; "" past the budget."""
    try:
        from ddgs import DDGS
    except ImportError:
        return ""

    def one(backend: str) -> list:
        try:
            return list(DDGS(timeout=QUICK_BUDGET_S).text(query, max_results=5, region="us-en", backend=backend))
        except Exception:  # noqa: BLE001 - most engines answer nothing from some PCs
            return []

    loop = asyncio.get_running_loop()
    pending = {loop.run_in_executor(None, one, b) for b in QUICK_BACKENDS}
    deadline = loop.time() + QUICK_BUDGET_S
    hits: list = []
    while pending and not hits and loop.time() < deadline:
        done, pending = await asyncio.wait(pending, timeout=deadline - loop.time(),
                                           return_when=asyncio.FIRST_COMPLETED)
        for f in done:
            if not hits and f.result():
                hits = f.result()
    parts = []
    for i, hit in enumerate(hits[:4], 1):
        title = (hit.get("title") or "").strip()
        body = " ".join((hit.get("body") or "").split())
        url = (hit.get("href") or hit.get("url") or "").strip()
        if title or body:
            parts.append(f"[{i}] {title} - {body} ({url})")
    return _truncate("\n".join(parts), QUICK_CHARS)


def build_search_context(results: str) -> str:
    if not results.strip():
        return ""
    return (
        "\n\n[Web Search Results — ground factual / recent claims in these. "
        "Cite source numbers like [1] when you use them.]\n"
        + results
        + "\n[End of search results]\n"
    )


# ── Tavily (premium path) ─────────────────────────────────────────────────────

async def _tavily(query: str, max_results: int) -> str:
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": cfg.tavily_api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": True,
    }
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()

    parts: list[str] = []
    if data.get("answer"):
        parts.append(f"Summary: {data['answer']}")

    for i, result in enumerate(data.get("results", []), 1):
        title = result.get("title", "").strip()
        url_str = result.get("url", "")
        body = (result.get("raw_content") or result.get("content") or "").strip()
        body = body[:PAGE_CHAR_BUDGET]
        parts.append(f"[{i}] {title} — {url_str}\n{body}")

    return _truncate("\n\n".join(parts), OVERALL_CHAR_BUDGET)


# ── Free deep search: DuckDuckGo HTML + page fetch ────────────────────────────

async def _free_deep_search(query: str, max_results: int) -> str:
    sub_queries = _expand_query(query)

    seen: set[str] = set()
    hits: list[Tuple[str, str]] = []   # (title, url)

    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        search_tasks = [_ddg_html_search(client, q) for q in sub_queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        for result in search_results:
            if isinstance(result, Exception):
                continue
            for title, url in result:
                if url in seen:
                    continue
                seen.add(url)
                hits.append((title, url))
                if len(hits) >= max_results * 2:
                    break
            if len(hits) >= max_results * 2:
                break

        hits = hits[:max_results]
        if not hits:
            return ""

        fetch_tasks = [_fetch_text(client, url) for _, url in hits]
        pages = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    parts: list[str] = []
    for i, ((title, url), page) in enumerate(zip(hits, pages), 1):
        body = "" if isinstance(page, Exception) else page
        body = (body or "").strip()[:PAGE_CHAR_BUDGET]
        if not body:
            continue
        parts.append(f"[{i}] {title} — {url}\n{body}")

    return _truncate("\n\n".join(parts), OVERALL_CHAR_BUDGET)


# ── Query expansion ───────────────────────────────────────────────────────────

_STOPWORDS = {
    "what", "whats", "what's", "how", "why", "when", "where", "who",
    "is", "are", "the", "a", "an", "of", "on", "in", "to", "for",
    "do", "does", "i", "you", "me", "this", "that", "please", "tell",
    "explain", "tora", "hey",
}


def _expand_query(query: str) -> list[str]:
    """Produce 1-2 focused queries: the original + one stripped/reformulated."""
    from datetime import datetime
    base = query.strip()
    queries = [base]

    tokens = re.findall(r"[A-Za-z0-9\-']+", base.lower())
    keywords = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]

    # For recency-sensitive questions ("best X", "top X", "who is X now",
    # "latest X", "current X") append the current year so DDG surfaces
    # fresh results instead of evergreen pages.
    RECENCY_RE = re.compile(
        r"\b(best|top|greatest|popular|trending|latest|current|"
        r"now|today|2024|2025|2026)\b", re.IGNORECASE
    )
    WHO_IS_RE = re.compile(r"\b(who\s+is|who\s+are|what\s+is\s+the\s+best|"
                           r"what\s+are\s+the\s+top)\b", re.IGNORECASE)
    year = str(datetime.now().year)

    if RECENCY_RE.search(base) or WHO_IS_RE.search(base):
        # Build a tight keyword query + year for the freshest hits
        kw_year = " ".join(keywords)
        if year not in kw_year:
            kw_year = kw_year + " " + year
        if kw_year.strip() != base.lower():
            queries.append(kw_year.strip())
    elif keywords and len(keywords) < len(tokens):
        kw = " ".join(keywords)
        if kw and kw != base.lower():
            queries.append(kw)

    return queries[:2]


# ── DuckDuckGo HTML scraper ───────────────────────────────────────────────────

_DDG_URL = "https://html.duckduckgo.com/html/"
_RESULT_LINK_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


async def _ddg_html_search(client: httpx.AsyncClient, query: str) -> list[Tuple[str, str]]:
    """Search DuckDuckGo via the maintained `ddgs` package.

    The previous implementation scraped html.duckduckgo.com directly, which
    DDG now rate-limits / serves with anti-bot 202 stubs. The `ddgs` library
    rotates endpoints, user agents, and parses the JSON API — it's the
    canonical open-source path and is actively maintained.

    We run it in a thread because ddgs is sync; the rest of the search
    pipeline is async.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        # Fall back to the old regex path if ddgs isn't installed
        r = await client.post(_DDG_URL, data={"q": query, "kl": "us-en"})
        if r.status_code != 200:
            return []
        out: list[Tuple[str, str]] = []
        for match in _RESULT_LINK_RE.finditer(r.text):
            url = _normalize_ddg_url(match.group(1))
            # _html_to_text, not the _strip_html this used to name —
            # that function does not exist, so this fallback raised
            # NameError instead of returning results whenever ddgs was
            # missing. It is reachable again now the live model can search.
            title = _html_to_text(match.group(2)).strip()
            if url and title:
                out.append((title, url))
                if len(out) >= 4:
                    break
        return out

    def _sync_search() -> list[Tuple[str, str]]:
        try:
            results = list(DDGS().text(query, max_results=6, region="us-en"))
        except Exception:
            return []
        out: list[Tuple[str, str]] = []
        for hit in results:
            title = (hit.get("title") or "").strip()
            url = (hit.get("href") or hit.get("url") or "").strip()
            if title and url:
                out.append((title, url))
            if len(out) >= 4:
                break
        return out

    return await asyncio.to_thread(_sync_search)


def _normalize_ddg_url(href: str) -> str:
    """DDG wraps real URLs in /l/?uddg=<encoded>. Unwrap them."""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href or href.startswith("/l/"):
        try:
            qs = parse_qs(urlparse(href).query)
            target = qs.get("uddg", [""])[0]
            if target:
                return unquote(target)
        except Exception:
            return ""
    if href.startswith("http"):
        return href
    return ""


# ── Page fetch + text extraction ──────────────────────────────────────────────

_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>",
                              re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url)
        if r.status_code >= 400:
            return ""
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            return ""
        return _html_to_text(r.text)
    except Exception:
        return ""


def _html_to_text(html: str) -> str:
    html = _SCRIPT_STYLE_RE.sub(" ", html)

    # Prefer <main> or <article> if present; otherwise whole body
    body_match = re.search(
        r"<(article|main)\b[^>]*>(.*?)</\1>", html, re.IGNORECASE | re.DOTALL,
    )
    if body_match:
        core = body_match.group(2)
    else:
        body_match = re.search(r"<body\b[^>]*>(.*?)</body>",
                               html, re.IGNORECASE | re.DOTALL)
        core = body_match.group(1) if body_match else html

    text = _TAG_RE.sub(" ", core)
    text = unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
