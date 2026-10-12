"""Registry-driven ingestion (RAG §5–7, §23).

    cd server && venv/Scripts/python -m app.guide.knowledge.ingest            # build + activate
    cd server && venv/Scripts/python -m app.guide.knowledge.ingest --dry-run  # parse/report only

Pipeline: read approved source files -> verify version/digest -> structure-aware segmentation
-> embed -> write a NEW index release -> lineage validation -> atomic activation.

Rules enforced here:
  * Content changed but `version` not bumped => refused as drift (RAG §24 "do not auto-reindex
    a changed page"). Every published change is an explicit new version.
  * Segmentation follows document structure, never fixed-length slicing (RAG §6): one FAQ per
    unit, a pricing/table/procedure section kept whole, prose split on paragraph boundaries.
  * A source file that disappears has its versions retired, so it is ineligible immediately.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import crypto
from ..db import new_session
from ..models import GuideKnowledgeSource, GuideKnowledgeSourceVersion, SOURCE_TYPES
from .embeddings import HASH_PROFILE, document_input, get_embedder
from .models import RagIndexRelease, RagKnowledgeUnit

SOURCES_DIR = Path(__file__).resolve().parent / "sources"
CHUNKING_PROFILE = "structure-v1"
PROSE_TARGET_TOKENS = 450
PROSE_MAX_TOKENS = 800
REVIEW_DAYS = {"HIGH": 90, "MATERIAL": 180, "LOW": 365}
REQUIRED_META = ("source_id", "title", "source_type", "canonical_path", "risk_class",
                 "authority_rank", "version")


class IngestError(RuntimeError):
    pass


@dataclass
class Unit:
    heading_path: str
    unit_type: str
    text: str

    @property
    def tokens(self) -> int:
        return max(1, round(len(self.text.split()) * 1.33))


@dataclass
class SourceDoc:
    meta: dict
    body: str
    path: Path
    units: list[Unit] = field(default_factory=list)

    @property
    def digest(self) -> bytes:
        return hashlib.sha256(self.body.strip().encode("utf-8")).digest()


# ── Parsing ────────────────────────────────────────────────────────────────────────────────

def parse_file(path: Path) -> SourceDoc:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise IngestError(f"{path.name}: missing front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    missing = [k for k in REQUIRED_META if k not in meta]
    if missing:
        raise IngestError(f"{path.name}: front matter missing {missing}")
    if meta["source_type"] not in SOURCE_TYPES:
        raise IngestError(f"{path.name}: unknown source_type {meta['source_type']!r}")
    if meta["risk_class"] not in REVIEW_DAYS:
        raise IngestError(f"{path.name}: unknown risk_class {meta['risk_class']!r}")
    cp = str(meta["canonical_path"])
    if not cp.startswith("/") or cp.startswith("//"):
        raise IngestError(f"{path.name}: canonical_path must be a relative public path")
    meta["version"] = str(meta["version"])
    doc = SourceDoc(meta=meta, body=m.group(2), path=path)
    doc.units = segment(doc)
    if not doc.units:
        raise IngestError(f"{path.name}: no content units")
    return doc


def _section_type(source_type: str, text: str) -> str:
    if source_type == "pricing":
        return "pricing"
    if source_type == "legal":
        return "legal"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if sum(ln.startswith("|") for ln in lines) >= 2:
        return "table"
    if sum(bool(re.match(r"^\d+[.)]\s", ln)) for ln in lines) >= 2:
        return "procedure"
    if "```" in text:
        return "code"
    return "prose"


def _split_prose(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], []
    for p in paras:
        candidate = "\n\n".join(current + [p])
        if current and len(candidate.split()) * 1.33 > PROSE_TARGET_TOKENS:
            chunks.append("\n\n".join(current))
            current = [p]
        else:
            current.append(p)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def segment(doc: SourceDoc) -> list[Unit]:
    title = doc.meta["title"]
    stype = doc.meta["source_type"]
    units: list[Unit] = []
    # The H1 repeats the title (already in every heading_path); drop it so it never becomes
    # a content-free unit. Then split on level-2 headings; text before the first one is the
    # document's overview.
    body = re.sub(r"(?m)^#\s+.+$", "", doc.body)
    parts = re.split(r"(?m)^##\s+(.+)$", body)
    sections = [("Overview", parts[0])] + list(zip(parts[1::2], parts[2::2]))
    for heading, text in sections:
        text = text.strip()
        heading = heading.strip()
        if not text:
            continue
        path = f"{title} > {heading}" if heading != "Overview" else title
        faq = re.split(r"(?m)^###\s+Q:\s*(.+)$", text)
        if len(faq) > 1:
            intro = faq[0].strip()
            if intro:
                units.append(Unit(path, "prose", intro))
            for q, a in zip(faq[1::2], faq[2::2]):
                units.append(Unit(f"{path} > {q.strip()}", "faq",
                                  f"Q: {q.strip()}\nA: {a.strip()}"))
            continue
        # Other level-3 subsections stay with their own heading.
        subs = re.split(r"(?m)^###\s+(.+)$", text)
        blocks = [(path, subs[0])] + [(f"{path} > {h.strip()}", b)
                                      for h, b in zip(subs[1::2], subs[2::2])]
        for bpath, btext in blocks:
            btext = btext.strip()
            if not btext:
                continue
            kind = _section_type(stype, btext)
            if kind == "prose" and len(btext.split()) * 1.33 > PROSE_MAX_TOKENS:
                units.extend(Unit(bpath, "prose", c) for c in _split_prose(btext))
            else:
                # Pricing/legal/table/procedure/code: indivisible so a qualifier never
                # separates from its claim (RAG §6 table).
                units.append(Unit(bpath, kind, btext))
    return units


def load_sources(directory: Path = SOURCES_DIR) -> list[SourceDoc]:
    docs = [parse_file(p) for p in sorted(directory.glob("*.md"))]
    ids = [d.meta["source_id"] for d in docs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise IngestError(f"duplicate source_id(s): {sorted(dupes)}")
    return docs


# ── Registry ───────────────────────────────────────────────────────────────────────────────

def _register(db: Session, doc: SourceDoc, now: datetime) -> GuideKnowledgeSourceVersion:
    m = doc.meta
    src = db.get(GuideKnowledgeSource, m["source_id"])
    if src is None:
        src = GuideKnowledgeSource(source_id=m["source_id"])
        db.add(src)
    src.source_type = m["source_type"]
    src.title = m["title"]
    src.owner = m.get("owner")
    src.authority_rank = int(m["authority_rank"])
    src.risk_class = m["risk_class"]
    src.canonical_path = m["canonical_path"]
    db.flush()

    version = db.scalar(select(GuideKnowledgeSourceVersion).where(
        GuideKnowledgeSourceVersion.source_id == m["source_id"],
        GuideKnowledgeSourceVersion.version == m["version"]))
    if version is not None:
        if version.content_digest != doc.digest:
            raise IngestError(
                f"{doc.path.name}: content changed but version {m['version']!r} was not bumped")
        if version.status in ("retired", "quarantined"):
            raise IngestError(f"{doc.path.name}: version {m['version']!r} is {version.status}")
        return version
    version = GuideKnowledgeSourceVersion(
        source_id=m["source_id"], version=m["version"], status="published",
        content_digest=doc.digest, locale=m.get("locale", "en-US"),
        jurisdiction=m.get("jurisdiction"), public_retrieval=True, effective_at=now,
        published_at=now, review_due_at=now + timedelta(days=REVIEW_DAYS[m["risk_class"]]),
    )
    db.add(version)
    db.flush()
    # The previous published version is superseded the moment the new one exists.
    db.execute(update(GuideKnowledgeSourceVersion).where(
        GuideKnowledgeSourceVersion.source_id == m["source_id"],
        GuideKnowledgeSourceVersion.source_version_id != version.source_version_id,
        GuideKnowledgeSourceVersion.status == "published").values(status="retired",
                                                                  updated_at=now))
    return version


# ── Build & activate ───────────────────────────────────────────────────────────────────────

def build_release(db: Session, docs: list[SourceDoc], *, embedding_profile: str | None = None,
                  activate: bool = True) -> RagIndexRelease:
    now = datetime.now(timezone.utc)
    embedder = get_embedder(embedding_profile)
    versions = {d.meta["source_id"]: _register(db, d, now) for d in docs}

    # Retire sources that are no longer in the approved set.
    db.execute(update(GuideKnowledgeSourceVersion).where(
        GuideKnowledgeSourceVersion.source_id.not_in(list(versions)),
        GuideKnowledgeSourceVersion.status == "published").values(status="retired",
                                                                  updated_at=now))

    build_digest = hashlib.sha256(b"".join(
        d.digest for d in sorted(docs, key=lambda d: d.meta["source_id"]))
        + embedder.profile.encode() + CHUNKING_PROFILE.encode()).digest()
    previous = db.scalar(select(RagIndexRelease).where(RagIndexRelease.status == "active"))
    release = RagIndexRelease(
        # Random suffix: rebuilding identical content (e.g. after an embedding fix) is a NEW
        # release, never a collision with the one it replaces.
        release_id=f"rel-{now:%Y%m%d%H%M%S}-{build_digest.hex()[:8]}-{secrets.token_hex(3)}",
        status="building",
        embedding_profile=embedder.profile, chunking_profile=CHUNKING_PROFILE,
        previous_release_id=previous.release_id if previous else None, build_digest=build_digest,
        manifest={"sources": {d.meta["source_id"]: d.meta["version"] for d in docs}},
    )
    db.add(release)
    db.flush()

    count = 0
    for doc in docs:
        inputs = [document_input(doc.meta["title"], u.heading_path, u.text) for u in doc.units]
        vectors = embedder.embed_documents(inputs)
        for ordinal, (unit, vec) in enumerate(zip(doc.units, vectors)):
            db.add(RagKnowledgeUnit(
                release_id=release.release_id,
                source_version_id=versions[doc.meta["source_id"]].source_version_id,
                source_id=doc.meta["source_id"], ordinal=ordinal,
                heading_path=unit.heading_path[:400], unit_type=unit.unit_type,
                content_text=unit.text, content_digest=crypto.digest(unit.text),
                token_count=unit.tokens, embedding_profile=embedder.profile, embedding=vec))
            count += 1
    release.source_count, release.unit_count = len(docs), count
    db.flush()
    _validate_lineage(db, release)
    release.status = "ready"
    if activate:
        activate_release(db, release.release_id)
    db.commit()
    return release


def _validate_lineage(db: Session, release: RagIndexRelease) -> None:
    bad = db.scalar(
        select(RagKnowledgeUnit.unit_id)
        .join(GuideKnowledgeSourceVersion,
              GuideKnowledgeSourceVersion.source_version_id == RagKnowledgeUnit.source_version_id)
        .where(RagKnowledgeUnit.release_id == release.release_id,
               GuideKnowledgeSourceVersion.status != "published").limit(1))
    if bad is not None:
        raise IngestError("lineage validation failed: unit references an unpublished version")


def activate_release(db: Session, release_id: str) -> None:
    """Atomic switch: one transaction flips the old release out and the new one in; the
    partial unique index guarantees there is never more than one active release."""
    release = db.get(RagIndexRelease, release_id)
    if release is None or release.status not in ("ready", "superseded"):
        raise IngestError(f"release {release_id} is not activatable")
    if release.embedding_profile == HASH_PROFILE and not _testing():
        raise IngestError("refusing to activate a hash-embedding release outside tests")
    db.execute(update(RagIndexRelease).where(RagIndexRelease.status == "active")
               .values(status="superseded"))
    db.flush()
    release.status = "active"
    release.activated_at = datetime.now(timezone.utc)


def _testing() -> bool:
    from ..settings import get_settings
    return get_settings().ENVIRONMENT.strip().lower() == "test"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest approved public Guide knowledge.")
    ap.add_argument("--sources", type=Path, default=SOURCES_DIR)
    ap.add_argument("--dry-run", action="store_true", help="parse and report only")
    ap.add_argument("--no-activate", action="store_true")
    args = ap.parse_args(argv)
    docs = load_sources(args.sources)
    for d in docs:
        kinds = {}
        for u in d.units:
            kinds[u.unit_type] = kinds.get(u.unit_type, 0) + 1
        print(f"{d.meta['source_id']:<32} v{d.meta['version']:<8} {len(d.units):>3} units {kinds}")
    if args.dry_run:
        return 0
    db = new_session()
    try:
        release = build_release(db, docs, activate=not args.no_activate)
    except IngestError as exc:
        db.rollback()
        print(f"INGEST FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(f"release {release.release_id}: {release.unit_count} units from "
          f"{release.source_count} sources, status={release.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
