"""DNS evidence for a custom domain: does it route to us, and does its owner say it is theirs.

Two independent records, both required:

  CNAME  <hostname>               -> the configured target (CUSTOM_DOMAIN_CNAME_TARGET)
         Proves traffic for the hostname will reach the platform. Chains are followed, so
         events.example.com -> alias.example.net -> cname.zoikostream.com passes.
  TXT    _zoikostream.<hostname>  =  zoikostream-domain-verification=<token>
         Proves control of the zone. The CNAME alone is NOT ownership proof: anyone who
         controls the DNS can point it at us, but only the requesting organization knows its
         token, so a CNAME someone else published can never verify somebody else's claim.

Queries go to public resolvers (CUSTOM_DOMAIN_DNS_RESOLVERS), not this host's own. One
answer is never treated as final: a pass needs every resolver that ANSWERED to agree, a
resolver that timed out or failed is "temporary" and never fails a domain on its own, and the
lifecycle (services/custom_domains.py) re-checks pending and active domains on a schedule.

Nothing here touches the database, and nothing raises: every outcome is a result code.
"""
from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Callable

import dns.exception
import dns.resolver

from ..config import settings

log = logging.getLogger(__name__)

TXT_LABEL = "_zoikostream"
TXT_VALUE_PREFIX = "zoikostream-domain-verification="
MAX_CNAME_HOPS = 8
QUERY_TIMEOUT = 2.0      # per attempt
QUERY_LIFETIME = 4.0     # per query, across retries

# Raw query outcomes.
OK, NXDOMAIN, NOANSWER, TIMEOUT, ERROR = "ok", "nxdomain", "noanswer", "timeout", "error"
_TEMPORARY = (TIMEOUT, ERROR)

# Result codes (custom_domains.ERROR_MESSAGES words each one for people).
NXDOMAIN_CODE = "nxdomain"
CNAME_MISSING = "cname_missing"
CNAME_MISMATCH = "cname_mismatch"
TXT_MISSING = "txt_missing"
TXT_MISMATCH = "txt_mismatch"
DNS_TIMEOUT = "dns_timeout"
DNS_ERROR = "dns_error"

# (nameserver, name, rdtype) -> (outcome, values). Replaceable in tests.
QueryFn = Callable[[str, str, str], "tuple[str, list[str]]"]


def txt_name(hostname: str) -> str:
    return f"{TXT_LABEL}.{hostname}"


def txt_value(token: str) -> str:
    return f"{TXT_VALUE_PREFIX}{token}"


def resolvers() -> list[str]:
    return [r.strip() for r in settings.CUSTOM_DOMAIN_DNS_RESOLVERS.split(",") if r.strip()] or ["1.1.1.1"]


def _norm(name: str) -> str:
    return name.strip().lower().rstrip(".")


def query(nameserver: str, name: str, rdtype: str) -> tuple[str, list[str]]:
    """One DNS question to one resolver. Never raises."""
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [nameserver]
    resolver.timeout = QUERY_TIMEOUT
    resolver.lifetime = QUERY_LIFETIME
    try:
        answer = resolver.resolve(name, rdtype, search=False, raise_on_no_answer=True)
    except dns.resolver.NXDOMAIN:
        return NXDOMAIN, []
    except dns.resolver.NoAnswer:
        return NOANSWER, []
    except (dns.resolver.LifetimeTimeout, dns.exception.Timeout):
        return TIMEOUT, []
    except dns.exception.DNSException as exc:     # NoNameservers (SERVFAIL), YXDOMAIN, ...
        log.info("custom-domain dns query failed type=%s rdtype=%s", type(exc).__name__, rdtype)
        return ERROR, []
    if rdtype == "CNAME":
        return OK, [_norm(r.target.to_text()) for r in answer]
    if rdtype == "TXT":
        return OK, [b"".join(r.strings).decode("utf-8", "replace") for r in answer]
    return OK, [r.to_text() for r in answer]


@dataclass(frozen=True)
class _Cname:
    outcome: str            # "ok" | NXDOMAIN_CODE | CNAME_MISSING | CNAME_MISMATCH | TIMEOUT | ERROR
    found: str | None = None


