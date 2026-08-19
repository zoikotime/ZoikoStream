"""Self-check for the outbound webhook signing contract (services/webhooks.py::sign).
Pure logic, no DB, no network. Run: `python test_webhooks.py` (or pytest)."""
import hmac

from app.services import webhooks as w


def test_sign_matches_manual_hmac():
    secret, ts, body = "whsec_test123", 1750000000, b'{"event":"session.started"}'
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, "sha256").hexdigest()
    assert w.sign(secret, ts, body) == expected


def test_sign_is_deterministic_and_body_sensitive():
    secret, ts = "whsec_test123", 1750000000
    sig_a = w.sign(secret, ts, b"body-a")
    sig_b = w.sign(secret, ts, b"body-b")
    assert sig_a != sig_b
    assert w.sign(secret, ts, b"body-a") == sig_a  # same inputs -> same signature


def test_sign_is_timestamp_sensitive():
    """A replayed body at a different timestamp must not verify — this is what lets a
    receiver's replay-window check (Webhooks.jsx's VERIFY_SAMPLE) actually work."""
    secret, body = "whsec_test123", b"same-body"
    assert w.sign(secret, 1, body) != w.sign(secret, 2, body)


def test_sign_is_secret_sensitive():
    ts, body = 1750000000, b"same-body"
    assert w.sign("secret-one", ts, body) != w.sign("secret-two", ts, body)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("\nAll webhook checks passed.")
