"""Media-library policy, transcripts and derived insights.

Three concerns, deliberately in one module because they all answer questions ABOUT a recording
without owning its persistence (that is crud/media.py):

  * who may watch it, who may download it, and for how long  — `can_view`, `resolve_download`
  * what was said                                            — transcript parse / serialise / search
  * what happened                                            — `derive_insights`

On the third: this stack has no ASR engine and no LLM credential, and inventing either would be
the worst possible thing to do in a compliance-facing artefact. So the AI surface is split by
PROVENANCE, and every field says which side it is on:

  * DERIVED — computed from real rows this platform already stores. Chapters come from poll
    launches, announcements and stage changes with real timestamps. Highlights come from genuine
    engagement spikes in analytics_snapshots. Keywords are term frequency over real chat and Q&A.
    Decisions come from closed polls, which ARE recorded decisions with recorded vote counts.
  * UNAVAILABLE — needs a model that is not configured. A narrative summary, free-text action
    items and text sentiment fall here. They return `{"available": False, "reason": ...}` and the
    UI prints the reason. Wiring a provider later fills them in without touching anything else.

`AI_PROVIDER` is the seam. It is None; when something implements `summarise(context) -> dict`,
register it and the unavailable fields become available. Nothing else changes.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..models import Event, LiveRecording, Organization, User
from ..security import verify_password
from . import storage, viewer as viewer_svc

log = logging.getLogger(__name__)

# ── organization policy ───────────────────────────────────────────────────────
# Defaults live HERE, never in the organizations.media column: an org row written before a toggle
# existed must resolve to the current default rather than a missing key. `policy_for` merges.
DEFAULT_MEDIA_POLICY = {
    # downloads: allowed | disabled | password
    "downloads": "allowed",
    # Minimum platform role that may download at all. Watching is governed by visibility; taking a
    # copy away is a separate, stricter decision — which is why it is a separate setting.
    "download_min_role": "host",
    "download_expiry_days": 0,          # 0 = links never stop being issuable
    "link_ttl_minutes": 15,             # lifetime of one signed URL
    "watermark": False,
    # Retention. 0 = "never" for each, and they are applied weakest-first by `retention_plan` so
    # an archive threshold can never be skipped by a delete threshold.
    "auto_archive_days": 0,
    "cold_storage_days": 0,
    "auto_delete_days": 0,
    "recycle_bin_days": 30,             # how long a soft-deleted recording is restorable
    # New events inherit this as their auto_start_recording default.
    "auto_record": False,
    "recording_quality": "1080p",
    # Storage alert threshold, percent of quota.
    "alert_storage_percent": 85,
}

_POLICY_CHOICES = {
    "downloads": ("allowed", "disabled", "password"),
    "download_min_role": ("viewer", "speaker", "moderator", "host", "org_admin"),
    "recording_quality": ("720p", "1080p", "2k", "4k"),
}
_POLICY_BOOLS = ("watermark", "auto_record")
# (minimum, maximum) per numeric key. Bounded rather than free: `link_ttl_minutes = 525600` is a
# permanent public URL wearing a signature, and `recycle_bin_days = 0` would make Delete
# unrecoverable while still calling itself a recycle bin.
_POLICY_RANGES = {
    "download_expiry_days": (0, 3650),
    "link_ttl_minutes": (1, 1440),
    "auto_archive_days": (0, 3650),
    "cold_storage_days": (0, 3650),
    "auto_delete_days": (0, 3650),
    "recycle_bin_days": (1, 365),
    "alert_storage_percent": (50, 100),
}


def policy_for(org: Organization | None) -> dict:
    """The effective media policy: defaults, overlaid with whatever the org has set."""
    return {**DEFAULT_MEDIA_POLICY, **((org.media if org else None) or {})}


def clean_policy(patch: dict) -> tuple[dict, list[str]]:
    """Whitelist and coerce a policy patch. Returns (accepted, rejected_keys).

    An unknown key is REJECTED and reported rather than stored: a typo that silently persists
    reads back as a saved setting that does nothing, which is worse than a visible refusal.
    """
    out, rejected = {}, []
    for key, value in (patch or {}).items():
        if key in _POLICY_CHOICES:
            if value in _POLICY_CHOICES[key]:
                out[key] = value
            else:
                rejected.append(key)
        elif key in _POLICY_BOOLS:
            out[key] = bool(value)
        elif key in _POLICY_RANGES:
            low, high = _POLICY_RANGES[key]
            try:
                out[key] = max(low, min(int(value), high))
            except (TypeError, ValueError):
                rejected.append(key)
        else:
            rejected.append(key)
    return out, rejected


def retention_plan(policy: dict) -> list[tuple[str, int]]:
    """[(action, age_days)] for the sweep, weakest action first and only for enabled thresholds.

    Order is the guarantee: with archive at 90 and delete at 365, a recording that is 400 days old
    must be archived on the way past rather than jumping straight to the bin having never appeared
    in the archive. Callers apply these in sequence to the same row.
    """
    plan = [(action, int(policy.get(f"{key}_days") or 0))
            for action, key in (("archive", "auto_archive"), ("cold", "cold_storage"),
                                ("delete", "auto_delete"))]
    return [(a, d) for a, d in plan if d > 0]


# ── visibility ────────────────────────────────────────────────────────────────

_ROLE_RANK = {"viewer": 0, "speaker": 1, "moderator": 2, "host": 3, "org_admin": 4, "super_admin": 5}


def is_org_staff(rec: LiveRecording, user: User) -> bool:
    """A member of the ORGANIZING org, or a platform admin. The one check that says "this is our
    own recording" — everything else is audience-side."""
    return user.role == "super_admin" or (bool(user.org_id) and user.org_id == rec.org_id)


