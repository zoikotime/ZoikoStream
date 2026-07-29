from app.services.email import send_email


def send_event_created(user_email, event):

    html = f"""
    <h2>{event.title}</h2>

    <p>Your event has been created successfully.</p>

    <p>Status: {event.status}</p>
    """

    send_email(
        user_email,
        "Event Created Successfully",
        html,
    )