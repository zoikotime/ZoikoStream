"""The current HTTP request's context variables: request reference and client address.

1. REQUEST ID (for audit receipts):
main.request_reference stamps every API request with a server-generated id, sets it here for
the duration of the request, and echoes it to the caller as ``X-Request-Id``. AuditLog rows
written while that request is being handled pick it up as their ``correlation_id`` (see
models/audit_log), so the receipt a console shows and the response the operator received carry
the same reference.

Server-generated only. A caller-supplied ``X-Request-Id`` is never adopted: an audit reference a
client can choose is one it can make collide with, or impersonate, someone else's receipt.

Outside a request (socket actions, background jobs) there is no reference and this returns
None — the writer then keeps whatever correlation id it was handed, or none.

2. CLIENT IP (for audit rows and forensics):
A context variable is set once per request by the middleware in main.py and read by
crud.create_audit_log when no address was passed explicitly, so a call site captures the
address by doing nothing at all. Work with no request behind it — background tickers,
scheduler callbacks — reads None, which is correct: those events have no client address, and
inventing one would be worse than the dash.

ContextVar rather than a module global because it is per-task: concurrent requests each see
their own value, and nothing leaks between them.
"""
import secrets
from contextvars import ContextVar

# ── Request ID ────────────────────────────────────────────────────────────────

_REQUEST_ID: ContextVar[str | None] = ContextVar("zk_request_id", default=None)


def new_request_id() -> str:
    return f"req_{secrets.token_hex(12)}"


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def bind_request_id(value: str):
    """Set the reference for the current context; returns the token to reset it with."""
    return _REQUEST_ID.set(value)


def reset_request_id(token) -> None:
    _REQUEST_ID.reset(token)


# ── Client IP ─────────────────────────────────────────────────────────────────

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
