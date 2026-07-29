import resend

from app.config import settings


resend.api_key = settings.RESEND_API_KEY


def send_email(
    to: str,
    subject: str,
    html: str,
):
    """
    Send an email using Resend.
    """

    if not settings.RESEND_API_KEY:
        print("RESEND_API_KEY not configured")
        return

    try:
        resend.Emails.send(
            {
                "from": settings.MAIL_FROM,
                "to": [to],
                "subject": subject,
                "html": html,
            }
        )

    except Exception as e:
        print(f"Email sending failed: {e}")