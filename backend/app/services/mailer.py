import logging
import smtplib
import uuid
from email.message import EmailMessage
from pathlib import Path

from ..config import settings
from ..timeutil import now_local

log = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> bool:
    """Send via SMTP, or, when SMTP_HOST is not set, save the message to MAIL_OUTBOX_DIR. Never raises."""
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user or "expense-tracker@localhost"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body, cte="8bit")
    try:
        if not settings.smtp_host:
            outbox = Path(settings.mail_outbox_dir)
            outbox.mkdir(parents=True, exist_ok=True)
            path = outbox / f"{now_local():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}-{to.replace('@', '_at_')}.eml"
            path.write_bytes(bytes(msg))
            log.warning("SMTP not configured: email to %s saved to %s", to, path)
            return True
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return True
    except Exception:
        log.exception("Failed to send email to %s", to)
        return False
