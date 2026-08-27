from __future__ import annotations

import hashlib
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Shared utilities — use these in both ResponseCache and SharedRedisCache
# ---------------------------------------------------------------------------

PRIVACY_PATTERNS = re.compile(
    r"\b(balance|password|credit.card|ssn|social.security|user.\d+|account.\d+)\b",
    re.IGNORECASE,
)


def _is_uncacheable(query: str) -> bool:
    """Return True if query contains privacy-sensitive keywords."""
    return bool(PRIVACY_PATTERNS.search(query))


def _looks_like_false_hit(query: str, cached_key: str) -> bool:
    """Return True if query and cached key contain different 4-digit numbers (years, IDs)."""
    nums_q = set(re.findall(r"\b\d{4}\b", query))
    nums_c = set(re.findall(r"\b\d{4}\b", cached_key))
    return bool(nums_q and nums_c and nums_q != nums_c)


# ---------------------------------------------------------------------------
# In-memory cache (existing)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheEntry:
    key: str
    value: str
    created_at: float
    metadata: dict[str, str]


class ResponseCache:
    """In-memory semantic response cache with privacy and false-hit guardrails.

    Lookup is by n-gram cosine similarity rather than exact key match, so a
    reworded question can still be served from cache. Two guardrails keep that
    from being dangerous:

    - ``_is_uncacheable()`` — privacy-sensitive queries (balances, passwords,
      SSNs, per-user IDs) are never stored and never served from cache.
    - ``_looks_like_false_hit()`` — a match whose 4-digit numbers disagree with
      the query (e.g. "2024 deadline" vs "2026 deadline") is rejected and
      recorded in ``false_hit_log`` as evidence.

    Use ``SharedRedisCache`` instead when more than one gateway instance runs.
    """

    def __init__(self, ttl_seconds: int, similarity_threshold: float, max_entries: int = 1000):
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self.max_entries = max_entries
        self._entries: list[CacheEntry] = []
        self.false_hit_log: list[dict[str, object]] = []

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response by semantic similarity.

        Expired entries are dropped first, then every remaining entry is scored
        and the best one wins. A hit must clear ``similarity_threshold`` *and* the
        false-hit check. Returns ``(response, score)`` on a hit, ``(None, best_score)``
        on a miss.
        """
        if _is_uncacheable(query):
            return None, 0.0

        now = time.time()
        self._entries = [e for e in self._entries if (now - e.created_at) <= self.ttl_seconds]

        if not self._entries:
            return None, 0.0

        best_entry: CacheEntry | None = None
        best_score: float = 0.0

        for entry in self._entries:
            score = self.similarity(query, entry.key)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_score >= self.similarity_threshold and best_entry is not None:
            if _looks_like_false_hit(query, best_entry.key):
                self.false_hit_log.append(
                    {
                        "query": query,
                        "cached_key": best_entry.key,
                        "reason": "date_or_number_mismatch",
                        "ts": time.time(),
                    }
                )
                return None, best_score
            return best_entry.value, best_score

        return None, best_score

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response, unless the query is privacy-sensitive.

        Oldest-first eviction keeps the cache bounded at ``max_entries``.
        """
        if _is_uncacheable(query):
            return
        if len(self._entries) >= self.max_entries:
            self._entries.pop(0)
        self._entries.append(
            CacheEntry(
                key=query,
                value=value,
                created_at=time.time(),
                metadata=metadata or {},
            )
        )

    @staticmethod
    def similarity(a: str, b: str) -> float:
        """Cosine similarity over word tokens plus character 3-grams.

        Each string is tokenized into its lowercased words *and* the character
        3-grams inside each word, e.g. "hello" -> ["hello", "hel", "ell", "llo"].
        The two token bags become ``Counter`` vectors and the score is
        ``dot(a, b) / (|a| * |b|)``, so the result is in [0.0, 1.0].

        Character n-grams are used instead of plain Jaccard token overlap because
        Jaccard is all-or-nothing per word: it cannot tell that "breaker pattern"
        and "breaker design" share most of their content, and it ignores how many
        times a token appears. Returns 1.0 for identical strings.
        """
        if a == b:
            return 1.0

        def _tokenize(text: str) -> list[str]:
            tokens: list[str] = []
            words = re.findall(r"\w+", text.lower())
            for word in words:
                tokens.append(word)
                if len(word) >= 3:
                    for i in range(len(word) - 2):
                        tokens.append(word[i : i + 3])
            return tokens

        tokens_a = _tokenize(a)
        tokens_b = _tokenize(b)

        if not tokens_a or not tokens_b:
            return 0.0

        counts_a = Counter(tokens_a)
        counts_b = Counter(tokens_b)

        intersection = set(counts_a.keys()) & set(counts_b.keys())
        dot_product = sum(counts_a[token] * counts_b[token] for token in intersection)

        norm_a = math.sqrt(sum(count**2 for count in counts_a.values()))
        norm_b = math.sqrt(sum(count**2 for count in counts_b.values()))

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot_product / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Redis shared cache (new)
# ---------------------------------------------------------------------------