def can_manage(rec: LiveRecording, user: User) -> bool:
    """May rename, move, tag, archive or delete. Host and above inside the owning org.

    A MODERATOR deliberately cannot: moderation is authority over a live room, not over the
    organization's media assets, and the brief's own permission table puts "delete recording" and
    "modify event" outside a moderator's remit. A moderator still watches and (policy permitting)
    downloads.
    """
    return is_org_staff(rec, user) and _ROLE_RANK.get(user.role, -1) >= _ROLE_RANK["host"]


def can_view(rec: LiveRecording, event: Event | None, user: User) -> tuple[bool, str | None]:
    """(allowed, reason). Reason is for the caller's 403/404 message, never shown to outsiders.

    Deleted rows are invisible to everyone except staff, who need the recycle bin. Archived is NOT
    hidden — archiving is a shelf, not a permission.
    """
    staff = is_org_staff(rec, user)
    if rec.deleted_at is not None and not staff:
        return False, "This recording is not available"
    if staff:
        # `private` inside the org means the creator and management only — an org_admin can always
        # see it (they answer for the data), and so can whoever recorded it.
        if rec.visibility == "private" and not (
            user.role in ("org_admin", "super_admin") or rec.created_by == user.id
        ):
            return False, "This recording is private"
        return True, None

    # Outside the organizing org. `organization` and `private` stop here.
    if rec.visibility not in ("event_audience", "public"):
        return False, "This recording is not available"
    if rec.status != "stopped" or not rec.storage_key:
        return False, "This recording is not available"
    if event is None:
        return False, "This recording is not available"
    # The organizer's replay switch is the audience-facing gate and it wins over visibility: an
    # event with replay off has no public replay, whatever the recording is labelled.
    if not event.replay_enabled:
        return False, "Replay is not enabled for this event"
    if rec.visibility == "public":
        return True, None
    allowed, _basis, _reason = viewer_svc.access_for(event, user)
    return (True, None) if allowed else (False, "This recording is not available")


# ── downloads ─────────────────────────────────────────────────────────────────

class DownloadDecision:
    """Outcome of a download request. Carries the URL or the reason, never both."""

    __slots__ = ("url", "error", "needs_password", "watermarked", "expires_in")

    def __init__(self, *, url=None, error=None, needs_password=False,
                 watermarked=False, expires_in=0):
        self.url, self.error = url, error
        self.needs_password, self.watermarked = needs_password, watermarked
        self.expires_in = expires_in

    def as_dict(self) -> dict:
        return {"url": self.url, "error": self.error, "needs_password": self.needs_password,
                "watermarked": self.watermarked, "expires_in": self.expires_in}


def download_rules(rec: LiveRecording, policy: dict) -> dict:
    """The rules in force for this recording: org policy, with the row's overrides applied.

    A per-recording override exists so one sensitive session can be locked down without changing
    the setting for the whole organization — the common real-world need, and the reason
    `download_policy` on the row is nullable rather than defaulted.
    """
    mode = rec.download_policy or policy["downloads"]
    if mode == "password" and not rec.download_password_hash:
        # A password mode with no password set would be an open door wearing a lock. Fail closed.
        mode = "disabled"
    expires = rec.download_expires_at
    if expires is None and policy.get("download_expiry_days") and rec.stopped_at:
        expires = rec.stopped_at + timedelta(days=int(policy["download_expiry_days"]))
    return {
        "mode": mode,
        "min_role": policy["download_min_role"],
        "expires_at": expires,
        "watermark": policy["watermark"] if rec.watermark is None else bool(rec.watermark),
        "ttl_minutes": int(policy["link_ttl_minutes"]),
    }


