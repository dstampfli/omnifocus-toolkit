#!/usr/bin/env python3
"""Read X (Twitter) post text via the X API v2, for triage classification.

Optional and token-gated: with no X_BEARER_TOKEN, none of this runs. The fetched
post text is model INPUT only — it is never embedded into any OmniJS write
source. All network/parse errors degrade to None; nothing here raises.
"""

import json
import re
import threading
import urllib.request
from typing import List, Optional

# x.com / twitter.com / mobile.<...>  ".../status/<id>". The (?<![\w.]) guard
# stops false hits like 'box.com/a/status/1' matching via the 'x.com' substring.
_STATUS_RE = re.compile(
    r"(?<![\w.])(?:mobile\.)?(?:x|twitter)\.com/[^/\s]+/status/(\d+)",
    re.IGNORECASE,
)

_API_URL = (
    "https://api.x.com/2/tweets/{id}"
    "?tweet.fields=note_tweet,created_at,author_id"
    "&expansions=author_id&user.fields=username,name"
)


def extract_tweet_ids(text: Optional[str]) -> List[str]:
    """Return de-duplicated X post IDs in `text`, in first-seen order."""
    if not text:
        return []
    seen: List[str] = []
    for m in _STATUS_RE.finditer(text):
        tid = m.group(1)
        if tid not in seen:
            seen.append(tid)
    return seen


def _format_author(users: list) -> str:
    author = users[0] if users else {}
    name = (author.get("name") or "").strip()
    username = (author.get("username") or "").strip()
    if name and username:
        return f"{name} (@{username})"
    if username:
        return f"@{username}"
    return name


def fetch_post_text(tweet_id: str, token: str, *, timeout: int = 20) -> Optional[str]:
    """Look up one X post; return 'X post by <who>: <text>' or None on any error.

    Prefers note_tweet.text (full text of long posts) over the truncated `text`.
    I/O boundary: catches everything, never raises."""
    req = urllib.request.Request(
        _API_URL.format(id=tweet_id),
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
        data = payload.get("data") or {}
        text = (data.get("note_tweet") or {}).get("text") or data.get("text") or ""
        text = text.strip()
        if not text:
            return None
        who = _format_author((payload.get("includes") or {}).get("users") or [])
        return f"X post by {who}: {text}" if who else f"X post: {text}"
    except Exception:  # noqa: BLE001 — network/HTTP/parse all degrade to None
        return None


class XPostFetcher:
    """Run-scoped X fetcher: dedupes lookups by id and caps total lookups at
    max_uses. `fetch_fn` is injected for testing."""

    def __init__(self, token: Optional[str], max_uses: int, fetch_fn=fetch_post_text):
        self.token = token
        self.max_uses = max_uses
        self.fetch_fn = fetch_fn
        self.cache = {}
        self.used = 0
        # The reviewer shares one fetcher across parallel task-review threads, so
        # the check-then-increment on `used` and the cache writes must be atomic.
        self._lock = threading.Lock()

    def texts_for(self, text: Optional[str]) -> List[str]:
        """Fetched post texts for the tweet IDs in `text`. [] when no token."""
        if not self.token:
            return []
        out: List[str] = []
        with self._lock:
            for tid in extract_tweet_ids(text):
                if tid not in self.cache:
                    if self.used >= self.max_uses:
                        continue
                    self.cache[tid] = self.fetch_fn(tid, self.token)
                    self.used += 1
                if self.cache[tid]:
                    out.append(self.cache[tid])
        return out