@dataclass(frozen=True)
class _Txt:
    outcome: str            # "ok" | TXT_MISSING | TXT_MISMATCH | TIMEOUT | ERROR
    present: bool = False


def _cname(q: QueryFn, ns: str, host: str, target: str) -> _Cname:
    name, first_hop = host, None
    for _ in range(MAX_CNAME_HOPS):
        outcome, values = q(ns, name, "CNAME")
        if outcome == OK and values:
            nxt = _norm(values[0])
            first_hop = first_hop or nxt
            if nxt == target:
                return _Cname(OK, nxt)
            name = nxt
            continue
        if outcome in _TEMPORARY:
            return _Cname(outcome)
        if outcome == NXDOMAIN and name == host:
            return _Cname(NXDOMAIN_CODE)
        break      # the chain ended (no CNAME, or it points at a name that does not exist)
    if first_hop is None:
        return _Cname(CNAME_MISSING)
    return _Cname(CNAME_MISMATCH, first_hop)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].strip()
    return value


def _txt(q: QueryFn, ns: str, host: str, token: str) -> _Txt:
    outcome, values = q(ns, txt_name(host), "TXT")
    if outcome in _TEMPORARY:
        return _Txt(outcome)
    if outcome != OK or not values:
        return _Txt(TXT_MISSING)
    expected = txt_value(token)
    # Constant-time per comparison: the token is the proof, so how close a guess came is not
    # something to leak through timing.
    if any(hmac.compare_digest(_unquote(v).encode(), expected.encode()) for v in values):
        return _Txt(OK, True)
    return _Txt(TXT_MISMATCH, True)


@dataclass(frozen=True)
class DnsResult:
    cname_ok: bool
    txt_ok: bool
    cname_found: str | None = None
    txt_present: bool = False
    error: str | None = None     # the blocking problem; None exactly when both records pass
    temporary: bool = False      # the error is a resolver failure, not an answer about the domain

    @property
    def ok(self) -> bool:
        return self.cname_ok and self.txt_ok

    def as_json(self) -> dict:
        return {"cname_ok": self.cname_ok, "cname_found": self.cname_found,
                "txt_ok": self.txt_ok, "txt_present": self.txt_present}


# A missing name is the root problem, then routing, then ownership: tell people about the
# record they need to add first.
_PRIORITY = (NXDOMAIN_CODE, CNAME_MISSING, CNAME_MISMATCH, TXT_MISSING, TXT_MISMATCH)


def check(hostname: str, token: str, target: str, *, nameservers: list[str] | None = None,
          q: QueryFn | None = None) -> DnsResult:
    """Ownership TXT and routing CNAME for `hostname`, across every resolver."""
    q = q or query
    host, target = _norm(hostname), _norm(target)
    answered: list[tuple[_Cname, _Txt]] = []
    temporary: set[str] = set()
    for ns in nameservers or resolvers():
        t = _txt(q, ns, host, token)
        c = _cname(q, ns, host, target)
        if t.outcome in _TEMPORARY or c.outcome in _TEMPORARY:
            temporary.update(o for o in (t.outcome, c.outcome) if o in _TEMPORARY)
            continue
        answered.append((c, t))

    if not answered:
        code = DNS_TIMEOUT if TIMEOUT in temporary else DNS_ERROR
        return DnsResult(False, False, error=code, temporary=True)

    cname_ok = all(c.outcome == OK for c, _ in answered)
    txt_ok = all(t.outcome == OK for _, t in answered)
    found = next((c.found for c, _ in answered if c.found), None)
    present = any(t.present for _, t in answered)
    problems = {c.outcome for c, _ in answered} | {t.outcome for _, t in answered}
    error = next((p for p in _PRIORITY if p in problems), None)
    return DnsResult(cname_ok, txt_ok, cname_found=found, txt_present=present, error=error)


def resolves(name: str, *, nameservers: list[str] | None = None, q: QueryFn | None = None) -> bool:
    """Whether `name` currently has an address (A or AAAA) on any resolver."""
    q = q or query
    for ns in nameservers or resolvers():
        for rdtype in ("A", "AAAA"):
            outcome, values = q(ns, _norm(name), rdtype)
            if outcome == OK and values:
                return True
    return False