def resolve_download(rec: LiveRecording, event: Event | None, user: User, policy: dict,
                     *, password: str | None = None, now: datetime | None = None) -> DownloadDecision:
    """Decide whether to mint a signed download URL, and mint it.

    Checks in strictest-useful order: can they even see it → is downloading switched on → is the
    window still open → do they clear the role bar → is the passphrase right → does a file exist.
    The order matters for what an unauthorised caller learns: someone who cannot view the recording
    is told nothing about its download configuration.
    """
    now = now or datetime.now(timezone.utc)
    visible, reason = can_view(rec, event, user)
    if not visible:
        return DownloadDecision(error=reason or "This recording is not available")

    rules = download_rules(rec, policy)
    if rules["mode"] == "disabled":
        return DownloadDecision(error="Downloads are turned off for this recording")
    if rules["expires_at"] and rules["expires_at"] <= now:
        return DownloadDecision(error="The download window for this recording has closed")

    # The role bar applies to STAFF only. An outside attendee watching a public replay is not a
    # "viewer role inside the org", and measuring them against the org's staff ladder would either
    # lock out every legitimate audience download or, set the other way, hand org-internal
    # recordings to anyone. Audience downloads are governed by visibility + mode alone.
    if is_org_staff(rec, user) and _ROLE_RANK.get(user.role, -1) < _ROLE_RANK.get(rules["min_role"], 3):
        return DownloadDecision(error="Your role cannot download recordings")

    if rules["mode"] == "password":
        if not password:
            return DownloadDecision(needs_password=True,
                                    error="This download is password protected")
        if not verify_password(password, rec.download_password_hash or ""):
            return DownloadDecision(needs_password=True, error="Incorrect password")

    if not rec.storage_key or not rec.size_bytes:
        return DownloadDecision(error="No file was captured for this recording")

    ttl = rules["ttl_minutes"] * 60
    url = storage.signed_url(rec.storage_key, ttl=ttl, download_name=download_filename(rec))
    if url is None:
        return DownloadDecision(error="Recording storage is not configured on this deployment")
    return DownloadDecision(url=url, watermarked=rules["watermark"], expires_in=ttl)


def playback_url(rec: LiveRecording, policy: dict) -> str | None:
    """A signed URL for the in-app player. No `download_name`, so the browser streams it inline
    instead of saving it — the player needs range requests, not an attachment."""
    if not rec.storage_key or not rec.size_bytes:
        return None
    return storage.signed_url(rec.storage_key, ttl=int(policy["link_ttl_minutes"]) * 60)


def download_filename(rec: LiveRecording, event_title: str | None = None) -> str:
    base = (rec.title or event_title or "recording").strip() or "recording"
    stamp = (rec.stopped_at or rec.started_at)
    suffix = f" {stamp:%Y-%m-%d}" if stamp else ""
    return f"{re.sub(r'[\\\\/:*?\"<>|]+', '-', base)[:100]}{suffix}.mp4"


# ── transcripts ───────────────────────────────────────────────────────────────
# No ASR engine is configured, so there is no way to GENERATE a transcript here. Rather than
# pretend, this accepts one: an organization already running Whisper (or paying for a
# transcription service) uploads WebVTT or SRT and every downstream feature — search, copy,
# download, timestamp navigation, subtitles — then works on real words.

ASR_PROVIDER = None       # set to an object with transcribe(url) -> segments to enable generation
AI_PROVIDER = None        # set to an object with summarise(context) -> dict to enable narratives

MAX_TRANSCRIPT_SEGMENTS = 20000     # ~11 hours of speech at 2s/segment
MAX_SEGMENT_CHARS = 1000

_TIME_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_CUE_RE = re.compile(r"^(.*?)\s*-->\s*(.*?)\s*$")
# WebVTT's own speaker markup, and the plain "Name: text" convention every human transcript uses.
_VOICE_RE = re.compile(r"^<v\s+([^>]+)>\s*(.*)$", re.DOTALL)
_SPEAKER_RE = re.compile(r"^([A-Z][\w .'-]{1,40}):\s+(.*)$", re.DOTALL)


def _ms(text: str) -> int | None:
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    hours, minutes, seconds, frac = m.groups()
    return (int(hours or 0) * 3600000 + int(minutes) * 60000 + int(seconds) * 1000
            + int(frac.ljust(3, "0")))


