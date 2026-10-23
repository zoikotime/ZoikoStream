"""Client IP capture for audit events: where it comes from, and who is allowed to choose it.

Two separate defects, both proven here.

1. COVERAGE. `crud.create_audit_log` is called from 59 places across nine modules, and
   exactly one of them passed an `ip` — the admin router's private `_audit()` helper. Every
   audit row written anywhere else (27 in routers/organization.py, 16 in crud/admin.py,
   3 in routers/commercial.py, …) stored NULL, so the console's IP column showed a dash for
   all of them. The address is now bound once per request by main.bind_client_ip and read by
   create_audit_log when none was passed, so no call site has to know about it.

2. SPOOFING. `client_ip` returned the LEFTMOST entry of X-Forwarded-For, with a comment
   arguing that a terminating frontend made the value unspoofable. The hop is unspoofable;
   the entry being read was not. X-Forwarded-For is `client, proxy1, proxy2 …` — the caller
   writes the left end and each proxy APPENDS the peer it saw. A request sent with
   `X-Forwarded-For: 1.2.3.4` reaches the app as `1.2.3.4, <real address>`, and the old code
   recorded 1.2.3.4. Any caller could choose the address written into the audit trail.

These tests touch no database: the extractor is pure, the context variable is in-process, and
create_audit_log is driven with a session stub that records what it was handed.
"""
import ipaddress

import pytest

from app.config import settings
from app.request_context import get_client_ip, reset_client_ip, set_client_ip
from app.security import _parse_ip, client_ip


class Req:
    """Just the two things client_ip reads off a request."""

    def __init__(self, xff=None, peer="10.0.0.1"):
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = type("C", (), {"host": peer})() if peer else None


@pytest.fixture(autouse=True)
def _one_trusted_hop(monkeypatch):
    # The shipped default, and what a single terminating frontend means.
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1, raising=False)


# ── who gets to choose the address ──────────────────────────────────────────────────────

def test_a_forged_header_cannot_choose_the_recorded_address():
    # THE regression. Against the original this returned "1.2.3.4".
    assert client_ip(Req("1.2.3.4, 203.0.113.9")) == "203.0.113.9"


def test_a_longer_forged_prefix_is_still_ignored():
    assert client_ip(Req("9.9.9.9, 8.8.8.8, 1.2.3.4, 203.0.113.9")) == "203.0.113.9"


def test_an_ordinary_request_through_one_proxy_is_read_correctly():
    assert client_ip(Req("203.0.113.9")) == "203.0.113.9"


def test_two_trusted_hops_read_one_further_left(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2, raising=False)
    # client, then what proxy1 saw, then what proxy2 saw.
    assert client_ip(Req("1.2.3.4, 203.0.113.9, 10.1.1.1")) == "203.0.113.9"


def test_no_trusted_proxy_ignores_the_header_entirely(monkeypatch):
    # Local development, or the app exposed directly: the header is not evidence of anything.
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0, raising=False)
    assert client_ip(Req("1.2.3.4, 203.0.113.9", peer="198.51.100.7")) == "198.51.100.7"


def test_a_chain_shorter_than_configured_does_not_index_past_its_start(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 3, raising=False)
    assert client_ip(Req("203.0.113.9")) == "203.0.113.9"


def test_without_the_header_the_peer_address_is_used():
    assert client_ip(Req(None, peer="198.51.100.7")) == "198.51.100.7"


def test_a_request_with_no_peer_at_all_yields_none():
    assert client_ip(Req(None, peer=None)) is None


# ── what counts as an address ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("203.0.113.9", "203.0.113.9"),
    ("203.0.113.9:443", "203.0.113.9"),
    ("2001:db8::1", "2001:db8::1"),
    ("[2001:db8::1]:443", "2001:db8::1"),
    ("::1", "::1"),
    ("  203.0.113.9  ", "203.0.113.9"),
])
def test_addresses_are_parsed_and_normalised(raw, expected):
    assert _parse_ip(raw) == expected
    ipaddress.ip_address(_parse_ip(raw))          # genuinely an address, not a lookalike


