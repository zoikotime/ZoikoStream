"""Public event URLs, built in one place.

Every link that sends an attendee to an event — emailed invitations, registration
confirmations, host-issued access links, replay notices, the console's "Copy viewer link" —
is built here, so an organization's ACTIVE custom domain is used everywhere at once and
nowhere else:

    active custom domain  -> https://events.customer.com/events/<id>/watch
    anything else         -> {APP_URL}/events/<id>/watch    (pending, failed, disabled, none)

"Active" means DNS proven, certificate live and the HTTPS probe passed
(services/custom_domains.py); a domain that is merely saved never appears in a link.
Management links (the organization console, billing, settings) always stay on the platform.
"""
from __future__ import annotations

from ..config import settings


def platform_base_url() -> str:
    return settings.APP_URL.rstrip("/")


def custom_domain_base_url(org) -> str | None:
    if (org is not None and org.domain and getattr(org, "domain_status", None) == "active"
            and getattr(org, "custom_domain_enabled", False)):
        return f"https://{org.domain}"
    return None


def public_base_url(org) -> str:
    return custom_domain_base_url(org) or platform_base_url()


def event_watch_url(event_id, org=None) -> str:
    return f"{public_base_url(org)}/events/{event_id}/watch"
