"""Deterministic boundary and intent classification (Conversation Flow §2, §20; Prompt §5).

Runs BEFORE retrieval or any model call, every turn. A private/restricted request never enters
public RAG or reaches the model as something to answer (CG-07): it gets a fixed boundary
template plus the closest safe route. Prior allowed turns never grant later authority — this is
re-evaluated per message.

These are pattern rules, deliberately conservative. They are the first layer of defense in
depth; the system prompt, retrieval eligibility and the output validator are the others.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .registry import INTENTS


# ── Input normalisation (Evaluation §12/§21 "Unicode/spacing/encoded instruction attempts") ──
# Every pattern below runs on normalize(text), so "ｉｇｎｏｒｅ", "ig<ZWSP>nore" or a Cyrillic
# "ignоre" cannot slip past a rule that "ignore" would trip. The visitor's own text is not
# rewritten for display or for the model; this is the classification view only.
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e"
                               "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
                               "\u2060\u2061\u2062\u2063\u2064\u2066\u2067\u2068\u2069"
                               "\u3164\ufeff\uffa0"), None)
# Common Latin look-alikes (Unicode TR39 confusables; Cyrillic/Greek/IPA letter subset).
_CONFUSABLES = str.maketrans({
    "\u0430": "a", "\u0432": "b", "\u0435": "e", "\u0451": "e", "\u043a": "k", "\u043c": "m",
    "\u043d": "h", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0442": "t", "\u0443": "y",
    "\u0445": "x", "\u0455": "s", "\u0456": "i", "\u0457": "i", "\u0458": "j", "\u0501": "d",
    "\u04bb": "h", "\u051b": "q", "\u051d": "w", "\u0261": "g", "\u0269": "i", "\u0131": "i",
    "\u2113": "l",
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K", "\u041c": "M", "\u041d": "H",
    "\u041e": "O", "\u0420": "P", "\u0421": "C", "\u0422": "T", "\u0425": "X", "\u0405": "S",
    "\u0406": "I", "\u0408": "J",
    "\u03b1": "a", "\u03bf": "o", "\u03c1": "p", "\u03bd": "v", "\u03c4": "t", "\u03b9": "i",
    "\u03ba": "k", "\u03c5": "u",
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H", "\u0399": "I",
    "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P", "\u03a4": "T",
    "\u03a5": "Y", "\u03a7": "X",
})
# "i g n o r e" / "i.g.n.o.r.e" -> "ignore": runs of 4+ single letters joined by one separator.
_SPACED = re.compile(r"(?<![A-Za-z])(?:[A-Za-z][ .\-_*]){3,}[A-Za-z](?![A-Za-z])")
# Evaluation §21 "typos": an adjacent-letter transposition of a routing keyword ("pricnig",
# "outgae", "ZiokoStream") is folded back to the keyword. Transpositions only — the commonest
# typing slip — so no real word is ever rewritten into a different one.
_KEYWORDS = (
    "zoikostream", "pricing", "price", "prices", "billed", "billing", "fees", "outage",
    "problem", "problems", "incident", "incidents", "issues", "streaming", "stream", "working",
    "service", "status", "having", "conference", "corporate", "managed", "please", "invoice",
    "invoices", "password", "captions", "buffering", "playback", "replay", "memorial",
    "wedding", "graduation", "church", "agent", "sales", "demo", "discount", "account",
    "guest", "attending", "registered", "person",
)


def _transpositions(word: str):
    for i in range(len(word) - 1):
        if word[i] != word[i + 1]:
            yield word[:i] + word[i + 1] + word[i] + word[i + 2:]


_TYPO = {t: k for k in _KEYWORDS for t in _transpositions(k) if t not in _KEYWORDS}
_WORDS = re.compile(r"[A-Za-z]{4,}")


def normalize(text: str) -> str:
    """Classification view of a message: NFKC (full-width/compatibility forms), invisible and
    bidi-control characters removed, combining marks dropped, Latin look-alikes folded,
    letter-spaced words rejoined, transposition typos of routing keywords fixed, whitespace
    collapsed."""
    t = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    if any(unicodedata.combining(c) for c in t):
        t = unicodedata.normalize("NFC", "".join(
            c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c)))
    t = t.translate(_CONFUSABLES)
    t = _SPACED.sub(lambda m: re.sub(r"[ .\-_*]", "", m.group(0)), t)
    t = _WORDS.sub(lambda m: _TYPO.get(m.group(0).lower(), m.group(0)), t)
    return re.sub(r"\s+", " ", t).strip()


# ── Unsupported locale (Prompt PG-AT-28; Evaluation §21) ───────────────────────────────────
# en-US is the only approved locale pack. Lightweight detection, no model and no heavy
# dependency: a non-Latin script, or Latin text whose function words are clearly another
# language. Short or mixed messages default to English (a false "unsupported" is worse than
# answering an English question).
_SCRIPTS = [("cyrillic", 0x0400, 0x052F), ("greek", 0x0370, 0x03FF), ("arabic", 0x0600, 0x06FF),
            ("hebrew", 0x0590, 0x05FF), ("devanagari", 0x0900, 0x097F),
            ("bengali", 0x0980, 0x09FF), ("tamil", 0x0B80, 0x0BFF), ("telugu", 0x0C00, 0x0C7F),
            ("thai", 0x0E00, 0x0E7F), ("hangul", 0xAC00, 0xD7AF), ("kana", 0x3040, 0x30FF),
            ("cjk", 0x4E00, 0x9FFF)]
_EN = {"the", "a", "an", "is", "are", "do", "does", "can", "how", "what", "when", "where",
       "why", "which", "who", "my", "your", "i", "you", "we", "it", "to", "for", "of", "and",
       "in", "on", "with", "this", "that", "be", "have", "not", "there", "will", "much",
       "need", "our", "if", "or", "me", "us", "get", "want", "stream", "event"}
_STOPWORDS = {
    "es": {"el", "los", "las", "que", "del", "por", "para", "con", "una", "como", "cómo",
           "qué", "cuánto", "cuanto", "cuesta", "puedo", "tiene", "está", "hay", "pero",
           "precio", "mi", "su", "evento", "también", "dónde", "quiero", "necesito", "y", "es",
           "un", "en", "la", "de", "se", "lo", "al", "nuestro", "transmitir"},
    "fr": {"le", "les", "des", "est", "une", "pour", "avec", "dans", "que", "qui", "pas",
           "vous", "nous", "je", "combien", "comment", "pourquoi", "mon", "votre", "coûte",
           "prix", "et", "sur", "ce", "il", "peux", "puis", "un", "la", "de", "du", "en"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "mit", "für", "wie",
           "was", "ich", "sie", "wir", "kann", "kostet", "preis", "mein", "ihr", "auf", "zu",
           "warum", "wo", "gibt", "es", "den", "dem", "unser", "unsere"},
    "pt": {"o", "os", "as", "que", "não", "uma", "com", "para", "por", "como", "quanto",
           "custa", "preço", "meu", "minha", "você", "posso", "tem", "é", "e", "do", "da",
           "um", "de", "em", "nosso"},
    "it": {"il", "lo", "gli", "che", "non", "una", "con", "per", "come", "quanto", "costa",
           "prezzo", "mio", "posso", "è", "sono", "della", "del", "perché", "un", "di"},
    "nl": {"de", "het", "een", "en", "niet", "voor", "met", "hoe", "wat", "ik", "kan",
           "kost", "prijs", "mijn", "zijn", "waarom", "waar", "onze"},
}
_FOREIGN_MARKS = re.compile(r"[¿¡ñçßãõœàèìòùâêîôûäëïöüáéíóú]", re.I)


@dataclass(frozen=True)
class Locale:
    supported: bool
    detected: str       # en | es | fr | ... | <script name> — an enum, safe for audit

    @property
    def locale_class(self) -> str:
        return "en-US" if self.supported else "unsupported"


def detect_locale(text: str) -> Locale:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return Locale(True, "en")
    counts: dict[str, int] = {}
    for c in letters:
        cp = ord(c)
        for name, lo, hi in _SCRIPTS:
            if lo <= cp <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    if counts:
        script, n = max(counts.items(), key=lambda kv: kv[1])
        if n / len(letters) >= 0.3:
            return Locale(False, script)
    words = re.findall(r"[^\W\d_]+", text.lower())
    if len(words) < 3:
        return Locale(True, "en")
    en_hits = sum(w in _EN for w in words)
    best, best_hits = "en", 0
    for lang, stop in _STOPWORDS.items():
        hits = sum(w in stop for w in words)
        if hits > best_hits:
            best, best_hits = lang, hits
    score = best_hits + min(len(_FOREIGN_MARKS.findall(text)), 3)
    if best != "en" and best_hits >= 2 and score >= 3 and score > en_hits * 1.5:
        return Locale(False, best)
    return Locale(True, "en")


@dataclass(frozen=True)
class Boundary:
    state: str                 # allowed_public | private_required | restricted
    reason: str | None = None  # stable reason class for audit (never the text)


def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


# Conversation Flow §20.1 restricted categories.
_RESTRICTED = [
    ("prompt_extraction", _rx(
        r"\b(system|developer|hidden|initial|original)\s+(prompt|instructions?|message|rules|policy)",
        r"\bignore\s+(all\s+|any\s+|your\s+|the\s+)?(previous|prior|above|earlier)?\s*(instructions|rules|prompts?)",
        r"\b(reveal|print|show|repeat|output|dump)\b.{0,40}\b(prompt|instructions|rules|configuration)",
        r"\bchain[- ]of[- ]thought\b", r"\byour\s+(internal\s+)?reasoning\b",
        r"\bwhat\s+(did|do)\s+you\s+retriev", r"\b(internal|private)\s+(sources?|documents?|metadata)",
        r"\b(jailbreak|DAN mode|developer mode)\b",
        r"\b(pretend|act|behave|imagine)\s+(to\s+be|as|like|you\s+are|you'?re|that\s+you\s+are)\s+"
        r"(an?\s+|the\s+)?(admin|administrator|operator|developer|root|system|staff|employee)")),
    ("event_private", _rx(
        r"\bguest\s*list\b", r"\battendee(s)?\s+(list|names|emails)\b",
        r"\bwho\s+(else\s+)?(is|are|was|were)\s+(attending|invited|registered|watching)",
        r"\b(am\s+i|is\s+\w+(\s+\w+)?)\s+(on\s+the\s+(guest\s+)?list|invited|registered)\b",
        r"\b(change|edit|update|modify)\s+(the\s+|my\s+)?event\s+(settings|access|password|pin)",
        r"\b(send|give|tell)\s+me\s+(the|my)\s+(pin|access code|event password|invite link)",
        r"\bwhat\s+is\s+(the|my)\s+(event\s+)?(pin|access code|event password)\b",
        r"\b(what'?s|what\s+is)\s+the\s+(\w+\s+)?password\s+for\b",
        r"(?:^|[.!?:]\s*)(please\s+)?make\s+(?!it\b)[\w' ]{2,40}\s+(public|private|unlisted)\b",
        r"\bwhich\s+(viewers|attendees|people|guests)\s+(joined|watched|left|attended)\b",
        r"\b(ip\s+address(es)?|locations?)\s+of\s+(the\s+)?(viewers|attendees|guests)\b",
        r"\bis\s+my\s+\w+\s+watching\b", r"\bwho\s+(registered|signed\s+up|paid|rsvp'?d)\b",
        r"\bregistration\s+(answers|responses|details)\b", r"\b(my\s+\w+'?s?\s+)registration\s+for\b",
        r"\bwho\s+paid\s+for\s+tickets\b", r"\b(accounts|users|viewers)\s+(are\s+)?banned\b",
        r"\binternal\s+event\s+id\b", r"\bprivate\s+notes\b",
        r"\bexport\s+the\s+chat\s+(log\s+)?from\b",r"\bhidden\s+(q&a|questions)\b", r"\bq&a\s+questions\s+that\s+were\s+hidden\b",
        r"\brecording\s+link\b.{0,40}\bbefore\b.{0,20}\bpublished\b",
        r"\bwhat\s+email\s+did\b", r"\b(organizer|host|speaker)'?s?\s+(email|phone|backup\s+contact|contact)\b",
        r"\bbackup\s+contact\b")),
    ("credential_request", _rx(
        r"\b(admin|root|database|db|server|internal)\s+(password|credentials?|login)",
        r"\b(your|the|zoikostream'?s?)\s+(api[_ ]?keys?|secret keys?|tokens?|private keys?)\b",
        r"\b(give|send|tell|show)\s+me\b.{0,30}\b(api[_ ]?key|password|token|credentials)",
        r"\bprivate\s+stream\s+keys?\b",
        r"\b(give|send|share|tell)\s+me\b.{0,30}\bstream\s+keys?\b", r"\bdatabase\s+credentials\b")),
    ("customer_private", _rx(
        r"\b(list|name)\s+(of\s+|some\s+of\s+)?(your\s+)?(customers|clients|tenants)\b",
        r"\bwhich\s+(customers|clients|companies|organizations|churches|schools)\s+"
        r"(use|stream|are\s+using|have\s+used)\b",
        r"\bwho\s+(are\s+your\s+(customers|clients)|(else\s+)?uses\s+zoikostream)\b",
        r"\b(customer|client|user|attendee)\s+(list|emails|contact\s+details|data)\b",
        r"\bwhich\s+(\w+\s+){1,2}(use|uses|are\s+using|stream\s+with)\s+(zoikostream|you)\b",
        r"\blist\s+(of\s+)?your\s+(\w+\s+){0,2}(customers|clients)\b",
        r"\bname\s+(some|a\s+few|any)\s+(\w+\s+){1,2}that\s+(stream|streams|use|uses)\s+(with\s+)?"
        r"(you|zoikostream)\b")),
    ("operator", _rx(
        r"\bgo\s+live\b", r"\b(start|stop|end|pause)\s+(the\s+|my\s+|our\s+)?(stream|broadcast|recording|event)\b",
        r"\b(stream|ingest|encoder)\s+health\b", r"\btelemetry\b",
        r"\b(my|our)\s+(event\'?s?\s+)?readiness\b", r"\breadiness\s+((check|status|score)\s+)?(for|of)\s+(my|our)\b",
        r"\bfailover\b", r"\brun[- ]of[- ]show\b", r"\bproducer\s+(console|ready)\b",
        # Changing how an event is set up is the event team's control (PG §13, PG-AT-15).
        r"\b(change|edit|update|modify|configure|adjust)\s+(the\s+|my\s+|our\s+|this\s+)?"
        r"(event\s+)?(replay|recording|stream|streaming|broadcast|chat|caption|captions)\s+"
        r"(settings?|options?|availability|visibility|permissions?)\b",
        r"\b(enable|disable|turn\s+(on|off)|publish|unpublish|delete|remove)\s+(the\s+)?"
        r"(replay|recording)\s+(for|of)\s+(this|the|my|our)\s+(event|stream)\b",
        r"\b(our|my)\s+(viewer|audience)\s+(analytics|numbers|count)\b")),
    ("account_private", _rx(
        r"\b(my|our)\s+(account|subscription|invoice|invoices|bill|billing|payment method|"
        r"card on file|plan|organization|org|workspace|license|seats)\b",
        r"\b(my|our)\s+(\w+\s+)?(invoices?|balance|payments?|receipts?)\b",
        r"\bbalance\s+on\s+(my|our)\b",
        r"\b(cancel|downgrade|upgrade|change|renew)\s+(my|our)\b",
        r"\brefund\b", r"\breset\s+(my\s+)?password\b", r"\bcan'?t\s+(log|sign)\s*in\b",
        r"\bdelete\s+(my|our)\s+(account|event|recording|data)\b",
        r"\b(look\s*up|check|find)\s+(my|our)\b")),
    ("internal", _rx(
        r"\bpayroll\b", r"\bsalar(y|ies)\b", r"\bpayslip\b", r"\bhr\s+(policy|policies|team|portal)\b",
        r"\bemployee\s+(handbook|benefits|policy)\b", r"\bleave\s+policy\b",
        r"\binternal\s+(architecture|infrastructure|sop|security|source\s+code|code)\b",
        r"\b(incident\s+)?runbooks?\b", r"\bpost[- ]?mortems?\b",
        r"\b(pentest|penetration test)\s+(report|results|findings)\b",
        r"\bvulnerabilit(y|ies)\s+in\s+(zoikostream|your)\b")),
]

# A "my account" phrase inside a plainly public pricing/plan comparison is still public.
_PUBLIC_OVERRIDE = _rx(r"\bwhich\s+plan\b", r"\bwhat\s+plans?\b", r"\bplans?\s+(do you|are)\b")

_INTENT_RULES = [
    ("DEMO", _rx(r"\bdemo\b", r"\bdemonstration\b")),
    ("SALES", _rx(r"\b(talk|speak|chat)\s+(to|with)\s+(sales|someone in sales|a sales)",
                  r"\bcontact\s+sales\b", r"\bsales\s+team\b", r"\b(custom|enterprise)\s+quote\b",
                  r"\bback\s+to\s+(the\s+)?sales\b", r"\b(someone|somebody)\s+from\s+sales\b",
                  r"\bhave\s+sales\b", r"\bsales\s+(contact|call|reach|get\s+in\s+touch)")),
    ("HANDOFF", _rx(r"\b(human|real person|live agent|representative)\b",
                    r"\b(talk|speak)\s+to\s+(someone|somebody|a person|support)\b",
                    r"\bcontact\s+support\b", r"\bsupport\s+team\b",
                    r"\bconnect\s+me\s+(with|to)\s+(support|someone|somebody|a\s+person|an?\s+agent)\b",
                    r"\bget\s+me\s+(an?\s+)?(agent|person|human)\b")),
    ("STATUS", _rx(r"\b(is|are)\s+(zoikostream|the\s+(site|service|platform|website)|it|you|things)\s+down\b",
                   r"\boutage\b", r"\b(current|check\s+the|check)\s+(service\s+)?status\b", r"\bstatus\s+right\s+now\b",
                   r"\bdown\s+right\s+now\b",
                   r"\b(is|are)\s+(zoikostream|streaming|the\s+service|the\s+platform|the\s+site)\s+"
                   r"(working|up)\b",
                   r"\b(having|any)\s+(problems?|issues|incidents?)\s+(right\s+now|today|at\s+the\s+moment)\b",
                   r"\bany\s+incidents?\b",
                   r"\b(problems?|issues?)\s+with\s+zoikostream\s+(right\s+now|today|at\s+the\s+moment)\b")),
    ("CAPTIONS", _rx(r"\bcaptions?\b", r"\bsubtitles?\b", r"\bclosed\s+caption", r"\bcc\b")),
    ("REPLAY", _rx(r"\breplay\b", r"\bwatch\s+(it\s+)?later\b", r"\brewatch\b", r"\bon[- ]demand\b",
                   r"\brecording\s+(of|available)\b")),
    ("AUDIO", _rx(r"\bno\s+(sound|audio)\b", r"\bcan'?t\s+hear\b", r"\baudio\s+(is|isn'?t|not)\b",
                  r"\bsound\s+(is|isn'?t|not)\b",
                  r"\b(sound|audio)\s+keeps\b", r"\b(sound|audio)\b.{0,20}\bcutting\s+out\b")),
    ("PLAYBACK", _rx(r"\bbuffer", r"\bfreez", r"\bblack\s+screen\b", r"\bstutter", r"\blagg",
                     r"\b(video|stream|player)\s+(won'?t|isn'?t|is\s+not|not|doesn'?t|keeps)\b",
                     r"\bplayback\b", r"\bwon'?t\s+(play|load)\b", r"\bpixelat")),
    ("VIEWER_ACCESS", _rx(r"\bcan'?t\s+(get\s+in|get\s+into|access|join|open|enter)\b",
                          r"\bcannot\s+(access|join|open)\b", r"\b(join|access|enter)\s+(the|my|an)\s+event\b",
                          r"\binvitation\b", r"\binvite\s+link\b", r"\baccess\s+code\b",
                          r"\b(event\s+)?pin\b", r"\bwaiting\s+room\b", r"\blink\s+(doesn'?t|isn'?t|not)\s+work",
                          r"\bwon'?t\s+let\s+me\s+(in|join|watch)\b")),
    ("PRICING", _rx(r"\bpric(e|es|ing)\b", r"\bcosts?\b", r"\bhow\s+much\b", r"\b(what|which)\s+plans?\b",
                    r"\bplans?\s+(do\s+you|are|cost|and\s+pricing)\b", r"\b(subscription|pricing|paid)\s+plans?\b",
                    r"\bper\s+(month|year|event)\b", r"\bfree\s+trial\b", r"\btrial\b",
                    r"\bdiscount\b", r"\bcoupon\b", r"\bpromo\b", r"\bbilling\s+(cycle|options)\b",
                    r"\bannual(ly)?\b", r"\bmonthly\b", r"\bquote\b", r"\bfees?\b", r"\bbilled\b")),
    ("PRIVACY", _rx(r"\bprivacy\b", r"\bpersonal\s+data\b", r"\bgdpr\b", r"\bccpa\b", r"\bcookies?\b",
                    r"\bdata\s+(retention|deletion|protection|processors?|processing)\b",
                    r"\bhow\s+(is|do you use)\s+(my\s+)?(data|chat)\b", r"\b(my|your|personal)\s+(data|information)\b",
                    r"\bprocess(es|ing)?\s+(my|personal)\b", r"\bsell\s+(my|your|user)\b", r"\b(children|kids|minors)\b")),
    ("SECURITY_TRUST", _rx(r"\bsecur(e|ity)\b", r"\bencrypt", r"\bcomplian(t|ce)\b", r"\bsoc\s*2\b",
                           r"\biso\s*27001\b", r"\bhipaa\b", r"\btrust\s+center\b", r"\bdata\s+residency\b",
                           r"\bwhere\s+is\s+(my\s+|the\s+)?data\s+stored\b")),
    ("LEGAL", _rx(r"\bterms\b", r"\blegal\b", r"\bcontract\b", r"\bliabilit", r"\bagreement\b",
                  r"\bsla\b")),
    ("DEVELOPER", _rx(r"\bapi\b", r"\bsdk\b", r"\bwebhooks?\b", r"\bdeveloper", r"\bintegrat",
                      r"\bendpoint\b", r"\brtmp\b", r"\bsrt\b", r"\bembed\b")),
    ("DISCOVERY", _rx(r"\bwe\s+(need|want|are\s+looking|plan)\b", r"\blooking\s+for\b",
                      r"\b(can|could|does)\s+(you|zoikostream|it)\s+(help|stream|support|handle|work)\b",
                      r"\buse\s+case\b", r"\bfor\s+(our|my)\s+(church|school|company|event|team)\b",
                      r"\b(funeral|memorial|wedding|church|school|town\s*hall|webinar|conference)\b")),
]

# "And is it still the same?" right after a status answer asks for the status again.
STATUS_FOLLOWUP = _rx(r"\b(still|now|anymore|yet)\b", r"\bsame\b", r"\bback\s+up\b",
                      r"\b(any|an)\s+update\b")

EVENT_TIME = _rx(r"\bwhat\s+time\b", r"\bwhen\s+(does|will|is)\b.{0,20}\b(start|begin|live)\b",
                 r"\bstart\s+time\b")

_DISCOUNT = _rx(r"\bdiscount\b", r"\bcoupon\b", r"\bpromo(\s+code)?\b", r"\bnegotiat",
                r"\bbetter\s+(deal|price)\b", r"\bcheaper\b")


def classify_boundary(text: str) -> Boundary:
    text = normalize(text)
    for reason, pattern in _RESTRICTED:
        if pattern.search(text):
            if reason == "account_private" and _PUBLIC_OVERRIDE.search(text):
                continue
            state = "restricted" if reason in ("prompt_extraction", "credential_request",
                                               "operator", "internal",
                                               "customer_private") else "private_required"
            return Boundary(state, reason)
    return Boundary("allowed_public")


# "Does it support captions?" is a product question, not a viewer problem: capability questions
# about a viewer feature are answered from public product evidence, not the support flow.
_CAPABILITY = _rx(r"\b(do|does|can|will)\s+(you|it|zoikostream|the\s+(platform|product|player))\s+"
                  r"(support|offer|provide|have|include|do|allow|record)\b",
                  r"\bis\s+there\s+(a|an|any)\s+(option|feature|support)\b")
_VIEWER_INTENTS = {"CAPTIONS", "REPLAY", "AUDIO", "PLAYBACK"}


def classify_intent(text: str, intent_hint: str | None = None) -> str:
    """Free text decides; a quick-action hint only breaks ties (API §8.1: the hint is advisory
    and never bypasses routing)."""
    text = normalize(text)
    capability = bool(_CAPABILITY.search(text))
    viewer_feature = False
    for intent, pattern in _INTENT_RULES:
        if capability and intent in _VIEWER_INTENTS:
            viewer_feature = viewer_feature or bool(pattern.search(text))
            continue
        if viewer_feature and intent == "DISCOVERY":
            return "PRODUCT_QA"  # "Does it support captions?" is a product question
        if pattern.search(text):
            return intent
    if intent_hint and intent_hint in INTENTS:
        return INTENTS[intent_hint]
    return "PRODUCT_QA"


def is_discount_request(text: str) -> bool:
    return bool(_DISCOUNT.search(normalize(text)))


# CF §28 "Pricing quote": a request for a bespoke/custom price is answered with the canonical
# quote-based copy and the demo/sales route — never an estimate.
_QUOTE = _rx(r"\bquote\b", r"\b(custom|bespoke|tailored|enterprise|volume|special)\s+"
             r"(pric|plan|rate|deal)",
             r"\bhow\s+much\s+(would|will|does|do)\b.{0,30}\b(cost|charge|be)\b.{0,20}"
             r"\b(for\s+(us|our|my)|if\s+we)\b",
             r"\b(price|pricing|cost)\s+for\s+(our|my)\b")


def is_quote_request(text: str) -> bool:
    return bool(_QUOTE.search(normalize(text)))


# CF §6.1: ask ONE clarification only when a material unknown blocks routing. A discovery
# message that names neither a use case nor a live/replay need is that case; one that names
# either is answered directly (CF-AT-05, "no unnecessary clarification").
_USE_CASE = _rx(r"\b(funeral|memorial|wedding|church|worship|services?|school|graduation|"
                r"town\s*hall|webinar|conference|concert|sports?|games?|meetings?|ceremon|"
                r"class|lecture|training|launch|festival|gala|summit|recital|keynote)\w*")
_LIVE_OR_REPLAY = _rx(r"\blive\b", r"\breplay", r"\brecord", r"\bon[- ]demand\b",
                      r"\bwatch\s+(it\s+)?later\b", r"\bboth\b")


def needs_clarification(text: str) -> bool:
    t = normalize(text)
    return not _USE_CASE.search(t) and not _LIVE_OR_REPLAY.search(t) \
        and not _CAPABILITY.search(t)
