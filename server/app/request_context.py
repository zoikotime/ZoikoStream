"""The current HTTP request's client address, available to code that never sees the request.

Audit events are written from 59 places across nine modules. Exactly one of them — the admin
router's private `_audit()` helper — passed an IP, so every audit row created anywhere else
stored NULL and the console's IP column showed a dash for it. Threading a Request through
nine modules to fix that would mean touching every call site and would still be forgotten by
the next one.

A context variable is set once per request by the middleware in main.py and read by
crud.create_audit_log when no address was passed explicitly, so a call site captures the
address by doing nothing at all. Work with no request behind it — background tickers,
scheduler callbacks — reads None, which is correct: those events have no client address, and
inventing one would be worse than the dash.

ContextVar rather than a module global because it is per-task: concurrent requests each see
their own value, and nothing leaks between them.
"""
from contextvars import ContextVar

_client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)


def set_client_ip(ip: str | None):
    """Bind the address for this request. Returns the token to reset with."""
    return _client_ip.set(ip)


def get_client_ip() -> str | None:
    """The bound address, or None outside a request."""
    return _client_ip.get()


def reset_client_ip(token) -> None:
    """Unbind, so a pooled worker cannot carry one request's address into the next."""
    _client_ip.reset(token)
