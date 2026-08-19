"""Self-check for the pure-logic pieces of controlled customer export: eligibility rules
(services/delivery.py::export_eligibility) and the delivery token hash (crud/delivery.py).
Pure logic, no DB. Run: `python test_delivery.py` (or pytest).

find_delivery_by_token's expiry/revocation branches touch the DB directly (same as
crud.event.find_access_link) and are verified manually against the running app + dev DB,
matching this session's established no-DB-fixture convention for query-shaped logic."""

from types import SimpleNamespace

from app.crud.delivery import _hash_delivery_token
from app.services.delivery import export_eligibility


def _rec(**kw):
    defaults = dict(status="stopped", enforced=True, legal_hold=False)
    return SimpleNamespace(**{**defaults, **kw})


def test_eligible_when_stopped_and_enforced():
    eligible, reason = export_eligibility(_rec(), held=False)
    assert eligible is True
    assert reason is None


def test_not_eligible_when_not_stopped():
    eligible, reason = export_eligibility(_rec(status="recording"), held=False)
    assert eligible is False
    assert "captured file" in reason


def test_not_eligible_when_not_enforced():
    eligible, reason = export_eligibility(_rec(enforced=False), held=False)
    assert eligible is False
    assert "captured file" in reason


def test_not_eligible_under_recordings_own_legal_hold():
    eligible, reason = export_eligibility(_rec(legal_hold=True), held=False)
    assert eligible is False
    assert "legal hold" in reason


def test_not_eligible_under_events_open_governance_hold():
    """held=True comes from admin_crud.event_under_legal_hold — a hold placed on the EVENT,
    not the recording row's own column. Must block exactly like the recording's own flag."""
    eligible, reason = export_eligibility(_rec(legal_hold=False), held=True)
    assert eligible is False
    assert "legal hold" in reason


def test_hash_is_deterministic_and_not_reversible_by_inspection():
    raw = "some-raw-token-value"
    h1 = _hash_delivery_token(raw)
    h2 = _hash_delivery_token(raw)
    assert h1 == h2
    assert h1 != raw
    assert len(h1) == 64  # sha256 hex digest


def test_hash_is_token_sensitive():
    assert _hash_delivery_token("token-a") != _hash_delivery_token("token-b")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nAll delivery checks passed.")
