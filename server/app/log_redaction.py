"""Keep viewer credentials out of the server's logs.

The browser WebSocket API cannot set headers, so the live socket carries its credential in the
query string (`?token=` / `?reg=` / `?link=`), and GET /events/{id}/watch takes `reg`/`link` as
query parameters too. Uvicorn logs the full path INCLUDING the query string: every HTTP access
line (logger "uvicorn.access") and every WebSocket accept/reject line (logger "uvicorn.error").
So a login JWT, a registration token or an access-link secret ended up in plain text in the
container log, and from there in whatever ships that log.

This filter rewrites the value of those parameters to "[redacted]" before the record is
formatted. The request itself is untouched: only the log line changes. Installed once at app
import (main.py). Uvicorn configures its loggers before importing the app, so the filters
attach to the loggers uvicorn actually writes through, in every worker process.
"""
from __future__ import annotations

import logging
import re

# Query parameters whose VALUE is a credential anywhere in this API.
SENSITIVE_PARAMS = ("token", "reg", "link", "invite", "secret")
_PATTERN = re.compile(r"([?&](?:%s)=)[^&#\s\"']*" % "|".join(SENSITIVE_PARAMS), re.IGNORECASE)
REDACTED = "[redacted]"


def redact(text: str) -> str:
    return _PATTERN.sub(r"\1" + REDACTED, text)


def _clean(value):
    return redact(value) if isinstance(value, str) else value


class RedactCredentials(logging.Filter):
    """Scrubs credential query parameters from a record's message and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_clean(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _clean(v) for k, v in record.args.items()}
        return True


# "uvicorn.access": one line per HTTP request. "uvicorn.error": the WebSocket accepted/rejected
# lines and connection errors. "uvicorn": the parent, in case a deployment logs through it.
LOGGERS = ("uvicorn.access", "uvicorn.error", "uvicorn")


def install() -> None:
    """Attach the filter to uvicorn's loggers. Safe to call more than once."""
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactCredentials) for f in logger.filters):
            logger.addFilter(RedactCredentials())
