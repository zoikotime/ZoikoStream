"""Public contact form — full path: POST /api/contact

Replaces a frontend mail-protocol link, which handed the submission to whatever mail handler the
visitor's operating system had registered. On a domain whose mail is hosted externally that
dropped the operator onto the mail host's sign-in page — from inside the product it looked like
ZoikoStream had redirected them somewhere third-party, and the enquiry was never actually sent
unless they finished composing it by hand. The submission now stays on our own API.

Unauthenticated by necessity (prospects have no account), so the protections are: a per-IP rate
limit, a schema that bounds every field and forbids unknown ones, and a destination that comes
from configuration rather than from the request.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, status

from ..email import send_contact_message_email
from ..ratelimit import rate_limit
from ..schemas.contact import ContactMessageIn, ContactMessageOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/contact", tags=["contact"])

# Per-IP budget on an unauthenticated surface, matching auth.py's approach. Generous for a
# person who mistypes their address and resubmits; tight enough that the form is not a
# convenient way to pump mail through our provider.
_CONTACT_LIMIT = rate_limit("contact", limit=5, window=300.0)


@router.post("", response_model=ContactMessageOut, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[_CONTACT_LIMIT])
def submit_contact_message(data: ContactMessageIn, background: BackgroundTasks) -> ContactMessageOut:
    """Accept an enquiry and hand delivery to the existing Resend service.

    202, not 200: we have accepted the message, and delivery happens after the response. The
    caller is told what is true — that we received it — rather than a claim about the mail
    provider we cannot honestly make yet.

    Delivery runs in the background so a slow mail provider cannot hold the request open, and
    `_send` already swallows and logs provider failures, so a mail outage cannot turn a
    successfully received enquiry into a 500 for the visitor.
    """
    # The recipient is settings.CONTACT_EMAIL, resolved inside the email service. Nothing from
    # `data` selects a destination — the schema forbids such a field even being present.
    background.add_task(
        send_contact_message_email,
        first=data.first, last=data.last, email=str(data.email), org=data.org,
        country=data.country, topic=data.topic, message=data.message,
    )
    # Logged without the message body: an enquiry is someone's business information, and the
    # log is the wrong place for it.
    log.info("contact enquiry accepted topic=%s org=%s", data.topic, data.org or "-")
    return ContactMessageOut()