@pytest.mark.parametrize("raw", ["", None, "not-an-ip", "999.999.999.999", "<script>", "203.0.113.9/24", "localhost"])
def test_anything_that_is_not_an_address_is_discarded(raw):
    assert _parse_ip(raw) is None


def test_a_malformed_header_falls_back_to_the_peer():
    # A corrupted or injected value must not be stored verbatim.
    assert client_ip(Req("not-an-ip", peer="198.51.100.7")) == "198.51.100.7"


def test_ipv6_survives_the_whole_path():
    assert client_ip(Req("1.2.3.4, 2001:db8::1")) == "2001:db8::1"


# ── the shared capture boundary ─────────────────────────────────────────────────────────

def test_nothing_is_bound_outside_a_request():
    assert get_client_ip() is None


def test_binding_and_unbinding_leaves_no_residue():
    token = set_client_ip("203.0.113.9")
    assert get_client_ip() == "203.0.113.9"
    reset_client_ip(token)
    assert get_client_ip() is None


class FakeSession:
    """Captures the row create_audit_log builds, without a database."""

    def __init__(self):
        self.added = []
        self.committed = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True


class Actor:
    id = "11111111-1111-1111-1111-111111111111"
    email = "ada@example.com"


def _write(db, **kw):
    from app.crud.admin import create_audit_log
    create_audit_log(db, actor=Actor(), action="organization.update", **kw)
    return db.added[-1]


def test_a_call_site_that_passes_nothing_still_records_the_address():
    # This is the 58-call-site fix: organization.py and friends never passed an ip.
    token = set_client_ip("203.0.113.9")
    try:
        assert _write(FakeSession()).ip == "203.0.113.9"
    finally:
        reset_client_ip(token)


def test_an_explicit_address_still_wins():
    # _audit() passes one, and anything that knows better must keep deciding for itself.
    token = set_client_ip("203.0.113.9")
    try:
        assert _write(FakeSession(), ip="198.51.100.7").ip == "198.51.100.7"
    finally:
        reset_client_ip(token)


def test_background_work_records_no_address_rather_than_a_guess():
    # Tickers and scheduler callbacks have no client. A dash is the honest answer; inventing
    # the server's own address would be worse than showing nothing.
    assert get_client_ip() is None
    assert _write(FakeSession()).ip is None


def test_the_row_is_still_written_and_committed():
    db = FakeSession()
    _write(db)
    assert len(db.added) == 1 and db.committed


# ── the pipeline as a whole ─────────────────────────────────────────────────────────────

def test_the_api_schema_carries_the_address_to_the_console():
    from app.schemas.admin import AuditLogOut
    assert "ip" in AuditLogOut.model_fields
    assert AuditLogOut.model_fields["ip"].is_required() is False      # legacy rows stay valid


def test_a_legacy_row_without_an_address_still_serialises():
    from app.schemas.admin import AuditLogOut
    row = AuditLogOut.model_validate(
        {"id": "11111111-1111-1111-1111-111111111111", "action": "organization.update", "ip": None})
    assert row.ip is None


def test_the_audit_table_has_somewhere_to_put_it():
    # No migration was needed: the column already existed and was simply never written to.
    from app.models.audit_log import AuditLog
    col = AuditLog.__table__.columns.get("ip")
    assert col is not None and col.nullable


def test_the_middleware_is_registered_ahead_of_the_request_measurer():
    """http middleware runs in REVERSE registration order, so the binder has to be registered
    first to wrap everything that might write an audit row.

    Read from main.py's source rather than by importing the app: importing it pulls in
    app.guide, which needs pgvector — an optional dependency this check has no stake in.
    """
    from pathlib import Path
    import app
    src = (Path(app.__file__).resolve().parent / "main.py").read_text(encoding="utf-8")
    bind = src.index("async def bind_client_ip")
    measure = src.index("async def measure_requests")
    assert bind < measure, "bind_client_ip must be registered before measure_requests"
