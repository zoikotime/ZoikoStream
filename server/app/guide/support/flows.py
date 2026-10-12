"""Viewer-support flow definitions, as data.

Each step is shown one at a time. An option leads to another step, a terminal outcome, or a
switch into a different flow. Steps that depend on public event context (D2) read ONLY the
validated safe claims; with no context they fall back to general wording and never assert an
event-specific fact (Conversation Flow §14–16, CF-AT-21..24).

Copy follows the canonical patterns in Conversation Flow §28 and the Prompt/Guardrails safe
templates. Nothing here asks for, accepts or echoes a PIN, password or invitation link: every
input is a fixed option id (PR-017, UX-AT-09).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Claims = dict


@dataclass(frozen=True)
class Terminal:
    status: str            # resolved | escalated
    resolution_code: str


@dataclass(frozen=True)
class Switch:
    flow_type: str
    resolution_code: str


@dataclass(frozen=True)
class Option:
    option_id: str
    label: str
    to: str | Terminal | Switch


@dataclass(frozen=True)
class Step:
    step_id: str
    prompt: str | Callable[[Claims | None], str]
    options: tuple[Option, ...]
    input_type: str = "choice"            # choice | boolean
    # Registered destination ids to show alongside the step (e.g. status page).
    action_ids: tuple[str, ...] = ()
    # A step can be skipped when the public context it needs is absent.
    applies: Callable[[Claims | None], bool] | None = None
    skip_to: str | None = None

    def render_prompt(self, claims: Claims | None) -> str:
        return self.prompt(claims) if callable(self.prompt) else self.prompt


@dataclass(frozen=True)
class Flow:
    flow_type: str
    title: str
    first_step: str
    steps: dict[str, Step] = field(default_factory=dict)


RESOLVED = Terminal("resolved", "resolved")


def _yes(to, label="Yes") -> Option:
    return Option("yes", label, to)


def _no(to, label="No") -> Option:
    return Option("no", label, to)


# ── D2 helpers (validated public claims only) ──────────────────────────────────────────────

def _start_time(claims: Claims | None) -> str | None:
    if not claims or not claims.get("public_start_at"):
        return None
    start = datetime.fromisoformat(claims["public_start_at"].replace("Z", "+00:00"))
    tz_name = claims.get("public_timezone")
    try:
        if tz_name:
            start = start.astimezone(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        tz_name = None
    # American English presentation (PRD §14): "Saturday, October 10 at 2:00 PM EDT".
    hour = start.strftime("%I").lstrip("0") or "12"
    text = f"{start.strftime('%A, %B')} {start.day} at {hour}:{start.strftime('%M %p')}"
    zone = start.strftime("%Z") if tz_name else "UTC"
    return f"{text} {zone}".strip()


def _has_timing(claims: Claims | None) -> bool:
    return _start_time(claims) is not None


def _timing_prompt(claims: Claims | None) -> str:
    when = _start_time(claims)
    status = (claims or {}).get("public_status")
    lead = f"This event's published start time is {when}."
    if status in ("scheduled", "starting_soon"):
        lead += " It hasn't started yet, so the page may not show video until then."
    elif status == "ended":
        lead += " The public event information shows the live event has ended."
    return f"{lead} Can you access the event now?"


def _captions_prompt(claims: Claims | None) -> str:
    claims = claims or {}
    langs = claims.get("caption_languages") or []
    available = claims.get("captions_available")
    if langs:
        fact = ("The public event information shows captions for this event in "
                f"{_language_list(langs)}.")
    elif available is True:
        fact = "The public event information shows captions are available for this event."
    elif available is False:
        fact = "The public event information doesn't show captions for this event."
    else:
        fact = "I don't have public event information that confirms captions for this event."
    how = ("When captions are offered, turn them on with the CC or captions button in the "
           "video player controls.")
    return f"{fact} {how} Did that help?"


_LANGUAGE_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German",
                   "pt": "Portuguese", "it": "Italian", "hi": "Hindi", "zh": "Chinese",
                   "ja": "Japanese", "ko": "Korean", "ar": "Arabic", "te": "Telugu",
                   "ta": "Tamil", "nl": "Dutch", "ru": "Russian"}


def _language_list(codes: list[str]) -> str:
    names = [_LANGUAGE_NAMES.get(c.split("-")[0].lower(), c) for c in codes]
    names = list(dict.fromkeys(names))
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _replay_prompt(claims: Claims | None) -> str:
    state = (claims or {}).get("replay_public_state")
    if state == "available":
        fact = ("The public event information shows a replay is available. You can watch it "
                "from the same event page.")
    elif state == "pending":
        fact = ("The public event information shows a replay is being prepared but isn't "
                "available yet.")
    elif state == "unavailable":
        fact = "The public event information doesn't show a replay for this event."
    else:
        fact = ("I don't have public event information about a replay for this event. Whether "
                "a replay is offered is up to the event organizer.")
    return f"{fact} Does that answer your question?"


# ── Flow definitions ───────────────────────────────────────────────────────────────────────

def _flow(flow_type: str, title: str, *steps: Step) -> Flow:
    return Flow(flow_type, title, steps[0].step_id, {s.step_id: s for s in steps})


VIEWER_ACCESS = _flow(
    "viewer_access", "Event access",
    Step("invitation_source", "How did you receive the event invitation?", (
        Option("email_invitation", "Email invitation", "original_link"),
        Option("message_link", "Message or shared link", "original_link"),
        Option("other", "Other", "original_link"),
    )),
    Step("original_link",
         "Open the event from the most recent invitation the organizer sent you, rather than "
         "a copied or older link. Does the event page open?", (
             _yes("access_prompt"), _no("basic_checks"),
         ), input_type="boolean"),
    Step("basic_checks",
         "Try these quick checks: refresh the page once, open the link in a new tab in a "
         "current version of Chrome, Edge, Firefox, or Safari, and make sure other websites "
         "load on your connection. Does the event page open now?", (
             _yes("access_prompt"), _no(Terminal("escalated", "page_unreachable")),
         ), input_type="boolean"),
    Step("access_prompt",
         "Is the event page asking you for a PIN, password, or access code?", (
             _yes("enter_on_page"), _no("event_timing"),
         ), input_type="boolean"),
    Step("enter_on_page",
         "Enter it only in the field on the event page, never here in Guide. If you don't "
         "have it or it isn't accepted, the event organizer can help. Guide can't look up or "
         "check access codes. Did that work?", (
             Option("worked", "Yes, I'm in", RESOLVED),
             Option("not_accepted", "It still doesn't work",
                    Terminal("escalated", "access_code_not_accepted")),
         )),
    Step("event_timing", _timing_prompt, (
        _yes(RESOLVED), _no(Terminal("escalated", "access_not_restored")),
    ), input_type="boolean", applies=_has_timing, skip_to="resolve_check"),
    Step("resolve_check", "Can you access the event now?", (
        _yes(RESOLVED), _no(Terminal("escalated", "access_not_restored")),
    ), input_type="boolean"),
)

PLAYBACK = _flow(
    "playback", "Playback help",
    Step("issue_type", "What's happening with the video?", (
        Option("not_loading", "Video won't load", "player_open"),
        Option("buffering", "It keeps buffering", "player_open"),
        Option("stops", "It stops or freezes", "player_open"),
        Option("quality", "Poor picture quality", "player_open"),
        Option("other", "Something else", "player_open"),
    )),
    Step("player_open", "Is the event page open with the video player showing?", (
        _yes("refresh"),
        _no(Switch("viewer_access", "switched_to_viewer_access"),
            "No, I can't get into the event"),
    ), input_type="boolean"),
    Step("refresh",
         "Refresh the page once, or close the tab and reopen your event link. Is the video "
         "playing now?", (_yes(RESOLVED), _no("browser_device")), input_type="boolean"),
    Step("browser_device",
         "Try a current version of Chrome, Edge, Firefox, or Safari, or another device if one "
         "is handy. Is it working now?", (_yes(RESOLVED), _no("network")),
         input_type="boolean"),
    Step("network",
         "Check your connection: pause other downloads or streams, move closer to your Wi-Fi "
         "router, or switch networks if you can. Is the video working now?",
         (_yes(RESOLVED), _no("public_status")), input_type="boolean"),
    Step("public_status",
         "You can check the public ZoikoStream status page for any reported service issue. "
         "A playback problem on one device doesn't by itself mean there's an outage. Is the "
         "video working now?", (
             _yes(RESOLVED), _no(Terminal("escalated", "playback_not_resolved")),
         ), input_type="boolean", action_ids=("status-page",)),
)

AUDIO = _flow(
    "audio", "Audio help",
    Step("video_playing", "Is the video playing?", (
        _yes("player_volume"),
        _no(Switch("playback", "switched_to_playback"), "No, the video isn't playing"),
    ), input_type="boolean"),
    Step("player_volume",
         "Check the volume control in the video player and make sure it isn't muted. Can you "
         "hear audio now?", (_yes(RESOLVED), _no("device_volume")), input_type="boolean"),
    Step("device_volume",
         "Check your device and browser volume, and make sure the browser tab isn't muted. If "
         "you're using headphones or Bluetooth speakers, confirm they're connected. Can you "
         "hear audio now?", (_yes(RESOLVED), _no("browser_device")), input_type="boolean"),
    Step("browser_device",
         "Try reopening the event in another current browser or on another device. Is audio "
         "working now?", (_yes(RESOLVED), _no(Terminal("escalated", "audio_not_resolved"))),
         input_type="boolean"),
)

CAPTIONS = _flow(
    "captions", "Captions",
    Step("captions_help", _captions_prompt, (
        Option("helped", "Yes, thanks", RESOLVED),
        Option("need_help", "I still need captions help",
               Terminal("escalated", "captions_assistance")),
    )),
)

REPLAY = _flow(
    "replay", "Replay",
    Step("replay_state", _replay_prompt, (
        Option("answered", "Yes, thanks", RESOLVED),
        Option("need_help", "I need more help", Terminal("escalated", "replay_assistance")),
    )),
)

VIEWER_OTHER = _flow(
    "viewer_other", "Viewer help",
    Step("category", "What do you need help with?", (
        Option("access", "Getting into the event", Switch("viewer_access", "category_access")),
        Option("video", "Video or buffering", Switch("playback", "category_playback")),
        Option("audio", "Audio", Switch("audio", "category_audio")),
        Option("captions", "Captions", Switch("captions", "category_captions")),
        Option("replay", "Replay", Switch("replay", "category_replay")),
        Option("other", "Something else", Terminal("escalated", "other_viewer_issue")),
    )),
)

FLOWS: dict[str, Flow] = {f.flow_type: f for f in (
    VIEWER_ACCESS, PLAYBACK, AUDIO, CAPTIONS, REPLAY, VIEWER_OTHER)}

# Canonical terminal copy (Conversation Flow §28; PR-025: no blame, no implied access).
RESOLVED_MESSAGE = ("Great — if anything else comes up, I can help with another viewer issue "
                    "or a ZoikoStream question.")
ESCALATE_WITH_ORGANIZER = ("The standard steps didn't solve this. The event organizer manages "
                           "access and settings for this event, so they're the best contact. "
                           "ZoikoStream viewer support can also help.")
ESCALATE_GENERIC = ("The standard steps didn't solve this. ZoikoStream viewer support can help "
                    "further. If you were invited by an event organizer, they can also help "
                    "with access to their event.")
SECRET_NOTICE = "Never share your event PIN, password, or invitation link here."


def validate_definitions() -> None:
    """Every option points at a real step; every flow is reachable. Run by the test suite and
    at import so a typo cannot ship a dead-end flow."""
    for flow in FLOWS.values():
        assert flow.first_step in flow.steps, flow.flow_type
        for step in flow.steps.values():
            assert step.options, (flow.flow_type, step.step_id)
            ids = [o.option_id for o in step.options]
            assert len(ids) == len(set(ids)), (flow.flow_type, step.step_id)
            for opt in step.options:
                if isinstance(opt.to, str):
                    assert opt.to in flow.steps, (flow.flow_type, step.step_id, opt.to)
                elif isinstance(opt.to, Switch):
                    assert opt.to.flow_type in FLOWS or opt.to.flow_type in {
                        "viewer_access", "playback", "audio", "captions", "replay"}
            if step.skip_to:
                assert step.skip_to in flow.steps


validate_definitions()
