"""Schema re-exports.

Explicit names, not `from .x import *`. The star form previously let schemas/channel.py
silently shadow a differently-validated ChannelCreate declared in schemas/auth.py — the
duplicate was invisible at the import site and the losing definition was dead code.
Listing names means a future collision is a visible conflict instead of a silent one.

Modules with a crowded namespace of their own (admin, event, organization) are imported
from their module directly, so they are deliberately not re-exported here.
"""

from .auth import (
    ForgotPasswordIn,
    LoginIn,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
    VerifyOtpIn,
)
from .channel import ChannelCreate, ChannelResponse
from .stream import (
    StreamCreate,
    StreamListItem,
    StreamListResponse,
    StreamResponse,
    StreamUpdate,
)

__all__ = [
    "ChannelCreate",
    "ChannelResponse",
    "ForgotPasswordIn",
    "LoginIn",
    "RegisterIn",
    "ResetPasswordIn",
    "StreamCreate",
    "StreamListItem",
    "StreamListResponse",
    "StreamResponse",
    "StreamUpdate",
    "TokenOut",
    "UserOut",
    "VerifyOtpIn",
]
