"""Retrieval Service (ZST-UX-GUIDE-001 §11, §13): versioned operator documentation.

The corpus is a small set of markdown documents (knowledge/docs/*.md), each with front matter
naming its id, title, version and the console page it describes. Documents are split into
sections at "## " headings; a query is answered with the best-matching sections by BM25 over
lowercased word tokens. Lexical retrieval is deliberate: the corpus is small, curated and
versioned, results are reproducible, and every answer cites the document version (§6.3).

Documentation is the ``documentation`` source class: it describes how the product works, never
the current state of a tenant's event.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..sources import Source

DOCS_DIR = Path(__file__).resolve().parent / "docs"
CORPUS_VERSION = "2026.10"

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("""a an and are as at be by can do does for from has have how i if in is it its
me my no not of on or our so that the their them then there these this to was we what when where
which who why will with work works you your""".split())


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


@dataclass(frozen=True)
class Section:
    doc_id: str
    doc_title: str
    heading: str
    text: str
    version: str
    path: str
    api: str | None = None          # API version for developer documentation (§6.15)

    @property
    def id(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.heading.lower()).strip("-")
        return f"doc:{self.doc_id}#{slug}"

    def source(self) -> Source:
        return Source(id=self.id, label=f"{self.doc_title} — {self.heading}",
                      source_class="documentation", owner="ZoikoStream documentation",
                      version=self.version,
                      detail={"path": self.path, **({"api": self.api} if self.api else {})})


def _parse(path: Path) -> list[Section]:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    if raw.startswith("---"):
        head, raw = raw[3:].split("\n---", 1)
        for line in head.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"')
    out: list[Section] = []
    for chunk in re.split(r"^## ", raw, flags=re.M)[1:]:
        heading, _, body = chunk.partition("\n")
        out.append(Section(meta.get("id", path.stem), meta.get("title", path.stem),
                           heading.strip(), body.strip(), meta.get("version", CORPUS_VERSION),
                           meta.get("path", ""), meta.get("api") or None))
    return out


class Index:
    """BM25 over sections (heading words weighted double)."""

    K1, B = 1.4, 0.75

    def __init__(self, sections: list[Section]):
        self.sections = sections
        self.docs = [Counter(tokens(s.heading) * 2 + tokens(s.doc_title) + tokens(s.text))
                     for s in sections]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k: int = 3, min_score: float = 1.0) -> list[tuple[Section, float]]:
        q = set(tokens(query))
        scored = []
        for s, d, length in zip(self.sections, self.docs, self.lengths):
            score = 0.0
            for t in q & d.keys():
                tf = d[t]
                score += self.idf[t] * tf * (self.K1 + 1) / (
                    tf + self.K1 * (1 - self.B + self.B * length / self.avg))
            if score >= min_score:
                scored.append((s, score))
        scored.sort(key=lambda p: -p[1])
        return scored[:k]


@lru_cache
def index() -> Index:
    sections = [s for p in sorted(DOCS_DIR.glob("*.md")) for s in _parse(p)]
    return Index(sections)


def search(query: str, k: int = 3) -> list[Section]:
    return [s for s, _ in index().search(query, k=k)]


def api_version() -> str | None:
    """The platform API version the developer documentation describes (front matter)."""
    return next((s.api for s in index().sections if s.api), None)
