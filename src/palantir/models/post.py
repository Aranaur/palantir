from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class RawPost:
    """Raw post collected from a source (Telegram channel or RSS feed)."""

    source_id: str
    post_id: str
    text: str
    url: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def unique_key(self) -> str:
        return f"{self.source_id}::{self.post_id}"


@dataclass(frozen=True, slots=True)
class ScoredPost:
    """Post after LLM analysis — includes engagement score and rationale."""

    raw: RawPost
    score: int
    rationale: str


@dataclass(frozen=True, slots=True)
class FinalPost:
    """Post ready to be sent to admin — rewritten text."""

    scored: ScoredPost
    rewritten_text: str
