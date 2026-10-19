"""Profile photos (services/avatars.py, routers/profile.py): the user's OWN avatar.

Pinned here: an upload is judged by its bytes (never its name or declared type), re-encoded to a
square WebP that is cropped rather than stretched, upright, and stripped of metadata and anything
smuggled inside; it persists on the account and appears wherever the account is shown; it can
be replaced or removed (the old object is deleted, the old URL stops working at once); nobody
can set or clear someone else's; and storage paths cannot be steered.

Storage is the local fallback in a temporary directory (GCS is faked in its own test); the
upload rate limiter is reset per test.
"""
import io
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image
from sqlalchemy import inspect
from starlette.testclient import TestClient

import app.main as m
from app import ratelimit
from app.db import SessionLocal, engine
from app.models import AuditLog, Organization, User
from app.security import create_access_token, hash_password
from app.services import avatars, livekit

URL_SHAPE = r"^/api/avatars/[0-9a-f-]{36}/[A-Za-z0-9_-]{32}\.webp$"


@pytest.fixture(autouse=True)
def local_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(livekit, "gcs_configured", lambda: False)
    monkeypatch.setattr(avatars, "LOCAL_ROOT", tmp_path)
    ratelimit._HITS.clear()
    yield tmp_path
    ratelimit._HITS.clear()


class World:
    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Avatars {uuid.uuid4().hex[:6]}", status="active")
            db.add(org)
            db.flush()
            self.org = org.id
            self.users = []
            self.admin = self._user(db, "org_admin", "Vihari Rao")
            self.member = self._user(db, "host", "Asha Member")
            org.owner_user_id = self.admin
            db.commit()
        finally:
            db.close()

    def _user(self, db, role, name):
        u = User(org_id=self.org, full_name=name, role=role, is_active=True,
                 email=f"av-{uuid.uuid4().hex[:10]}@example.com", username=f"av{uuid.uuid4().hex[:10]}",
                 password_hash=hash_password("x"), email_verified=True)
        db.add(u)
        db.flush()
        self.users.append(u.id)
        return u.id

    def client(self, user_id=None):
        db = SessionLocal()
        try:
            token = create_access_token(db.get(User, user_id or self.admin), remember=False, db=db)
        finally:
            db.close()
        c = TestClient(m.app)
        c.headers["Authorization"] = f"Bearer {token}"
        return c

    def avatar_of(self, user_id):
        db = SessionLocal()
        try:
            return db.get(User, user_id).avatar_url
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(AuditLog).filter(AuditLog.org_id == self.org).delete(synchronize_session=False)
            org = db.get(Organization, self.org)
            if org is not None:
                org.owner_user_id = None
                db.flush()
            db.query(User).filter(User.id.in_(self.users)).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id == self.org).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@pytest.fixture
def w():
    world = World()
    try:
        yield world
    finally:
        world.cleanup()


# ── image builders ────────────────────────────────────────────────────────────────────────

def image_bytes(fmt="PNG", size=(800, 600), color=(30, 120, 220), draw=None, **save):
    img = Image.new("RGB", size, color)
    if draw:
        draw(img)
    buf = io.BytesIO()
    img.save(buf, format=fmt, **save)
    return buf.getvalue()


def upload(client, data, content_type="image/png"):
    return client.post("/api/profile/avatar", content=data, headers={"Content-Type": content_type})


def served(client, url):
    return TestClient(m.app).get(url)          # an <img> sends no credentials


def decode(response):
    return Image.open(io.BytesIO(response.content))


# ── 1-4: no photo, upload, persistence ────────────────────────────────────────────────────

def test_1_an_account_without_a_photo_reports_none_everywhere(w):
    c = w.client()
    assert c.get("/api/auth/me").json()["avatar_url"] is None
    assert c.get("/api/organization/console-state").json()["user"]["avatar_url"] is None
    members = c.get("/api/organization/users", params={"page_size": 100}).json()["items"]
    assert {u["avatar_url"] for u in members if u["id"] in map(str, w.users)} == {None}


