"""BUG-011: the app returned to sign-in about a minute after signing in.

Nothing in the client or in auth_sessions does that at the 30-minute default. It is what an
idle limit of ~1 minute does, because activity is only reported once a minute by the client
and recorded at most once per SESSION_ACTIVITY_MIN_INTERVAL_SECONDS: such a session expires
while it is being used. SESSION_IDLE_TIMEOUT_MINUTES is therefore never taken below
MIN_SESSION_IDLE_MINUTES.
"""
import pytest

from app.config import MIN_SESSION_IDLE_MINUTES, Settings


def _settings(**env):
    return Settings(DATABASE_URL="postgresql://u:p@localhost/x", **env)


@pytest.mark.parametrize("minutes", [0, 1, 2, MIN_SESSION_IDLE_MINUTES - 1, -5])
def test_a_limit_shorter_than_activity_reporting_is_raised_to_the_floor(minutes, caplog):
    assert _settings(SESSION_IDLE_TIMEOUT_MINUTES=minutes).SESSION_IDLE_TIMEOUT_MINUTES == MIN_SESSION_IDLE_MINUTES
    assert "below the" in caplog.text


@pytest.mark.parametrize("minutes", [MIN_SESSION_IDLE_MINUTES, 15, 30, 240])
def test_a_workable_limit_is_kept_as_configured(minutes, caplog):
    assert _settings(SESSION_IDLE_TIMEOUT_MINUTES=minutes).SESSION_IDLE_TIMEOUT_MINUTES == minutes
    assert "below the" not in caplog.text


def test_the_default_is_unchanged():
    assert _settings().SESSION_IDLE_TIMEOUT_MINUTES == 30


def test_the_floor_outlasts_the_activity_write_interval():
    s = _settings()
    assert MIN_SESSION_IDLE_MINUTES * 60 > 2 * s.SESSION_ACTIVITY_MIN_INTERVAL_SECONDS
