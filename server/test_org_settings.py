"""Self-check for org self-service crud + schema behavior. No real DB — fakes the
Session. Run with `python test_org_settings.py` (or pytest)."""
from types import SimpleNamespace

from app.crud import organization as crud
from app.schemas.organization import OrgMeOut, OrgNotifications, OrgProfileUpdate


class FakeDB:
    def __init__(self, scalar_result=None):
        self._scalar = scalar_result
    def commit(self): pass
    def refresh(self, _): pass
    def scalar(self, _stmt): return self._scalar


def test_apply_fields_is_partial():
    org = SimpleNamespace(name="Old", website="keep.me", slug=None)
    # Only `name` supplied → website must be untouched (exclude_unset).
    crud.apply_fields(FakeDB(), org, OrgProfileUpdate(name="New"))
    assert org.name == "New"
    assert org.website == "keep.me"


def test_merge_json_preserves_unsent_keys():
    org = SimpleNamespace(notifications={"billing": True, "mentions": True})
    before = org.notifications
    merged = crud.merge_json(FakeDB(), org, "notifications", {"mentions": False, "weekly_summary": True})
    assert merged == {"billing": True, "mentions": False, "weekly_summary": True}
    assert org.notifications is not before  # new dict → change detected on plain JSON column


def test_merge_json_from_null():
    org = SimpleNamespace(security=None)
    merged = crud.merge_json(FakeDB(), org, "security", {"require_2fa": True})
    assert merged == {"require_2fa": True}


def test_slug_taken():
    import uuid
    me = uuid.uuid4()
    assert crud.slug_taken(FakeDB(scalar_result=uuid.uuid4()), "taken", me) is True
    assert crud.slug_taken(FakeDB(scalar_result=None), "free", me) is False


def test_schema_shapes():
    # /me is a superset of profile (inheritance, not a duplicate schema).
    assert set(OrgProfileUpdate.model_fields) <= set(OrgMeOut.model_fields) | {"name"}
    # Notification defaults fill missing keys on read.
    n = OrgNotifications(**{"billing": False})
    assert n.billing is False and n.event_scheduled is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("org-settings self-check passed")