@pytest.mark.parametrize("fmt, content_type", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_2_a_png_jpg_or_webp_upload_is_stored_as_a_square_webp(w, fmt, content_type):
    c = w.client()
    r = upload(c, image_bytes(fmt), content_type)
    assert r.status_code == 200, r.text
    url = r.json()["avatar_url"]
    assert url is not None
    import re
    assert re.match(URL_SHAPE, url) and str(w.admin) in url
    image = served(c, url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/webp"
    assert image.headers["x-content-type-options"] == "nosniff"
    pic = decode(image)
    assert pic.format == "WEBP" and pic.size == (512, 512)


def test_3_the_photo_persists_on_the_account(w):
    url = upload(w.client(), image_bytes()).json()["avatar_url"]
    assert w.avatar_of(w.admin) == url
    # A fresh sign-in (another device, after a browser restart) gets the same photo.
    fresh = w.client()
    assert fresh.get("/api/auth/me").json()["avatar_url"] == url
    assert served(fresh, url).status_code == 200


def test_4_it_appears_wherever_the_account_is_shown(w):
    url = upload(w.client(), image_bytes()).json()["avatar_url"]
    viewer = w.client(w.member)                     # another member of the organization
    assert w.client().get("/api/organization/console-state").json()["user"]["avatar_url"] == url
    members = w.client().get("/api/organization/users", params={"page_size": 100}).json()["items"]
    assert next(u for u in members if u["id"] == str(w.admin))["avatar_url"] == url
    assert served(viewer, url).status_code == 200


# ── 5-7: processing ───────────────────────────────────────────────────────────────────────

def test_5_a_wide_photo_is_cropped_to_its_centre_never_stretched(w):
    def stripe(img):                                # a red band at the far left, outside the centre square
        for x in range(0, 150):
            for y in range(img.height):
                img.putpixel((x, y), (220, 20, 20))
    url = upload(w.client(), image_bytes(size=(1000, 500), color=(20, 20, 220), draw=stripe)).json()["avatar_url"]
    pic = decode(served(None, url)).convert("RGB")
    assert pic.size == (500, 500)                   # never upscaled past the source's short side
    left_edge = pic.getpixel((2, 250))
    assert left_edge[2] > 150 and left_edge[0] < 80, "a stretched image would carry the red band"


def test_6_a_small_photo_is_not_upscaled_and_a_tiny_one_is_refused(w):
    url = upload(w.client(), image_bytes(size=(200, 300))).json()["avatar_url"]
    assert decode(served(None, url)).size == (200, 200)
    r = upload(w.client(), image_bytes(size=(10, 10)))
    assert r.status_code == 422 and "too small" in r.json()["detail"]


def test_7_orientation_is_applied_and_metadata_is_stripped(w):
    # Stored landscape, with EXIF orientation 6 ("rotate 90° clockwise to display") and a blue
    # mark at the top-left of the centre square. Displayed upright, that mark is top-RIGHT.
    def mark(img):
        for x in range(100, 125):
            for y in range(0, 25):
                img.putpixel((x, y), (0, 0, 255))
    exif = Image.Exif()
    exif[0x0112] = 6                                # Orientation
    exif[0x010F] = "SecretCam"                      # Make — must not survive
    data = image_bytes("JPEG", size=(300, 100), color=(240, 240, 240), draw=mark, exif=exif.tobytes(), quality=95)
    url = upload(w.client(), data, "image/jpeg").json()["avatar_url"]
    pic = decode(served(None, url))
    assert pic.size == (100, 100)
    assert not pic.info.get("exif"), "EXIF must be stripped"
    rgb = pic.convert("RGB")
    top_right, top_left = rgb.getpixel((90, 8)), rgb.getpixel((8, 8))
    assert top_right[2] > 150 and top_right[0] < 120, "the mark must end up top-right (upright)"
    assert top_left[0] > 180, "top-left stays the light background"


# ── 8-9: change and remove ────────────────────────────────────────────────────────────────

def test_8_changing_the_photo_retires_the_old_one_at_once(w, local_storage):
    c = w.client()
    first = upload(c, image_bytes(color=(200, 10, 10))).json()["avatar_url"]
    second = upload(c, image_bytes(color=(10, 200, 10))).json()["avatar_url"]
    assert first != second and w.avatar_of(w.admin) == second
    assert served(c, first).status_code == 404
    assert served(c, second).status_code == 200
    files = list(local_storage.rglob("*.webp"))
    assert [f.name for f in files] == [second.rsplit("/", 1)[1]], "the replaced object is deleted"


def test_9_removing_the_photo_restores_initials_and_nothing_else_changes(w, local_storage):
    c = w.client()
    before = c.get("/api/auth/me").json()
    url = upload(c, image_bytes()).json()["avatar_url"]
    r = c.delete("/api/profile/avatar")
    assert r.status_code == 200
    after = r.json()
    assert after["avatar_url"] is None and w.avatar_of(w.admin) is None
    assert {k: after[k] for k in ("full_name", "email", "username", "role")} == \
           {k: before[k] for k in ("full_name", "email", "username", "role")}
    assert served(c, url).status_code == 404
    assert list(local_storage.rglob("*.webp")) == []
    assert c.delete("/api/profile/avatar").status_code == 200        # removing nothing is fine


# ── 10-13: what is refused ────────────────────────────────────────────────────────────────

GIF = image_bytes("GIF")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.mark.parametrize("data, content_type", [
    (GIF, "image/gif"),
    (GIF, "image/png"),                             # spoofed type: the bytes decide
    (SVG, "image/svg+xml"),
    (SVG, "image/png"),
    (b"<html><script>alert(1)</script></html>", "image/jpeg"),
    (b"MZ\x90\x00\x03 executable", "image/png"),
    (image_bytes("BMP"), "image/png"),
], ids=["gif", "gif-as-png", "svg", "svg-as-png", "html-as-jpeg", "exe-as-png", "bmp-as-png"])
def test_10_unsupported_or_spoofed_types_are_refused(w, data, content_type):
    r = upload(w.client(), data, content_type)
    assert r.status_code == 415, r.text
    assert r.json()["detail"] == "Upload a PNG, JPG or WebP image."
    assert w.avatar_of(w.admin) is None


def test_11_a_corrupt_image_with_a_valid_signature_is_refused(w):
    r = upload(w.client(), b"\x89PNG\r\n\x1a\n" + b"\x00" * 400)
    assert r.status_code == 422 and w.avatar_of(w.admin) is None


def test_12_oversized_uploads_are_refused(w):
    r = upload(w.client(), b"\x89PNG\r\n\x1a\n" + b"\x00" * (avatars.MAX_BYTES + 1))
    assert r.status_code == 413 and r.json()["detail"] == "That image is larger than 5 MB."
    # A tiny file declaring a gigantic canvas is refused before it is decoded.
    big = io.BytesIO()
    Image.new("1", (10_000, 5_000)).save(big, format="PNG")
    assert len(big.getvalue()) < avatars.MAX_BYTES
    r = upload(w.client(), big.getvalue())
    assert r.status_code == 422 and "dimensions" in r.json()["detail"]
    assert w.avatar_of(w.admin) is None


def test_13_signed_out_callers_can_neither_upload_nor_remove(w):
    anonymous = TestClient(m.app)
    assert upload(anonymous, image_bytes()).status_code in (401, 403)
    assert anonymous.delete("/api/profile/avatar").status_code in (401, 403)


# ── 14-16: permissions, paths, payloads ───────────────────────────────────────────────────

def test_14_nobody_can_set_or_clear_another_members_photo(w):
    admin, member = w.client(), w.client(w.member)
    mine = upload(member, image_bytes()).json()["avatar_url"]
    # The write routes take no user id: whatever is appended, the caller's OWN account changes.
    theirs = admin.post(f"/api/profile/avatar?user_id={w.member}", content=image_bytes(color=(1, 2, 3)),
                        headers={"Content-Type": "image/png"}).json()["avatar_url"]
    assert w.avatar_of(w.member) == mine and w.avatar_of(w.admin) == theirs
    admin.delete(f"/api/profile/avatar?user_id={w.member}")
    assert w.avatar_of(w.member) == mine and w.avatar_of(w.admin) is None
    # The organization's member editor cannot carry an avatar either.
    admin.patch(f"/api/organization/users/{w.member}", json={"avatar_url": "/api/avatars/x/evil.webp"})
    assert w.avatar_of(w.member) == mine


def test_15_storage_paths_cannot_be_steered(w, local_storage):
    url = upload(w.client(), image_bytes()).json()["avatar_url"]
    name = url.rsplit("/", 1)[1]
    files = list(local_storage.rglob("*"))
    assert all(f.resolve().is_relative_to(local_storage.resolve()) for f in files)
    assert {f.name for f in files if f.is_file()} == {name}
    for path in (
        f"/api/avatars/{w.admin}/..%2F..%2Fsecret.webp",
        f"/api/avatars/{w.admin}/../../app/config.py",
        f"/api/avatars/{w.admin}/{name[:-5]}.png",
        f"/api/avatars/{w.member}/{name}",           # someone else's id with my file
        f"/api/avatars/not-a-uuid/{name}",
    ):
        assert TestClient(m.app).get(path).status_code in (404, 422), path


def test_16_anything_smuggled_inside_the_file_does_not_survive(w):
    polyglot = image_bytes() + b"<script>alert(document.cookie)</script>"
    url = upload(w.client(), polyglot).json()["avatar_url"]
    body = served(None, url)
    assert b"<script" not in body.content
    assert "default-src 'none'" in body.headers["content-security-policy"]


# ── 17-19: storage backends and the schema ────────────────────────────────────────────────

class FakeBlob:
    def __init__(self, bucket, key):
        self.bucket, self.key = bucket, key

    def upload_from_string(self, data, content_type=None):
        self.bucket.objects[self.key] = (data, content_type)

    def download_as_bytes(self):
        return self.bucket.objects[self.key][0]

    def delete(self):
        self.bucket.objects.pop(self.key)


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def blob(self, key):
        return FakeBlob(self, key)


def test_17_with_gcs_configured_photos_go_to_the_bucket_under_avatars(w, monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr(avatars, "_gcs_bucket", lambda: bucket)
    c = w.client()
    first = upload(c, image_bytes()).json()["avatar_url"]
    (key, (data, content_type)), = bucket.objects.items()
    assert key == f"avatars/{w.admin}/{first.rsplit('/', 1)[1]}" and content_type == "image/webp"
    assert served(c, first).content == data
    second = upload(c, image_bytes(color=(9, 9, 9))).json()["avatar_url"]
    assert list(bucket.objects) == [f"avatars/{w.admin}/{second.rsplit('/', 1)[1]}"]


def test_18_production_without_gcs_refuses_rather_than_writing_to_local_disk(w, monkeypatch, local_storage):
    from app.config import settings
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(livekit, "_gcs_client", lambda: None)
    r = upload(w.client(), image_bytes())
    assert r.status_code == 503
    assert w.avatar_of(w.admin) is None and list(local_storage.rglob("*")) == []


def test_19_the_column_is_nullable_and_existing_accounts_keep_working(w):
    column = next(c for c in inspect(engine).get_columns("users") if c["name"] == "avatar_url")
    assert column["nullable"] is True
    db = SessionLocal()
    try:
        legacy = User(org_id=w.org, full_name="Legacy", role="host", is_active=True,
                      email=f"lg-{uuid.uuid4().hex[:8]}@example.com", username=f"lg{uuid.uuid4().hex[:8]}",
                      password_hash=hash_password("x"), email_verified=True,
                      created_at=datetime.now(timezone.utc))
        db.add(legacy)
        db.commit()
        w.users.append(legacy.id)
        assert legacy.avatar_url is None
    finally:
        db.close()
    assert w.client(legacy.id).get("/api/auth/me").json()["avatar_url"] is None


def test_20_only_our_own_avatar_paths_are_ever_deleted(local_storage):
    bystander = local_storage / "shared" / "default-avatar.webp"
    bystander.parent.mkdir(parents=True)
    bystander.write_bytes(b"shared")
    for url in ("/static/default-avatar.webp", "https://cdn.example.com/a.webp",
                "/api/avatars/../../shared/default-avatar.webp", None, ""):
        assert avatars.delete(url) is False
    assert bystander.exists()
