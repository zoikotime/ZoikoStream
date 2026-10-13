"""Provenance and freshness (ZST-UX-GUIDE-001 §6.3, §15).

Every fact Guide shows is attached to a Source naming its class, its owning service and when
it was last true. Source classes are fixed (§6.3): authoritative state, operational
telemetry, documentation, tenant content, and AI inference.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

SOURCE_CLASSES = ("authoritative", "operational", "documentation", "tenant", "inference")


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    dt = aware(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def age_text(seconds: float | None) -> str:
    if seconds is None:
        return "time unknown"
    s = int(seconds)
    if s < 60:
        return f"{s} sec ago"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


@dataclass
class Source:
    id: str
    label: str
    source_class: str                 # one of SOURCE_CLASSES
    owner: str                        # owning platform service
    updated_at: datetime | None = None
    stale_after: int | None = None    # seconds; None = not freshness-sensitive
    version: str | None = None
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.source_class not in SOURCE_CLASSES:
            raise ValueError(f"unknown source class {self.source_class!r}")

    @property
    def age_seconds(self) -> float | None:
        ts = aware(self.updated_at)
        return None if ts is None else max(0.0, (now() - ts).total_seconds())

    @property
    def stale(self) -> bool:
        age = self.age_seconds
        return bool(self.stale_after is not None and (age is None or age > self.stale_after))

    def public(self) -> dict:
        out = asdict(self)
        out["class"] = out.pop("source_class")
        out["updated_at"] = iso(self.updated_at)
        out["age_seconds"] = None if self.age_seconds is None else int(self.age_seconds)
        out["age_text"] = age_text(self.age_seconds) if self.updated_at else None
        out["stale"] = self.stale
        out.pop("stale_after")
        return out