def parse_transcript(text: str, *, language: str = "en", source: str = "upload") -> tuple[dict | None, str | None]:
    """WebVTT or SRT -> the stored shape. Returns (transcript, error).

    One parser for both formats because they differ only in the timestamp separator and an index
    line, and a second parser would be a second place for the speaker-extraction rules to drift.
    Speaker names are read from `<v Name>` or a `Name:` prefix when present; when they are not,
    `speakers` is empty and the UI says separation was not available in the source rather than
    inventing "Speaker 1".
    """
    if not (text or "").strip():
        return None, "The transcript file is empty"

    segments: list[dict] = []
    # Blank-line separated blocks in both formats. ﻿ is the BOM a Windows-exported VTT carries.
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿"))
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines or lines[0].strip().upper().startswith("WEBVTT"):
            continue
        cue_index = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if cue_index is None:
            continue
        match = _CUE_RE.match(lines[cue_index])
        start, end = (_ms(match.group(1)), _ms(match.group(2))) if match else (None, None)
        if start is None:
            continue
        body = "\n".join(lines[cue_index + 1:]).strip()
        if not body:
            continue
        speaker = None
        voice = _VOICE_RE.match(body)
        if voice:
            speaker, body = voice.group(1).strip(), voice.group(2).strip()
        else:
            named = _SPEAKER_RE.match(body)
            if named:
                speaker, body = named.group(1).strip(), named.group(2).strip()
        # Strip any remaining VTT inline tags — <i>, <c.yellow>, <00:00:01.000> karaoke cues.
        body = re.sub(r"</?[^>]{1,60}>", "", body).strip()
        if not body:
            continue
        segments.append({
            "start_ms": start,
            "end_ms": end if (end is not None and end >= start) else start,
            "speaker": speaker,
            "text": body[:MAX_SEGMENT_CHARS],
        })
        if len(segments) >= MAX_TRANSCRIPT_SEGMENTS:
            break

    if not segments:
        return None, "No timed cues were found — expected WebVTT or SRT"
    segments.sort(key=lambda s: s["start_ms"])
    speakers = sorted({s["speaker"] for s in segments if s["speaker"]})
    return {
        "language": (language or "en")[:12],
        "source": source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "segments": segments,
        "speakers": speakers,
        # Stated as a fact about the SOURCE, so the UI can explain why there are no speaker
        # labels instead of leaving a column mysteriously blank.
        "speaker_separation": bool(speakers),
        "duration_ms": segments[-1]["end_ms"],
    }, None


def _vtt_time(ms: int) -> str:
    ms = max(0, int(ms))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def to_vtt(transcript: dict | None) -> str:
    """Serialise to WebVTT for a <track> element. Always valid, even for an empty transcript —
    a browser that is handed a malformed track silently shows no captions at all."""
    lines = ["WEBVTT", ""]
    for i, seg in enumerate((transcript or {}).get("segments") or [], start=1):
        lines.append(str(i))
        lines.append(f"{_vtt_time(seg['start_ms'])} --> {_vtt_time(seg.get('end_ms') or seg['start_ms'])}")
        speaker = seg.get("speaker")
        # `<v Name>` is the standard cue-voice span, so a player that understands it can style
        # per speaker instead of having the name baked into the caption text.
        lines.append(f"<v {speaker}>{seg['text']}" if speaker else seg["text"])
        lines.append("")
    return "\n".join(lines)


def to_text(transcript: dict | None) -> str:
    """Plain text for Copy / Download. Timestamps kept: a transcript without them cannot be
    checked against the recording, which is most of what a transcript is for."""
    out = []
    for seg in (transcript or {}).get("segments") or []:
        stamp = _vtt_time(seg["start_ms"])[:8]
        who = f"{seg['speaker']}: " if seg.get("speaker") else ""
        out.append(f"[{stamp}] {who}{seg['text']}")
    return "\n".join(out)


def search_transcript(transcript: dict | None, query: str, *, limit: int = 200) -> list[dict]:
    """Case-insensitive substring hits with their timestamps.

    Python-side, not SQL: the transcript is one JSON document already loaded for the page, and a
    full-text index on a JSON column would be a second thing to keep in step for a search that
    runs over a few thousand short strings.
    """
    needle = (query or "").strip().lower()
    if len(needle) < 2:
        return []
    hits = []
    for seg in (transcript or {}).get("segments") or []:
        if needle in seg["text"].lower():
            hits.append({"start_ms": seg["start_ms"], "speaker": seg.get("speaker"),
                         "text": seg["text"]})
            if len(hits) >= limit:
                break
    return hits


# ── derived insights ──────────────────────────────────────────────────────────

_UNAVAILABLE_NARRATIVE = (
    "No language model is configured on this deployment, so a written summary cannot be "
    "produced. Set an AI provider to enable it."
)
_UNAVAILABLE_SENTIMENT = (
    "Sentiment needs a language model over the transcript. The reaction and engagement figures "
    "below are measured, not inferred."
)

