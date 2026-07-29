from app.services.email import send_email

send_email(
    to="boinivamshi.3686@gmail.com",  # Replace with your email
    subject="ZoikoStream Test",
    html="""
    <h2>Hello!</h2>
    <p>This email is sent from ZoikoStream using Resend.</p>
    """,
)

print("Email request sent.")