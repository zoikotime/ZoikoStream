"""Schema re-exports.

Explicit names, not `from .x import *`. Listing names means a future collision is a
visible conflict instead of a silent one.

Modules with a crowded namespace of their own (admin, event, organization) are imported
from their module directly, so they are deliberately not re-exported here.
"""

from .auth import (
    ChangeRecoveryContactIn,
    ConfirmRecoveryContactIn,
    ForgotPasswordIn,
    LoginIn,
    RegisterIn,
    RegistrationPendingOut,
    ResendVerificationIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
    VerificationResultOut,
    VerifyEmailIn,
    VerifyOtpIn,
)

__all__ = [
    "ChangeRecoveryContactIn",
    "ConfirmRecoveryContactIn",
    "ForgotPasswordIn",
    "LoginIn",
    "RegisterIn",
    "RegistrationPendingOut",
    "ResendVerificationIn",
    "ResetPasswordIn",
    "TokenOut",
    "UserOut",
    "VerificationResultOut",
    "VerifyEmailIn",
    "VerifyOtpIn",
]