# Words that dominate any chat log and say nothing about the subject. Kept short and generic on
# purpose — a long curated list is a maintenance burden that buys very little on top of this.
_STOPWORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because been before being
below between both but by can cannot could couldn't did didn't do does doesn't doing don't down
during each few for from further had hadn't has hasn't have haven't having he her here hers
herself him himself his how i if in into is isn't it its itself let's me more most mustn't my
myself no nor not of off on once only or other ought our ours ourselves out over own same shan't
she should shouldn't so some such than that the their theirs them themselves then there these
they this those through to too under until up very was wasn't we were weren't what when where
which while who whom why with won't would wouldn't you your yours yourself yourselves
just like really thanks thank yes yeah ok okay hi hello great good nice will can't also going get
got know think see said says say one two lot bit sure right well much many
""".split())
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
# A term must appear this often to count as a keyword. Below it, "keyword" means "somebody typed
# this twice", which produces a tag cloud of noise.
MIN_KEYWORD_COUNT = 3
# A sample counts as a highlight when its engagement exceeds the mean by this multiple. 1.8 keeps
# a flat event from reporting twenty "important moments" that are all just the mean.
HIGHLIGHT_FACTOR = 1.8


def _offset_ms(when: datetime | None, origin: datetime | None) -> int | None:
    """Position of a real timestamp within the recording, or None if it falls outside it.

    Anything before the capture started, or after it stopped, is NOT a chapter — a poll launched
    ten minutes before Record was pressed does not appear in the file, and pinning it to 00:00
    would put a marker on footage that does not contain it.
    """
    if when is None or origin is None:
        return None
    delta = int((when - origin).total_seconds() * 1000)
    return delta if delta >= 0 else None


def derive_insights(rec: LiveRecording, context: dict) -> dict:
    """Everything that can be honestly said about this recording from the rows around it.

    `context` is assembled by crud.media.insight_context — polls, announcements, activity,
    messages, questions and analytics samples for the event, already scoped and limited. Kept as a
    plain dict so this function is pure and directly testable without a database.
    """
    origin = rec.started_at
    duration = rec.duration_ms or 0
    inside = lambda ms: ms is not None and (duration == 0 or ms <= duration)  # noqa: E731

    chapters = _chapters(context, origin, inside)
    moments = _highlights(context, origin, inside)
    keywords = _keywords(context)
    decisions = _decisions(context, origin, inside)
    totals = context.get("totals") or {}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": getattr(AI_PROVIDER, "name", None) or "derived",
        # ── needs a model ──
        "summary": _narrative("summary", context),
        "action_items": _narrative("action_items", context),
        "sentiment": {"available": False, "reason": _UNAVAILABLE_SENTIMENT},
        # ── measured / derived ──
        "chapters": chapters,
        "moments": moments,
        "keywords": keywords,
        "topics": _topics(context, keywords),
        "suggested_tags": _suggested_tags(context, keywords),
        "decisions": decisions,
        "engagement": {
            "messages": int(totals.get("messages") or 0),
            "questions": int(totals.get("questions") or 0),
            "questions_answered": int(totals.get("questions_answered") or 0),
            "polls": int(totals.get("polls") or 0),
            "poll_votes": int(totals.get("poll_votes") or 0),
            "reactions": int(totals.get("reactions") or 0),
            "hands": int(totals.get("hands") or 0),
            "peak_viewers": int(totals.get("peak_viewers") or 0),
        },
        # What produced each derived field, so the UI never has to guess whether a number is real.
        "sources": {
            "chapters": "poll launches, announcements and stage changes with recorded timestamps",
            "moments": "engagement spikes in the 15-second analytics samples",
            "keywords": "term frequency over this event's chat and Q&A",
            "decisions": "closed polls and their recorded vote counts",
        },
    }


def _narrative(field: str, context: dict) -> dict:
    """A model-dependent field. Asks the provider if one is registered; otherwise says why not."""
    if AI_PROVIDER is None:
        return {"available": False, "reason": _UNAVAILABLE_NARRATIVE}
    try:
        produced = AI_PROVIDER.summarise(context) or {}
    except Exception as exc:  # noqa: BLE001 — a provider outage must not fail the whole page
        log.warning("AI provider failed for %s: %s", field, exc)
        return {"available": False, "reason": "The AI provider could not be reached."}
    value = produced.get(field)
    if not value:
        return {"available": False, "reason": "The AI provider returned nothing for this field."}
    return {"available": True, "value": value, "provider": getattr(AI_PROVIDER, "name", "ai")}


def _chapters(context: dict, origin, inside) -> list[dict]:
    """Real moments, in order. Every one is something a person did at a recorded time."""
    out = []
    for poll in context.get("polls") or []:
        at = _offset_ms(poll.get("launched_at"), origin)
        if inside(at):
            out.append({"at_ms": at, "title": f"Poll: {poll['question']}"[:120], "kind": "poll"})
    for ann in context.get("announcements") or []:
        at = _offset_ms(ann.get("sent_at"), origin)
        if inside(at):
            out.append({"at_ms": at, "title": f"Announcement: {ann['text']}"[:120], "kind": "announcement"})
    for act in context.get("activity") or []:
        at = _offset_ms(act.get("created_at"), origin)
        if inside(at):
            out.append({"at_ms": at, "title": act["text"][:120], "kind": act.get("kind") or "system"})
    out.sort(key=lambda c: c["at_ms"])
    # An opening chapter so the player's chapter list starts at the beginning of the file rather
    # than at whatever happened first, which is usually several minutes in.
    if not out or out[0]["at_ms"] > 1000:
        out.insert(0, {"at_ms": 0, "title": "Start of recording", "kind": "start"})
    return out[:60]


def _highlights(context: dict, origin, inside) -> list[dict]:
    """Engagement spikes. A REAL measurement: the sampler already wrote these every 15 seconds.

    Scored on interaction (messages + questions + reactions + hands) rather than viewers, because
    viewer count drifts slowly and says nothing about what was interesting — a spike in people
    typing is the closest thing this platform has to "something happened here".
    """
    samples = context.get("samples") or []
    if len(samples) < 4:
        return []
    scored = []
    for s in samples:
        at = _offset_ms(s.get("created_at"), origin)
        if not inside(at):
            continue
        score = (int(s.get("messages") or 0) + int(s.get("questions") or 0) * 2
                 + int(s.get("reactions") or 0) + int(s.get("hands") or 0) * 2)
        scored.append((at, score, s))
    if not scored:
        return []
    mean = sum(s for _, s, _ in scored) / len(scored)
    if mean <= 0:
        return []
    peaks = [(at, score, s) for at, score, s in scored if score >= mean * HIGHLIGHT_FACTOR]
    peaks.sort(key=lambda p: p[1], reverse=True)

    # Thin out neighbours: consecutive 15s samples in one busy minute are ONE moment, and
    # returning four adjacent markers would make the highlight reel useless.
    kept: list[tuple[int, int, dict]] = []
    for peak in peaks:
        if all(abs(peak[0] - k[0]) > 60_000 for k in kept):
            kept.append(peak)
        if len(kept) >= 12:
            break
    kept.sort(key=lambda p: p[0])
    return [{"at_ms": at, "score": score, "viewers": int(s.get("viewers") or 0),
             "label": f"{score} interactions in 15s"} for at, score, s in kept]


def _keywords(context: dict) -> list[dict]:
    counts = Counter()
    for text in context.get("texts") or []:
        for word in _WORD_RE.findall((text or "").lower()):
            if word not in _STOPWORDS and len(word) > 2:
                counts[word] += 1
    return [{"term": term, "count": n} for term, n in counts.most_common(30)
            if n >= MIN_KEYWORD_COUNT]


def _topics(context: dict, keywords: list[dict]) -> list[str]:
    """The organizer's own labels first, then the strongest keywords.

    Not clustering — clustering needs embeddings. The event's tags and category are a human's
    answer to "what is this about", which beats a guess, and the top terms extend it.
    """
    topics = [t for t in (context.get("event_tags") or []) if t]
    if context.get("event_category"):
        topics.append(context["event_category"])
    for kw in keywords[:8]:
        if kw["term"] not in {t.lower() for t in topics}:
            topics.append(kw["term"])
    return topics[:12]


def _suggested_tags(context: dict, keywords: list[dict]) -> list[str]:
    """Auto-tags the organizer has not already applied. Suggestions, applied on a click — nothing
    here writes to `tags`, because a tag that appeared by itself is a tag nobody trusts."""
    existing = {t.lower() for t in (context.get("event_tags") or []) if t}
    return [kw["term"] for kw in keywords if kw["term"] not in existing][:8]


def _decisions(context: dict, origin, inside) -> dict:
    """Closed polls with their winning option.

    These are genuine recorded decisions with genuine vote counts, which is why they are reported
    as decisions rather than being lumped in with the model-dependent fields. A poll that closed
    with no votes is skipped: "the room decided nothing" is not a decision.
    """
    items = []
    for poll in context.get("polls") or []:
        if poll.get("status") != "closed":
            continue
        options = [o for o in (poll.get("options") or []) if isinstance(o, dict)]
        total = sum(int(o.get("votes") or 0) for o in options)
        if not options or total == 0:
            continue
        winner = max(options, key=lambda o: int(o.get("votes") or 0))
        at = _offset_ms(poll.get("closed_at") or poll.get("launched_at"), origin)
        items.append({
            "at_ms": at if inside(at) else None,
            "question": poll["question"],
            "outcome": str(winner.get("label") or ""),
            "votes": int(winner.get("votes") or 0),
            "total_votes": total,
            "share": round(int(winner.get("votes") or 0) * 100 / total, 1),
        })
    return {"items": items,
            "note": ("Free-text decisions spoken aloud need a language model over the transcript; "
                     "these are the poll outcomes, which are recorded votes.")}


def demo() -> None:
    """Self-check for the parts with real edge cases: policy coercion, the transcript parser, the
    download decision order, and the two derivations that must never invent a number."""
    from types import SimpleNamespace

    # ── policy ──
    accepted, rejected = clean_policy({
        "downloads": "password", "link_ttl_minutes": 99999, "recycle_bin_days": 0,
        "watermark": "yes", "downloads_typo": 1, "download_min_role": "emperor",
    })
    assert accepted["downloads"] == "password"
    assert accepted["link_ttl_minutes"] == 1440, "TTL must clamp, not pass through"
    assert accepted["recycle_bin_days"] == 1, "a 0-day recycle bin is not a recycle bin"
    assert accepted["watermark"] is True
    assert set(rejected) == {"downloads_typo", "download_min_role"}
    assert policy_for(None)["downloads"] == "allowed"
    assert policy_for(SimpleNamespace(media={"downloads": "disabled"}))["downloads"] == "disabled"
    # Retention is ordered weakest-first and drops disabled thresholds.
    assert retention_plan({"auto_delete_days": 365, "auto_archive_days": 90,
                           "cold_storage_days": 0}) == [("archive", 90), ("delete", 365)]

    # ── transcript ──
    vtt = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
<v Ada Lovelace>Welcome everyone to the <i>quarterly</i> review.

2
00:00:05.500 --> 00:00:08.000
Grace Hopper: Revenue is up on the quarter.
"""
    t, err = parse_transcript(vtt)
    assert err is None and len(t["segments"]) == 2
    assert t["segments"][0]["speaker"] == "Ada Lovelace"
    assert t["segments"][0]["text"] == "Welcome everyone to the quarterly review.", t["segments"][0]
    assert t["segments"][1]["speaker"] == "Grace Hopper", "Name: prefix must be read as a speaker"
    assert t["speakers"] == ["Ada Lovelace", "Grace Hopper"] and t["speaker_separation"] is True
    assert t["segments"][1]["start_ms"] == 5500

    # SRT: comma decimals and an index line, same parser.
    srt = "1\n00:00:02,250 --> 00:00:03,000\nHello there\n"
    s, err = parse_transcript(srt)
    assert err is None and s["segments"][0]["start_ms"] == 2250
    assert s["speaker_separation"] is False and s["speakers"] == [], "must not invent Speaker 1"

    # Round trip, and the round trip is parseable again.
    again, err = parse_transcript(to_vtt(t))
    assert err is None and len(again["segments"]) == 2 and again["speakers"] == t["speakers"]
    assert to_vtt(None).startswith("WEBVTT"), "an empty track must still be valid VTT"
    assert "[00:00:01]" in to_text(t) and "Ada Lovelace:" in to_text(t)
    assert parse_transcript("not a subtitle file")[1], "garbage must be refused, not stored"
    assert parse_transcript("")[1]

    # Search finds the cue and its timestamp; a one-character query is not a search.
    assert search_transcript(t, "revenue")[0]["start_ms"] == 5500
    assert search_transcript(t, "r") == [] and search_transcript(t, "absent") == []

    # ── download decision ──
    org_policy = policy_for(None)
    staff = SimpleNamespace(id="u1", role="host", org_id="org1")
    rec = SimpleNamespace(id="r1", org_id="org1", visibility="organization", status="stopped",
                          storage_key="k.mp4", size_bytes=10, deleted_at=None, created_by="u1",
                          download_policy=None, download_password_hash=None,
                          download_expires_at=None, watermark=None, stopped_at=None,
                          started_at=None, title="Town Hall")
    # Storage unconfigured: an honest error, never a fabricated URL.
    d = resolve_download(rec, None, staff, org_policy)
    assert d.url is None and "storage is not configured" in d.error

    rec.download_policy = "disabled"
    assert "turned off" in resolve_download(rec, None, staff, org_policy).error

    rec.download_policy = "password"
    # Password mode with no password set must FAIL CLOSED, not wave everyone through.
    assert download_rules(rec, org_policy)["mode"] == "disabled"
    rec.download_password_hash = "$2b$12$notarealhash"
    d = resolve_download(rec, None, staff, org_policy)
    assert d.needs_password and d.url is None

    rec.download_policy, rec.download_password_hash = None, None
    rec.download_expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert "window" in resolve_download(rec, None, staff, org_policy).error
    rec.download_expires_at = None

    # A speaker is below the default host bar; an org_admin is above it.
    speaker = SimpleNamespace(id="u2", role="speaker", org_id="org1")
    assert "role cannot download" in resolve_download(rec, None, speaker, org_policy).error
    # …and cannot be reached at all when they cannot even view it: the visibility check runs first,
    # so the reply says nothing about how downloads are configured.
    rec.visibility = "private"
    assert "private" in resolve_download(rec, None, speaker, org_policy).error
    rec.visibility = "organization"

    # An outsider never reaches an org-visibility recording.
    outsider = SimpleNamespace(id="u9", role="viewer", org_id="other")
    assert can_view(rec, None, outsider)[0] is False
    # Public still requires a real file and the organizer's replay switch.
    rec.visibility = "public"
    event = SimpleNamespace(id="e1", org_id="org1", visibility="public", status="ended",
                            replay_enabled=False, registration_required=False)
    assert can_view(rec, event, outsider)[0] is False, "replay off means no public replay"
    event.replay_enabled = True
    assert can_view(rec, event, outsider)[0] is True
    # A soft-deleted recording is invisible to the audience but still reachable by staff.
    rec.deleted_at = datetime.now(timezone.utc)
    assert can_view(rec, event, outsider)[0] is False
    assert can_view(rec, event, staff)[0] is True
    rec.deleted_at = None

    # A moderator manages a live room, not the media library.
    assert can_manage(rec, staff) is True
    assert can_manage(rec, SimpleNamespace(id="m", role="moderator", org_id="org1")) is False
    assert can_manage(rec, SimpleNamespace(id="a", role="org_admin", org_id="other")) is False

    # ── insights ──
    start = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    r = SimpleNamespace(started_at=start, duration_ms=600_000)
    ctx = {
        "polls": [
            {"question": "Ship on Friday?", "status": "closed", "launched_at": start + timedelta(minutes=2),
             "closed_at": start + timedelta(minutes=3),
             "options": [{"label": "Yes", "votes": 7}, {"label": "No", "votes": 3}]},
            # Closed with no votes — not a decision.
            {"question": "Empty", "status": "closed", "launched_at": start + timedelta(minutes=4),
             "closed_at": start + timedelta(minutes=4), "options": [{"label": "A", "votes": 0}]},
            # Launched BEFORE the recording started: must not become a chapter.
            {"question": "Earlier", "status": "live", "launched_at": start - timedelta(minutes=5),
             "options": []},
        ],
        "announcements": [{"text": "Break in 5", "sent_at": start + timedelta(minutes=5)}],
        "activity": [{"kind": "role", "text": "Ada took the stage",
                      "created_at": start + timedelta(minutes=1)}],
        "samples": [{"created_at": start + timedelta(seconds=15 * i),
                     "messages": 1, "questions": 0, "reactions": 0, "hands": 0, "viewers": 10}
                    for i in range(20)],
        "texts": ["roadmap roadmap roadmap pricing", "pricing pricing the and thanks",
                  "roadmap latency latency latency"],
        "event_tags": ["Quarterly"], "event_category": "Town Hall",
        "totals": {"messages": 42, "reactions": 9, "peak_viewers": 88},
    }
    # One sample spikes hard enough to be a highlight.
    ctx["samples"][10] = {**ctx["samples"][10], "messages": 40, "questions": 3}
    out = derive_insights(r, ctx)

    assert out["summary"]["available"] is False and "No language model" in out["summary"]["reason"]
    assert out["action_items"]["available"] is False
    assert out["sentiment"]["available"] is False, "sentiment must never be fabricated"
    assert out["provider"] == "derived"

    titles = [c["title"] for c in out["chapters"]]
    assert titles[0] == "Start of recording", "nothing happens near 0:00, so a start marker leads"
    assert any("Ship on Friday" in t for t in titles) and any("Break in 5" in t for t in titles)
    assert not any("Earlier" in t for t in titles), "a pre-recording poll is not in the file"
    assert all(c["at_ms"] >= 0 for c in out["chapters"])
    assert out["chapters"] == sorted(out["chapters"], key=lambda c: c["at_ms"])
    # …but when something REAL happens at the top of the file it is the first chapter, and no
    # synthetic marker is stacked above it.
    at_zero = derive_insights(r, {**ctx, "activity": [
        {"kind": "role", "text": "Ada took the stage", "created_at": start}]})
    assert at_zero["chapters"][0]["title"] == "Ada took the stage"

    assert len(out["moments"]) == 1 and out["moments"][0]["at_ms"] == 150_000

    terms = [k["term"] for k in out["keywords"]]
    assert "roadmap" in terms and "latency" in terms
    assert "the" not in terms and "thanks" not in terms, "stopwords must be dropped"
    assert all(k["count"] >= MIN_KEYWORD_COUNT for k in out["keywords"])

    assert out["topics"][0] == "Quarterly" and "Town Hall" in out["topics"]
    assert "Quarterly" not in out["suggested_tags"], "do not suggest a tag already applied"

    decisions = out["decisions"]["items"]
    assert len(decisions) == 1 and decisions[0]["outcome"] == "Yes"
    assert decisions[0]["votes"] == 7 and decisions[0]["total_votes"] == 10
    assert decisions[0]["share"] == 70.0
    assert out["engagement"]["messages"] == 42 and out["engagement"]["peak_viewers"] == 88

    # An event with nothing recorded produces empty lists, not placeholders.
    bare = derive_insights(SimpleNamespace(started_at=start, duration_ms=0), {})
    assert bare["moments"] == [] and bare["keywords"] == [] and bare["decisions"]["items"] == []
    assert bare["chapters"] == [{"at_ms": 0, "title": "Start of recording", "kind": "start"}]

    # Filenames are filesystem- and header-safe.
    assert "/" not in download_filename(SimpleNamespace(
        title="Q3/Q4: plan", stopped_at=start, started_at=start))

    print("ok  media policy / transcript / insight self-check")


if __name__ == "__main__":
    demo()
