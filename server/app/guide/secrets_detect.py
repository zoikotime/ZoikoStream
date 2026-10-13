"""User-supplied secret detection and redaction (ZST-SEC-PRIV-PUB-GUIDE-001 §11).

Runs on every inbound free-text value BEFORE persistence, logging or model forwarding. A hit
replaces the matched span with a fixed placeholder and reports only the detector CLASS — the
detected value never leaves this function.

Pattern detection cannot prove something is a secret (§11, "DETECTION IS NOT PERFECT"). The
detectors therefore lean toward over-redaction for the high-harm classes (card numbers,
tokens, credential-bearing links), and the UX separately tells users never to share them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PLACEHOLDERS = {
    "payment_card": "[card number removed]",
    "password": "[password removed]",
    "event_pin": "[PIN removed]",
    "access_token": "[token removed]",
    "secret_link": "[private link removed]",
}

_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

# A separator ("is", ":", "=") is required: "my password isn't working" must not redact.
_PASSWORD = re.compile(
    r"(?i)\b(pass(?:word|wd|phrase)|pwd)\b(\s*(?:is|was|=|:)\s*)(['\"]?)([^\s'\"]{3,})\3")

# Longer alternatives first so "PIN code 4821" captures 4821, not "code".
_PIN = re.compile(
    r"(?i)\b(access\s*code|event\s*code|entry\s*code|invite\s*code|pass\s*code|passcode|"
    r"pin\s*code|pin\s*number|pin)\b"
    r"(\s*(?:is|was|=|:|-)?\s*)([A-Za-z0-9-]{3,12})\b")

_TOKENS = [
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),  # JWT
    re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}\b"),                     # Stripe
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                                # AWS
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),                                      # GitHub
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"),                                     # Slack
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b(?:api[_ -]?key|secret|token|client[_ -]?secret)\b\s*(?:is|=|:)\s*"
               r"['\"]?[A-Za-z0-9._~+/=-]{12,}"),
]
# A long opaque run mixing letters and digits reads as a credential even without a label.
_OPAQUE = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b")

_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_SECRET_QUERY_KEYS = re.compile(
    r"(?i)[?&#](?:token|key|sig|signature|code|pin|pass(?:word)?|reg|link|invite|access|auth|"
    r"session|ticket|t|k)=")
# Platform paths whose identifier itself is the credential or private reference.
_SECRET_PATHS = re.compile(r"(?i)/(?:deliveries|e|accept-invite|invite|i)/[A-Za-z0-9_-]{6,}")


@dataclass
class Detection:
    text: str
    classes: list[str] = field(default_factory=list)

    @property
    def suspected(self) -> bool:
        return bool(self.classes)


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect(text: str) -> Detection:
    found: list[str] = []

    def mark(cls: str):
        if cls not in found:
            found.append(cls)

    def links(m: re.Match) -> str:
        url = m.group(0)
        if _SECRET_QUERY_KEYS.search(url) or _SECRET_PATHS.search(url):
            mark("secret_link")
            return PLACEHOLDERS["secret_link"]
        return url

    out = _URL.sub(links, text)

    for pattern in _TOKENS:
        if pattern.search(out):
            mark("access_token")
            out = pattern.sub(PLACEHOLDERS["access_token"], out)
    if _OPAQUE.search(out):
        mark("access_token")
        out = _OPAQUE.sub(PLACEHOLDERS["access_token"], out)

    def cards(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            mark("payment_card")
            return PLACEHOLDERS["payment_card"]
        return m.group(0)

    out = _CARD_CANDIDATE.sub(cards, out)

    def password(m: re.Match) -> str:
        if m.group(4).lower().strip(".,!?") in _STOPWORDS:
            return m.group(0)
        mark("password")
        return f"{m.group(1)}{m.group(2)}{PLACEHOLDERS['password']}"

    out = _PASSWORD.sub(password, out)

    def pin(m: re.Match) -> str:
        value, separator = m.group(3), m.group(2).strip()
        # "PIN code for my event" — the captured word is ordinary English, not a value.
        if value.lower() in _STOPWORDS:
            return m.group(0)
        # Without "is"/":" only a digit-bearing value counts ("PIN 4821", not "PIN screen").
        if not separator and not any(ch.isdigit() for ch in value):
            return m.group(0)
        mark("event_pin")
        return f"{m.group(1)}{m.group(2)}{PLACEHOLDERS['event_pin']}"

    out = _PIN.sub(pin, out)
    return Detection(text=out, classes=found)


_STOPWORDS = {
    "for", "from", "the", "and", "but", "not", "isnt", "doesnt", "dont", "work", "works",
    "working", "field", "box", "page", "prompt", "screen", "again", "here", "there", "that",
    "this", "anymore", "wrong", "invalid", "incorrect", "expired", "ask", "asks", "asked",
    "required", "needed", "need", "number", "where", "what", "when", "with", "into", "help",
    "keeps", "still", "please", "missing", "rejected", "forgotten", "lost", "reset",
    "correct", "right", "same", "different", "case", "sensitive", "too", "very", "being",
    "it", "my", "your", "their", "our", "a", "an", "in", "on", "at", "to", "of", "or",
    # Descriptions of how credentials are handled, not credential values ("your password
    # is hashed", "the PIN is required").
    "hashed", "encrypted", "stored", "never", "optional", "readable", "shown", "sent",
    "needed", "set", "changed", "valid", "used", "case-sensitive",
}
