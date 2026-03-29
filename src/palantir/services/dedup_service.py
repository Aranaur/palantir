"""Content-based deduplication using shingled Jaccard similarity."""

from __future__ import annotations

import logging
import re
import unicodedata

from palantir.models.post import RawPost

logger = logging.getLogger(__name__)

_SHINGLE_SIZE = 3
_DEFAULT_THRESHOLD = 0.55


def _normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace, remove punctuation."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _shingles(text: str, n: int = _SHINGLE_SIZE) -> set[str]:
    """Return set of character n-grams."""
    words = _normalize(text).split()
    if len(words) < n:
        return {" ".join(words)}
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def deduplicate(
    posts: list[RawPost],
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[RawPost]:
    """Remove near-duplicate posts, keeping the first (longest text) occurrence."""
    if not posts:
        return posts

    # Sort by text length descending — prefer longer (more complete) versions
    sorted_posts = sorted(posts, key=lambda p: len(p.text), reverse=True)

    kept: list[tuple[RawPost, set[str]]] = []
    removed = 0

    for post in sorted_posts:
        post_shingles = _shingles(post.text)

        is_dup = False
        for _, existing_shingles in kept:
            if _jaccard(post_shingles, existing_shingles) >= threshold:
                is_dup = True
                break

        if is_dup:
            removed += 1
        else:
            kept.append((post, post_shingles))

    if removed:
        logger.info("Dedup: removed %d duplicate(s) from %d posts", removed, len(posts))

    return [post for post, _ in kept]
