from datetime import datetime

from app.services.email import send_email


def send_event_created_email(
    to: str,
    organizer_name: str,
    event_title: str,
    start_time: datetime | None,
    status: str,
):
    """
    Send confirmation email when an event is created.
    """

    start = (
        start_time.strftime("%d %b %Y, %I:%M %p")
        if start_time
        else "Not Scheduled"
    )

    subject = f"Event Created Successfully - {event_title}"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
    </head>

    <body style="margin:0;padding:0;background:#f4f6f8;font-family:Arial,Helvetica,sans-serif;">

    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:40px 0;">

    <tr>
    <td align="center">

    <table width="620" cellpadding="0" cellspacing="0"
    style="background:#ffffff;border-radius:10px;overflow:hidden;
    box-shadow:0 3px 10px rgba(0,0,0,.08);">

    <!-- Header -->
    <tr>
    <td align="center"
    style="background:#2563eb;padding:28px;color:white;">

    <h1 style="margin:0;font-size:28px;">
    🎉 ZoikoStream
    </h1>

    <p style="margin-top:10px;font-size:18px;">
    Event Created Successfully
    </p>

    </td>
    </tr>

    <!-- Body -->

    <tr>
    <td style="padding:35px;">

    <p style="font-size:16px;">
    Hello <strong>{organizer_name}</strong>,
    </p>

    <p style="font-size:15px;line-height:1.7;color:#555;">
    Your event has been successfully created on
    <strong>ZoikoStream</strong>.
    </p>

    <br>

    <table width="100%"
    style="border-collapse:collapse;border:1px solid #e5e7eb;">

    <tr style="background:#f8fafc;">
    <td style="padding:12px;border:1px solid #e5e7eb;">
    <strong>Event</strong>
    </td>

    <td style="padding:12px;border:1px solid #e5e7eb;">
    {event_title}
    </td>
    </tr>

    <tr>
    <td style="padding:12px;border:1px solid #e5e7eb;">
    <strong>Start Time</strong>
    </td>

    <td style="padding:12px;border:1px solid #e5e7eb;">
    {start}
    </td>
    </tr>

    <tr style="background:#f8fafc;">
    <td style="padding:12px;border:1px solid #e5e7eb;">
    <strong>Status</strong>
    </td>

    <td style="padding:12px;border:1px solid #e5e7eb;">
    <span style="
    background:#fff3cd;
    color:#856404;
    padding:6px 12px;
    border-radius:20px;
    font-size:13px;
    font-weight:bold;">
    {status.title()}
    </span>
    </td>
    </tr>

    </table>

    <br>

    <p style="font-size:15px;color:#555;">
    You can now:
    </p>

    <ul style="color:#555;line-height:1.8;">
    <li>Invite Hosts</li>
    <li>Assign Moderators</li>
    <li>Add Speakers</li>
    <li>Configure Streaming Settings</li>
    <li>Publish your event when you're ready</li>
    </ul>

    <br>

    <div style="
    background:#eef6ff;
    padding:18px;
    border-left:5px solid #2563eb;
    border-radius:6px;
    font-size:14px;
    color:#444;">

    💡 <strong>Tip:</strong><br>

    Your event is currently in
    <strong>Draft</strong> mode.
    After reviewing the settings, you can publish it for attendees.

    </div>

    <br><br>

    <p style="font-size:15px;">
    Thank you,
    </p>

    <p style="font-size:16px;">
    <strong>ZoikoStream Team</strong>
    </p>

    </td>
    </tr>

    <!-- Footer -->

    <tr>
    <td align="center"
    style="background:#f8fafc;padding:18px;color:#888;font-size:13px;">

    © 2026 ZoikoStream. All rights reserved.

    </td>
    </tr>

    </table>

    </td>
    </tr>

    </table>

    </body>
    </html>
    """

    return send_email(
        to=to,
        subject=subject,
        html=html,
    )