class SharedRedisCache:
    """Redis-backed shared cache for multi-instance deployments.

    An in-memory cache lives inside one process, so N gateway replicas keep N
    separate caches and the hit rate drops by roughly a factor of N. Storing the
    entries in Redis means every replica reads and writes the same state.

    Data model:
        Key    = "{prefix}{query_hash}"   (Redis String namespace)
        Value  = Redis Hash with fields:  "query", "response"
        TTL    = Redis EXPIRE (automatic cleanup — no manual eviction)

    Similarity lookup: SCAN all keys under self.prefix, HGET each entry's
    "query" field, and score it locally with ResponseCache.similarity().

    Helpers:
        _is_uncacheable(query)          — True if privacy-sensitive
        _looks_like_false_hit(q, key)   — True if 4-digit numbers differ
        self._query_hash(query)         — deterministic short hash for Redis key
        ResponseCache.similarity(a, b)  — reuse your improved similarity function
    """

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        similarity_threshold: float,
        prefix: str = "rl:cache:",
    ):
        import redis as redis_lib

        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self.prefix = prefix
        self.false_hit_log: list[dict[str, object]] = []
        self._redis: Any = redis_lib.Redis.from_url(redis_url, decode_responses=True)

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return bool(self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response in Redis.

        Privacy-sensitive queries short-circuit to a miss. Otherwise the hashed
        key is tried first (one HGET, score 1.0); on a miss the prefix is scanned
        and each stored query is scored with ``ResponseCache.similarity``. The best
        match at or above ``similarity_threshold`` is returned unless it fails the
        false-hit check, in which case it is logged and reported as a miss.

        Returns ``(response, score)`` on a hit and ``(None, best_score)`` on a miss,
        so the caller can see how close the near-miss was.
        """
        if _is_uncacheable(query):
            return None, 0.0

        exact_key = f"{self.prefix}{self._query_hash(query)}"
        exact_response: str | None = self._redis.hget(exact_key, "response")
        if exact_response is not None:
            return exact_response, 1.0

        best_query: str | None = None
        best_response: str | None = None
        best_score: float = 0.0

        for key in self._redis.scan_iter(f"{self.prefix}*"):
            cached_query: str | None = self._redis.hget(key, "query")
            if cached_query is None:
                continue
            score = ResponseCache.similarity(query, cached_query)
            if score > best_score:
                best_score = score
                best_query = cached_query
                best_response = self._redis.hget(key, "response")

        if (
            best_score >= self.similarity_threshold
            and best_response is not None
            and best_query is not None
        ):
            if _looks_like_false_hit(query, best_query):
                self.false_hit_log.append(
                    {
                        "query": query,
                        "cached_key": best_query,
                        "reason": "date_or_number_mismatch",
                    }
                )
                return None, best_score
            return best_response, best_score

        return None, best_score

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response in Redis under a TTL.

        Privacy-sensitive queries are dropped before they ever reach Redis. The
        original query text is stored alongside the response because the
        similarity scan needs it. Expiry is delegated to Redis EXPIRE, so stale
        entries are evicted by the server and no manual sweep is needed.
        """
        if _is_uncacheable(query):
            return
        key = f"{self.prefix}{self._query_hash(query)}"
        self._redis.hset(key, mapping={"query": query, "response": value})
        self._redis.expire(key, self.ttl_seconds)

    def flush(self) -> None:
        """Remove all entries with this cache prefix (for testing)."""
        for key in self._redis.scan_iter(f"{self.prefix}*"):
            self._redis.delete(key)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()

    @staticmethod
    def _query_hash(query: str) -> str:
        """Deterministic short hash for a query string."""
        return hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]
