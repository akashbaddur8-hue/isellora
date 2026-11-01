"""
Email delivery service for iSellora.
Handles sending transactional emails including password reset notifications via SMTP,
with support for TLS/SSL, error handling, testing outbox, and secure development outbox logging.
"""
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import os
import smtplib
import socket
from flask import current_app

logger = logging.getLogger(__name__)

# In-memory outbox for automated test inspection
TEST_OUTBOX = []


def is_email_configured() -> bool:
    """
    Checks whether valid SMTP server configuration is present in app config.
    Requires server, and requires credentials if the mail server is an external host.
    """
    server = (current_app.config.get("MAIL_SERVER") or "").strip().lower()
    if not server or server in ("smtp.example.com", "placeholder", "localhost.placeholder", "none"):
        return False
    username = (current_app.config.get("MAIL_USERNAME") or "").strip()
    password = (current_app.config.get("MAIL_PASSWORD") or "").strip()
    external_domains = ("gmail", "google", "outlook", "office365", "sendgrid", "mailgun", "aws", "ses")
    if any(domain in server for domain in external_domains) or username:
        return bool(username and password)
    return True


def clear_test_outbox():
    """Clears the in-memory test outbox (used in unit test fixtures)."""
    TEST_OUTBOX.clear()


def build_password_reset_email_body(user_name: str, reset_url: str, expiry_minutes: int = 15) -> tuple[str, str]:
    """
    Constructs both plain text and HTML versions of the password reset email.
    Never exposes passwords, tokens in query strings, or sensitive data.
    """
    display_name = (user_name or "Valued Member").strip()

    # Plain text version
    text_content = f"""Hello {display_name},

We received a request to reset your password for your iSellora account.

To choose a new password, click or paste the following link into your browser:
{reset_url}

This link is valid for {expiry_minutes} minutes and can only be used once.

Security Notice:
If you did not request a password reset, no action is needed and your account is secure.
Please do not forward or share this link with anyone.

Best regards,
The iSellora Team
"""

    # Responsive HTML version with iSellora purple-blue branding
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reset Your iSellora Password</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            background-color: #f8fafc;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #0f172a;
            -webkit-font-smoothing: antialiased;
        }}
        .email-wrapper {{
            width: 100%;
            background-color: #f8fafc;
            padding: 40px 16px;
        }}
        .email-container {{
            max-width: 540px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 14px;
            border: 1px solid #e2e8f0;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
        }}
        .email-header {{
            background: linear-gradient(135deg, #4f46e5 0%, #3b82f6 100%);
            padding: 32px 30px;
            text-align: center;
        }}
        .brand-title {{
            color: #ffffff;
            font-size: 24px;
            font-weight: 800;
            letter-spacing: -0.5px;
            margin: 0;
        }}
        .brand-subtitle {{
            color: #e0e7ff;
            font-size: 13px;
            margin-top: 4px;
        }}
        .email-body {{
            padding: 36px 32px;
        }}
        .greeting {{
            font-size: 18px;
            font-weight: 700;
            color: #0f172a;
            margin-top: 0;
            margin-bottom: 16px;
        }}
        .body-text {{
            font-size: 15px;
            line-height: 1.6;
            color: #334155;
            margin-bottom: 24px;
        }}
        .btn-wrapper {{
            text-align: center;
            margin: 32px 0;
        }}
        .btn-primary {{
            display: inline-block;
            background-color: #4f46e5;
            color: #ffffff !important;
            font-size: 15px;
            font-weight: 700;
            text-decoration: none;
            padding: 14px 32px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(79, 70, 229, 0.25);
        }}
        .fallback-box {{
            background: #f1f5f9;
            border-radius: 8px;
            padding: 14px;
            font-size: 12px;
            color: #64748b;
            word-break: break-all;
            margin-top: 24px;
        }}
        .fallback-box a {{
            color: #4f46e5;
            text-decoration: none;
        }}
        .security-notice {{
            background: #fef2f2;
            border-left: 4px solid #ef4444;
            padding: 12px 16px;
            border-radius: 4px;
            font-size: 13px;
            color: #991b1b;
            margin-top: 28px;
        }}
        .email-footer {{
            padding: 24px 32px;
            background-color: #f8fafc;
            border-top: 1px solid #e2e8f0;
            text-align: center;
            font-size: 12px;
            color: #94a3b8;
        }}
    </style>
</head>
<body>
    <div class="email-wrapper">
        <div class="email-container">
            <div class="email-header">
                <h1 class="brand-title">iSellora</h1>
                <div class="brand-subtitle">Smartphones Verified & Delivered</div>
            </div>
            <div class="email-body">
                <h2 class="greeting">Hello {display_name},</h2>
                <p class="body-text">
                    We received a request to reset your password for your <strong>iSellora</strong> account.
                    Click the button below to choose a new, secure password.
                </p>
                <div class="btn-wrapper">
                    <a href="{reset_url}" class="btn-primary" target="_blank" rel="noopener noreferrer">Reset My Password</a>
                </div>
                <p class="body-text" style="font-size: 13px; color: #64748b; margin-bottom: 12px;">
                    This link will expire in <strong>{expiry_minutes} minutes</strong> and is strictly single-use.
                </p>
                <div class="fallback-box">
                    If the button above does not work, copy and paste this secure link into your browser:<br>
                    <a href="{reset_url}">{reset_url}</a>
                </div>
                <div class="security-notice">
                    <strong>Didn't request this?</strong> If you did not ask to reset your password, you can safely ignore this email. Your current password remains protected and active.
                </div>
            </div>
            <div class="email-footer">
                &copy; iSellora Marketplace. This is an automated transactional security email; please do not reply directly.
            </div>
        </div>
    </div>
</body>
</html>"""

    return text_content, html_content


def send_password_reset_email(to_email: str, user_name: str, reset_url: str) -> tuple[bool, str]:
    """
    Sends the password reset email to the specified user email.
    Supports:
    - In-memory inspection during pytest (current_app.config['TESTING'])
    - Local file/console capture during development when SMTP is not configured
    - Real authenticated SMTP over TLS or SSL when configured
    - Comprehensive error handling without credential leakage
    Returns (success: bool, user_facing_message_or_error: str).
    """
    expiry_minutes = current_app.config.get("PASSWORD_RESET_EXPIRY_MINUTES", 15)
    subject = "Reset Your iSellora Password"
    text_body, html_body = build_password_reset_email_body(user_name, reset_url, expiry_minutes)

    # 1. Automated Testing Mode (Mock outbox)
    if current_app.config.get("TESTING"):
        TEST_OUTBOX.append({
            "to": to_email,
            "name": user_name,
            "subject": subject,
            "reset_url": reset_url,
            "text": text_body,
            "html": html_body,
            "sent_at": datetime.now(timezone.utc).isoformat()
        })
        current_app.logger.debug("Captured password reset email for %s in TEST_OUTBOX", to_email)
        return True, "Email recorded in testing outbox."

    # 2. Check if SMTP configuration is present
    server = (current_app.config.get("MAIL_SERVER") or "").strip()
    port = int(current_app.config.get("MAIL_PORT", 587))
    use_tls = current_app.config.get("MAIL_USE_TLS", True)
    use_ssl = current_app.config.get("MAIL_USE_SSL", False)
    username = (current_app.config.get("MAIL_USERNAME") or "").strip()
    password = (current_app.config.get("MAIL_PASSWORD") or "").strip()
    sender = (current_app.config.get("MAIL_DEFAULT_SENDER") or username or "no-reply@isellora.local").strip()

    if not is_email_configured():
        # Development Mode Fallback: Capture email locally and log clearly to developer
        if current_app.config.get("DEBUG") or current_app.env == "development":
            instance_dir = current_app.instance_path
            os.makedirs(instance_dir, exist_ok=True)
            log_path = os.path.join(instance_dir, "dev_email_outbox.log")
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n{'='*70}\n")
                    f.write(f"TIME: {datetime.now(timezone.utc).isoformat()}\n")
                    f.write(f"TO: {to_email} ({user_name})\n")
                    f.write(f"SUBJECT: {subject}\n")
                    f.write(f"RESET LINK: {reset_url}\n")
                    f.write(f"NOTE: MAIL_SERVER is not configured. Configure in backend/.env for real SMTP delivery.\n")
                    f.write(f"{'='*70}\n")
            except Exception as e:
                current_app.logger.warning("Could not write to dev_email_outbox.log: %s", e)

            current_app.logger.warning(
                "[DEV MODE EMAIL] Password reset email for '%s' was captured in: %s\n"
                "  >>> Reset Link: %s\n"
                "  >>> To enable live delivery: configure MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD in backend/.env",
                to_email, log_path, reset_url
            )
            return True, "Development mode: reset link captured in local development outbox."

        # Production Mode: SMTP must be configured
        current_app.logger.error(
            "Cannot send password reset email: MAIL_SERVER is not configured in production environment."
        )
        return False, "Email service is not configured on this server."

    # 3. Live SMTP Transmission
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        timeout = 10
        if use_ssl:
            smtp_client = smtplib.SMTP_SSL(server, port, timeout=timeout)
        else:
            smtp_client = smtplib.SMTP(server, port, timeout=timeout)

        with smtp_client:
            if not use_ssl and use_tls:
                smtp_client.starttls()
            if username and password:
                smtp_client.login(username, password)
            smtp_client.send_message(msg)

        current_app.logger.info("Password reset email sent successfully to %s", to_email)
        return True, "Reset instructions sent to your email."

    except (smtplib.SMTPException, socket.error, socket.timeout, OSError) as err:
        # Never log credentials or user passwords
        current_app.logger.error(
            "SMTP transmission failure while sending password reset to %s: %s (%s)",
            to_email, err.__class__.__name__, str(err)
        )
        return False, "Failed to connect to mail server. Please try again later."
    except Exception as err:
        current_app.logger.error(
            "Unexpected error sending password reset email to %s: %s",
            to_email, err.__class__.__name__
        )
        return False, "An unexpected error occurred while sending the email."
