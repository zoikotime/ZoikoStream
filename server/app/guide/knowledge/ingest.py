"""Registry-driven ingestion, index lifecycle and content governance (RAG §5–8, §23–25;
RBAC §5–8, §17).

    cd server && venv/Scripts/python -m app.guide.knowledge.ingest            # build + gate + activate
    cd server && venv/Scripts/python -m app.guide.knowledge.ingest --dry-run  # parse/report only
    python -m app.guide.knowledge.ingest gate                                 # evaluate the active release
    python -m app.guide.knowledge.ingest releases                             # list releases
    python -m app.guide.knowledge.ingest rollback [--to <release_id>] --actor <id>
    python -m app.guide.knowledge.ingest break-glass quarantine --source <id> \\
        --reason-code incident --incident <ref> --actor <id>
    python -m app.guide.knowledge.ingest break-glass disable-retrieval \\
        --reason-code incident --incident <ref> --actor <id>

Pipeline (RAG §23): approval check -> read approved source files -> verify version/digest ->
structure-aware segmentation -> embed -> write a NEW candidate release -> lineage validation ->
smoke tests + deterministic offline evaluation (gate.py) -> atomic activation ONLY on pass.

Rules enforced here:
  * Content changed but `version` not bumped => refused as drift (RAG §24 "do not auto-reindex
    a changed page"). Every published change is an explicit new version.
  * Unapproved / expired / self-approved content is refused (governance.py, RBAC §7-8) and the
    refusal is audited ("content_rejected"); new versions are audited "content_published".
  * Segmentation follows document structure, never fixed-length slicing (RAG §6): one FAQ per
    unit, a pricing/table/procedure section kept whole, prose split on paragraph boundaries.
  * A superseded version is NOT retired: it stays referenced by the retained prior release, so
    a rollback restores that release with its own source versions intact (RAG §25.1). It is
    ineligible in the meantime because eligibility is scoped to the ACTIVE release's units.
    Only a source that leaves the approved set is retired; retired/quarantined versions stay
    ineligible even after a rollback (RAG §34 decision 12).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from .. import crypto
from ..models import GuideKnowledgeSource, GuideKnowledgeSourceVersion, SOURCE_TYPES
from . import gate, governance, retrieval
from .embeddings import HASH_PROFILE, document_input, get_embedder
from .models import RagIndexRelease, RagKnowledgeUnit

SOURCES_DIR = Path(__file__).resolve().parent / "sources"
# v2: units carry topic / intent_tags / locale / jurisdiction / risk / validity metadata and the
# embedding input gained its "Topic:" line (RAG §7-8), so every v1 release is rebuilt.
CHUNKING_PROFILE = "structure-v2"
INDEX_VERSION = "guide-rag-index-2"
PROSE_TARGET_TOKENS = 450
PROSE_MAX_TOKENS = 800
REVIEW_DAYS = {"HIGH": 90, "MATERIAL": 180, "LOW": 365}
REQUIRED_META = ("source_id", "title", "source_type", "canonical_path", "risk_class",
                 "authority_rank", "version")
SERVER_DIR = Path(__file__).resolve().parents[3]


class IngestError(RuntimeError):
    pass


@dataclass
class Unit:
    heading_path: str
    unit_type: str
    text: str
    topic: str | None = None

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

    @property
    def index_meta(self) -> dict:
        """Front-matter fields that shape the index (not the approval evidence)."""
        m = self.meta
        return {k: str(m.get(k)) for k in ("locale", "jurisdiction", "topic", "intent_tags",
                                           "topics", "valid_from", "valid_to", "risk_class",
                                           "authority_rank", "source_type")}


# ── Parsing ────────────────────────────────────────────────────────────────────────────────

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:96] or "general"


def _dt(value, name: str, path: Path) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise IngestError(f"{path.name}: {name} is not an ISO date") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_file(path: Path) -> SourceDoc:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise IngestError(f"{path.name}: missing front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    missing = [k for k in REQUIRED_META if k not in meta]
    if missing:
        raise IngestError(f"{path.name}: front matter missing {missing}")
    if meta["source_type"] not in SOURCE_TYPES or meta["source_type"] not in \
            retrieval.SOURCE_CLASS:
        raise IngestError(f"{path.name}: unknown source_type {meta['source_type']!r}")
    if meta["risk_class"] not in REVIEW_DAYS:
        raise IngestError(f"{path.name}: unknown risk_class {meta['risk_class']!r}")
    cp = str(meta["canonical_path"])
    if not cp.startswith("/") or cp.startswith("//"):
        raise IngestError(f"{path.name}: canonical_path must be a relative public path")
    meta["version"] = str(meta["version"])
    meta.setdefault("locale", "en-US")
    for key in ("valid_from", "valid_to"):
        meta[key] = _dt(meta.get(key), key, path)
    tags = meta.get("intent_tags")
    if tags is not None and (not isinstance(tags, list)
                             or not set(tags) <= set(retrieval.INTENT_CLASSES)):
        raise IngestError(f"{path.name}: intent_tags must be a list of known intents")
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
    topics = doc.meta.get("topics") or {}
    doc_topic = doc.meta.get("topic") or _slug(title)
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
        # RAG §7 topic: an explicit `topics:` mapping wins, else the section heading.
        topic = topics.get(heading) or (doc_topic if heading == "Overview" else _slug(heading))
        path = f"{title} > {heading}" if heading != "Overview" else title
        faq = re.split(r"(?m)^###\s+Q:\s*(.+)$", text)
        if len(faq) > 1:
            intro = faq[0].strip()
            if intro:
                units.append(Unit(path, "prose", intro, topic))
            for q, a in zip(faq[1::2], faq[2::2]):
                units.append(Unit(f"{path} > {q.strip()}", "faq",
                                  f"Q: {q.strip()}\nA: {a.strip()}", topic))
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
                units.extend(Unit(bpath, "prose", c, topic) for c in _split_prose(btext))
            else:
                # Pricing/legal/table/procedure/code: indivisible so a qualifier never
                # separates from its claim (RAG §6 table).
                units.append(Unit(bpath, kind, btext, topic))
    return units


def load_sources(directory: Path = SOURCES_DIR) -> list[SourceDoc]:
    docs = [parse_file(p) for p in sorted(directory.glob("*.md"))]
    ids = [d.meta["source_id"] for d in docs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise IngestError(f"duplicate source_id(s): {sorted(dupes)}")
    return docs


def intent_tags(doc: SourceDoc) -> list[str]:
    """Intents whose RAG §10.1 allowlist admits this source (routing metadata, RAG §7)."""
    explicit = doc.meta.get("intent_tags")
    if explicit:
        return list(explicit)
    stype = doc.meta["source_type"]
    return [i for i, types in retrieval.INTENT_SOURCES.items() if stype in types]


# ── Governance ─────────────────────────────────────────────────────────────────────────────

def check_governance(docs: list[SourceDoc], *, publisher: str | None = None,
                     now: datetime | None = None) -> tuple[dict, list[str]]:
    """Approval checks for every source. Raises IngestError (after auditing each rejected
    source as "content_rejected") when any source may not be indexed. Returns the per-source
    approval snapshot for the manifest, and warnings (pending approvals outside production)."""
    now = now or datetime.now(timezone.utc)
    checks = {d.meta["source_id"]: governance.check_approval(
        d.meta, review_days=REVIEW_DAYS[d.meta["risk_class"]], now=now, publisher=publisher)
        for d in docs}
    try:
        warnings = governance.enforce(list(checks.values()))
    except governance.GovernanceError as exc:
        from ..settings import get_settings
        s = get_settings()
        refuse_pending = s.is_production() or not s.GUIDE_KB_ALLOW_PENDING_APPROVAL
        for c in checks.values():
            if c.problems or (c.pending and refuse_pending):
                governance.audit_event("content_rejected", source_id=c.source_id,
                                       version=c.version,
                                       reason="sod_or_metadata" if c.problems
                                       else "approval_pending_or_expired")
        raise IngestError(str(exc)) from None
    snapshot = {d.meta["source_id"]: governance.approval_snapshot(d.meta,
                                                                  checks[d.meta["source_id"]])
                for d in docs}
    return snapshot, warnings


# ── Registry ───────────────────────────────────────────────────────────────────────────────

def _register(db: Session, doc: SourceDoc, now: datetime
              ) -> tuple[GuideKnowledgeSourceVersion, bool]:
    m = doc.meta
    src = db.get(GuideKnowledgeSource, m["source_id"])
    if src is None:
        src = GuideKnowledgeSource(source_id=m["source_id"])
        db.add(src)
    src.source_type = m["source_type"]
    src.title = m["title"]
    src.owner = m.get("owner_role") or m.get("owner")
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
        return version, False
    version = GuideKnowledgeSourceVersion(
        source_id=m["source_id"], version=m["version"], status="published",
        content_digest=doc.digest, locale=m.get("locale", "en-US"),
        jurisdiction=m.get("jurisdiction"), public_retrieval=True,
        # Database time, not process time: the candidate is evaluated inside this same
        # transaction, where now() is the transaction start, so a process-clock timestamp
        # could make a just-registered version "not yet effective" for its own gate.
        effective_at=m.get("valid_from") or func.now(), expires_at=m.get("valid_to"),
        published_at=now, review_due_at=now + timedelta(days=REVIEW_DAYS[m["risk_class"]]),
    )
    db.add(version)
    db.flush()
    # The previous version is deliberately NOT retired here: it remains the lineage of the
    # retained prior release (rollback target). It stops being eligible the moment the new
    # release is activated, because retrieval only searches the active release's units.
    return version, True


# ── Manifest helpers ───────────────────────────────────────────────────────────────────────

def application_commit() -> str:
    """EVAL §2.1 application_build: GUIDE_APP_COMMIT (set by CI/deploy), else git, else
    "unknown" — never None in a manifest."""
    from ..settings import get_settings
    configured = (get_settings().GUIDE_APP_COMMIT or os.environ.get("GITHUB_SHA", "")).strip()
    if configured:
        return configured
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10, check=False, cwd=SERVER_DIR)
        sha = out.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{7,40}", sha):
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _file_version(path: Path, prefix: str) -> str:
    try:
        return f"{prefix}-{hashlib.sha256(path.read_bytes()).hexdigest()[:12]}"
    except OSError:
        return f"{prefix}-unknown"


def _destination_version(db: Session) -> str:
    try:
        rows = db.execute(text(
            "SELECT destination_id, label, action_type, href_kind, coalesce(href_value, ''), "
            "enabled FROM guide_destination_registry ORDER BY destination_id")).all()
    except Exception:  # noqa: BLE001 — a manifest field must never fail an ingest
        return "destinations-unknown"
    blob = "\n".join("|".join(str(c) for c in r) for r in rows)
    return f"destinations-{hashlib.sha256(blob.encode()).hexdigest()[:12]}"


def runtime_versions(db: Session) -> dict:
    """Prompt / policy / flow / destination / retention versions in force at build time."""
    from .. import retention
    out = {"retention_policy_version": retention.POLICY_VERSION,
           "conversation_flow_version": _file_version(
               Path(__file__).resolve().parents[1] / "support" / "flows.py", "flows"),
           "destination_registry_version": _destination_version(db),
           "application_commit": application_commit()}
    try:
        from .. import prompts
        out["system_prompt_version"] = prompts.active_prompt(db).version
        out["policy_version"] = prompts.active_policy(db).version
    except Exception:  # noqa: BLE001
        out["system_prompt_version"] = out["policy_version"] = "unknown"
    return out


# ── Build & activate ───────────────────────────────────────────────────────────────────────

def release_digest(docs: list[SourceDoc], embedding_profile: str) -> bytes:
    """Fingerprint of an index build: every source's content and index-shaping metadata, the
    embedding profile and the chunking profile. Identical inputs give an identical digest."""
    h = hashlib.sha256()
    for d in sorted(docs, key=lambda d: d.meta["source_id"]):
        h.update(d.digest)
        h.update(repr(sorted(d.index_meta.items())).encode())
    h.update(embedding_profile.encode() + CHUNKING_PROFILE.encode())
    return h.digest()


def is_current(db: Session, docs: list[SourceDoc], embedding_profile: str) -> bool:
    """True when the active release was built from exactly these sources and profiles, so a
    deploy can skip re-indexing unchanged knowledge."""
    active = db.scalar(select(RagIndexRelease).where(RagIndexRelease.status == "active"))
    return bool(active and active.embedding_profile == embedding_profile
                and active.build_digest == release_digest(docs, embedding_profile))


def build_release(db: Session, docs: list[SourceDoc], *, embedding_profile: str | None = None,
                  activate: bool = True, approved_by: str | None = None,
                  publisher: str | None = None) -> RagIndexRelease:
    """Build a candidate release; with activate=True run the activation gate and activate
    only on pass. A failed gate leaves the release recorded as 'failed' (with its evaluation
    in the manifest) and raises IngestError; the previously active release keeps serving."""
    now = datetime.now(timezone.utc)
    approvals, warnings = check_governance(docs, publisher=publisher, now=now)
    embedder = get_embedder(embedding_profile)
    registered = {d.meta["source_id"]: _register(db, d, now) for d in docs}
    versions = {sid: v for sid, (v, _new) in registered.items()}

    # Retire sources that are no longer in the approved set.
    db.execute(update(GuideKnowledgeSourceVersion).where(
        GuideKnowledgeSourceVersion.source_id.not_in(list(versions)),
        GuideKnowledgeSourceVersion.status == "published").values(status="retired",
                                                                  updated_at=now))

    build_digest = release_digest(docs, embedder.profile)
    previous = db.scalar(select(RagIndexRelease).where(RagIndexRelease.status == "active"))
    release_id = f"rel-{now:%Y%m%d%H%M%S}-{build_digest.hex()[:8]}-{secrets.token_hex(3)}"
    fusion = retrieval.fusion_profile()
    release = RagIndexRelease(
        # Random suffix: rebuilding identical content (e.g. after an embedding fix) is a NEW
        # release, never a collision with the one it replaces.
        release_id=release_id, status="building",
        embedding_profile=embedder.profile, chunking_profile=CHUNKING_PROFILE,
        previous_release_id=previous.release_id if previous else None, build_digest=build_digest,
        fusion_profile=fusion, reranker_profile=retrieval.RERANKER_PROFILE,
        answerability_profile=retrieval.ANSWERABILITY_PROFILE,
        manifest={
            # RAG Appendix B / EVAL §2.1 release manifest.
            "rag_release_id": release_id,
            "index_version": INDEX_VERSION,
            "sources": {d.meta["source_id"]: d.meta["version"] for d in docs},
            "source_registry_snapshot": {
                sid: {**approvals[sid], "source_version_id": str(v.source_version_id),
                      "content_digest": v.content_digest.hex()}
                for sid, v in versions.items()},
            "embedding_profile": embedder.profile,
            "chunking_profile": CHUNKING_PROFILE,
            "lexical_profile": retrieval.LEXICAL_PROFILE,
            "fusion_profile": fusion,
            "reranker_profile": retrieval.RERANKER_PROFILE,
            "answerability_policy_version": retrieval.ANSWERABILITY_PROFILE,
            "build_digest": build_digest.hex(),
            "previous_release_id": previous.release_id if previous else None,
            "approval_warnings": warnings,
            **runtime_versions(db),
        },
    )
    db.add(release)
    db.flush()

    count = 0
    for doc in docs:
        m = doc.meta
        tags = intent_tags(doc)
        inputs = [document_input(m["title"], u.heading_path, u.text, u.topic) for u in doc.units]
        vectors = embedder.embed_documents(inputs)
        for ordinal, (unit, vec) in enumerate(zip(doc.units, vectors)):
            db.add(RagKnowledgeUnit(
                release_id=release.release_id,
                source_version_id=versions[m["source_id"]].source_version_id,
                source_id=m["source_id"], ordinal=ordinal,
                heading_path=unit.heading_path[:400], unit_type=unit.unit_type,
                topic=(unit.topic or "")[:96] or None, intent_tags=tags,
                locale=m.get("locale", "en-US"), jurisdiction=m.get("jurisdiction"),
                risk_class=m["risk_class"], valid_from=m.get("valid_from"),
                valid_to=m.get("valid_to"),
                content_text=unit.text, content_digest=crypto.digest(unit.text),
                token_count=unit.tokens, embedding_profile=embedder.profile, embedding=vec))
            count += 1
    release.source_count, release.unit_count = len(docs), count
    release.manifest = {**release.manifest, "active_source_count": len(docs),
                        "active_unit_count": count}
    db.flush()
    _validate_lineage(db, release)
    release.status = "ready"
    from ..sessions import audit
    for sid, (v, new) in registered.items():
        if new:  # RBAC §22 auth.content.published: actor + source/version + approval evidence
            audit(db, None, "content_published", source_id=sid, version=v.version,
                  source_version_id=str(v.source_version_id), release_id=release_id,
                  approval_state=approvals[sid]["approval_state"],
                  approved_by=approvals[sid]["approved_by"])
    # Smoke tests + offline evaluation of the candidate, always — a --no-activate build is
    # still recorded with the evidence a later activation needs (RAG §23).
    result = gate.evaluate(db, release.release_id)
    record_evaluation(release, result)
    if not result.passed:
        release.status = "failed"
        db.commit()
        raise IngestError(f"activation gate failed for {release.release_id}: "
                          + "; ".join(f for f in result.failures
                                      if not f.startswith("answerability:"))[:2000])
    if activate:
        activate_release(db, release.release_id, approved_by=approved_by)
    db.commit()
    return release


def record_evaluation(release: RagIndexRelease, result: gate.GateResult) -> None:
    release.evaluation_run_id = result.evaluation_run_id
    release.manifest = {**release.manifest, "evaluation_run_id": result.evaluation_run_id,
                        "evaluation": result.summary()}


def _validate_lineage(db: Session, release: RagIndexRelease) -> None:
    bad = db.scalar(
        select(RagKnowledgeUnit.unit_id)
        .join(GuideKnowledgeSourceVersion,
              GuideKnowledgeSourceVersion.source_version_id == RagKnowledgeUnit.source_version_id)
        .where(RagKnowledgeUnit.release_id == release.release_id,
               GuideKnowledgeSourceVersion.status != "published").limit(1))
    if bad is not None:
        raise IngestError("lineage validation failed: unit references an unpublished version")


def _default_approver() -> str:
    return (os.environ.get("GUIDE_KB_PUBLISHER") or os.environ.get("GITHUB_ACTOR")
            or "deploy-pipeline")


def activate_release(db: Session, release_id: str, *, approved_by: str | None = None,
                     rollback: bool = False) -> None:
    """Atomic switch: one transaction flips the old release out and the new one in; the
    partial unique index guarantees there is never more than one active release.

    A NEW release ('ready') activates only with a passing evaluation recorded in its manifest
    (RAG §23, RAG-AT-21). A rollback re-activates a release that was active before
    ('superseded' with activated_at) — it passed its gate when it was first promoted."""
    release = db.get(RagIndexRelease, release_id)
    if release is None or release.status not in ("ready", "superseded"):
        raise IngestError(f"release {release_id} is not activatable")
    if release.embedding_profile == HASH_PROFILE and not _testing():
        raise IngestError("refusing to activate a hash-embedding release outside tests")
    evaluation = (release.manifest or {}).get("evaluation") or {}
    if release.status == "ready" and not evaluation.get("passed"):
        raise IngestError(f"release {release_id} has no passing evaluation; run the gate")
    if release.status == "superseded" and release.activated_at is None:
        raise IngestError(f"release {release_id} was never active; build it through the gate")
    previous = db.scalar(select(RagIndexRelease).where(RagIndexRelease.status == "active"))
    db.execute(update(RagIndexRelease).where(RagIndexRelease.status == "active")
               .values(status="superseded"))
    db.flush()
    release.status = "active"
    release.activated_at = datetime.now(timezone.utc)
    release.approved_by = (approved_by or _default_approver())[:96]
    release.manifest = {**release.manifest, "approved_by": release.approved_by,
                        "activated_at": release.activated_at.isoformat(timespec="seconds")}
    from ..sessions import audit
    audit(db, None, "index_rolled_back" if rollback else "index_activated",
          release_id=release_id, previous_release_id=previous.release_id if previous else None,
          evaluation_run_id=release.evaluation_run_id, approved_by=release.approved_by,
          embedding_profile=release.embedding_profile, fusion_profile=release.fusion_profile)


def rollback(db: Session, *, to: str | None = None, actor: str | None = None) -> RagIndexRelease:
    """Re-activate a prior known-good release (RAG §25.1, RAG-AT-24). Its own source versions
    are intact (superseding never retires them); any version since quarantined or retired
    stays ineligible, because eligibility is re-checked on every query."""
    current = db.scalar(select(RagIndexRelease).where(RagIndexRelease.status == "active"))
    if to is None:
        q = select(RagIndexRelease).where(RagIndexRelease.status == "superseded",
                                          RagIndexRelease.activated_at.is_not(None))
        if current is not None:
            q = q.where(RagIndexRelease.release_id != current.release_id)
        target = db.scalar(q.order_by(RagIndexRelease.activated_at.desc()).limit(1))
        if target is None:
            raise IngestError("no prior release to roll back to")
    else:
        target = db.get(RagIndexRelease, to)
        if target is None:
            raise IngestError(f"release {to} does not exist")
    activate_release(db, target.release_id, approved_by=actor, rollback=True)
    db.commit()
    return target


def excluded_sources(db: Session, release_id: str) -> list[str]:
    """Sources in a release whose version is no longer eligible (quarantined/retired)."""
    return list(db.scalars(text(
        "SELECT DISTINCT u.source_id FROM guide_rag.rag_knowledge_units u "
        "JOIN guide_knowledge_source_versions v ON v.source_version_id = u.source_version_id "
        "WHERE u.release_id = :r AND v.status <> 'published' ORDER BY 1"), {"r": release_id}))


def _break_glass_args(reason_code: str, incident: str, actor: str) -> None:
    if reason_code not in governance.BREAK_GLASS_REASONS:
        raise IngestError(f"--reason-code must be one of {governance.BREAK_GLASS_REASONS}")
    if not re.fullmatch(r"[A-Za-z0-9_.:\-]{3,64}", incident or ""):
        raise IngestError("--incident must be an incident/ticket reference (3-64 chars)")
    if not (actor or "").strip():
        raise IngestError("--actor is required")


def break_glass_quarantine(db: Session, source_id: str, *, reason_code: str, incident: str,
                           actor: str) -> int:
    """Emergency quarantine (RBAC §17, RAG §24): every version of the source becomes ineligible
    immediately, in every release, including any later rollback target."""
    _break_glass_args(reason_code, incident, actor)
    n = db.execute(update(GuideKnowledgeSourceVersion).where(
        GuideKnowledgeSourceVersion.source_id == source_id,
        GuideKnowledgeSourceVersion.status.in_(("published", "draft"))).values(
        status="quarantined", updated_at=datetime.now(timezone.utc))).rowcount
    if not n:
        raise IngestError(f"no eligible versions of {source_id!r} to quarantine")
    from ..sessions import audit
    audit(db, None, "break_glass", action="quarantine_source", source_id=source_id,
          versions=n, reason_code=reason_code, incident_ref=incident, actor=actor)
    db.commit()
    return n


def break_glass_disable_retrieval(db: Session, *, reason_code: str, incident: str,
                                  actor: str) -> str | None:
    """Emergency stop for static retrieval: the active release is stood down, so every
    retrieval returns NO_SOURCE and the Guide answers with gaps / routes — never facts from
    model memory (RAG-AT-30). Restore with `rollback --to <release_id>`."""
    _break_glass_args(reason_code, incident, actor)
    active = db.scalar(select(RagIndexRelease).where(RagIndexRelease.status == "active"))
    if active is None:
        raise IngestError("retrieval is already disabled (no active release)")
    active.status = "superseded"
    from ..sessions import audit
    audit(db, None, "break_glass", action="disable_retrieval", release_id=active.release_id,
          reason_code=reason_code, incident_ref=incident, actor=actor)
    db.commit()
    return active.release_id


def _testing() -> bool:
    from ..settings import get_settings
    return get_settings().ENVIRONMENT.strip().lower() == "test"


# ── CLI ────────────────────────────────────────────────────────────────────────────────────

def _cli_session() -> Session:
    """The ingest principal when configured (RBAC §20 least privilege), else the Guide DB."""
    from ..settings import get_settings
    url = get_settings().GUIDE_INGEST_DATABASE_URL
    if url:
        from sqlalchemy import create_engine
        from ..db import engine_url
        engine = create_engine(engine_url(url), pool_pre_ping=True)
        return Session(bind=engine, autoflush=False, expire_on_commit=False)
    from ..db import new_session
    return new_session()


def _print_gate(result: gate.GateResult) -> None:
    print(f"evaluation {result.evaluation_run_id}: {'PASS' if result.passed else 'FAIL'} "
          f"(gate set: {result.gate_set})")
    for k, v in sorted(result.metrics.items()):
        t = gate.THRESHOLDS.get(k)
        print(f"  {k:<24} {v!s:>8}" + (f"  >= {t}" if t is not None else ""))
    for f in result.failures[:40]:
        print(f"  - {f}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = ("build", "gate", "releases", "rollback", "break-glass")
    if not argv or argv[0] not in commands:
        argv = ["build", *argv]
    ap = argparse.ArgumentParser(prog="python -m app.guide.knowledge.ingest",
                                 description="Approved public Guide knowledge: index lifecycle.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a release, gate it, activate on pass")
    b.add_argument("--sources", type=Path, default=SOURCES_DIR)
    b.add_argument("--dry-run", action="store_true", help="parse, check approvals, report only")
    b.add_argument("--no-activate", action="store_true")
    b.add_argument("--approved-by", help="release approver recorded in the manifest")
    b.add_argument("--publisher", help="publishing identity (separation-of-duties check)")
    sub.add_parser("gate", help="evaluate the active release (no change)")
    sub.add_parser("releases", help="list releases")
    r = sub.add_parser("rollback", help="re-activate a prior known-good release")
    r.add_argument("--to", dest="to")
    r.add_argument("--actor", required=True)
    bg = sub.add_parser("break-glass", help="emergency quarantine / retrieval stop (audited)")
    bg.add_argument("action", choices=["quarantine", "disable-retrieval"])
    bg.add_argument("--source")
    bg.add_argument("--reason-code", required=True, choices=governance.BREAK_GLASS_REASONS)
    bg.add_argument("--incident", required=True)
    bg.add_argument("--actor", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "build":
        try:
            docs = load_sources(args.sources)
        except IngestError as exc:
            print(f"INGEST FAILED: {exc}", file=sys.stderr)
            return 1
        for d in docs:
            kinds: dict[str, int] = {}
            for u in d.units:
                kinds[u.unit_type] = kinds.get(u.unit_type, 0) + 1
            print(f"{d.meta['source_id']:<32} v{d.meta['version']:<8} {len(d.units):>3} units "
                  f"{kinds} approval={d.meta.get('approved_by') or governance.PENDING}")
        if args.dry_run:
            try:
                _snap, warnings = check_governance(docs, publisher=args.publisher)
            except IngestError as exc:
                print(f"APPROVAL REFUSED: {exc}", file=sys.stderr)
                return 1
            for w in warnings:
                print(f"WARNING: {w}")
            return 0
    db = _cli_session()
    try:
        if args.cmd == "build":
            release = build_release(db, docs, activate=not args.no_activate,
                                    approved_by=args.approved_by, publisher=args.publisher)
            ev = (release.manifest or {}).get("evaluation")
            print(f"release {release.release_id}: {release.unit_count} units from "
                  f"{release.source_count} sources, status={release.status}, "
                  f"evaluation={release.evaluation_run_id}")
            for w in release.manifest.get("approval_warnings", []):
                print(f"WARNING: {w}")
            if ev:
                print(f"gate: {'PASS' if ev['passed'] else 'FAIL'} {ev['gate_set']}")
        elif args.cmd == "gate":
            active = retrieval.active_release(db)
            if active is None:
                print("no active release", file=sys.stderr)
                return 2
            result = gate.evaluate(db, active[0])
            db.rollback()
            _print_gate(result)
            return 0 if result.passed else 1
        elif args.cmd == "releases":
            for rel in db.scalars(select(RagIndexRelease)
                                  .order_by(RagIndexRelease.created_at.desc()).limit(20)):
                print(f"{rel.release_id:<48} {rel.status:<10} units={rel.unit_count:<4} "
                      f"eval={rel.evaluation_run_id} activated={rel.activated_at}")
        elif args.cmd == "rollback":
            target = rollback(db, to=args.to, actor=args.actor)
            excluded = excluded_sources(db, target.release_id)
            print(f"rolled back to {target.release_id}"
                  + (f"; still ineligible: {excluded}" if excluded else ""))
        elif args.cmd == "break-glass":
            if args.action == "quarantine":
                if not args.source:
                    print("--source is required", file=sys.stderr)
                    return 2
                n = break_glass_quarantine(db, args.source, reason_code=args.reason_code,
                                           incident=args.incident, actor=args.actor)
                print(f"quarantined {n} version(s) of {args.source}")
            else:
                rid = break_glass_disable_retrieval(db, reason_code=args.reason_code,
                                                    incident=args.incident, actor=args.actor)
                print(f"retrieval disabled (stood down {rid}); restore: rollback --to {rid}")
    except IngestError as exc:
        db.rollback()
        print(f"INGEST FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
