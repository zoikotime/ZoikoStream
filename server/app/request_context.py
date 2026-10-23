"""The current HTTP request's reference, for audit receipts.

main.request_reference stamps every API request with a server-generated id, sets it here for
the duration of the request, and echoes it to the caller as ``X-Request-Id``. AuditLog rows
written while that request is being handled pick it up as their ``correlation_id`` (see
models/audit_log), so the receipt a console shows and the response the operator received carry
the same reference.

Server-generated only. A caller-supplied ``X-Request-Id`` is never adopted: an audit reference a
client can choose is one it can make collide with, or impersonate, someone else's receipt.

Outside a request (socket actions, background jobs) there is no reference and this returns
None — the writer then keeps whatever correlation id it was handed, or none.
"""
import secrets
from contextvars import ContextVar

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
