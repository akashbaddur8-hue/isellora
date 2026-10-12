
import os
import smtplib
import ssl
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

sender = os.getenv("MAIL_USERNAME", "").strip()
password = os.getenv("MAIL_PASSWORD", "").strip().replace(" ", "")

if not sender or not password:
    raise SystemExit("Check MAIL_USERNAME and MAIL_PASSWORD in .env")

def login_response(challenge=None):
    if challenge is None:
        return sender
    return password

try:
    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        timeout=30,
        context=ssl.create_default_context()
    ) as smtp:
        smtp.ehlo()
        print("Testing Gmail LOGIN authentication...")

        smtp.auth(
            "LOGIN",
            login_response,
            initial_response_ok=False
        )

        print("GMAIL LOGIN SUCCESS!")

except Exception as e:
    print("ERROR TYPE:", type(e).__name__)
    print("ERROR DETAILS:", str(e))
