from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

from .config import NotificationConfig, Target
from .resy_client import Slot

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(
        self,
        notification_config: NotificationConfig,
        smtp_password: str,
    ) -> None:
        self.cfg = notification_config
        self.smtp_password = smtp_password

    def notify_success(self, target: Target, slot: Slot, confirmation: dict) -> None:
        """Send email confirming a successful booking."""
        resy_id = confirmation.get("resy_token") or confirmation.get("reservation_id") or "N/A"
        subject = f"Reservation booked: {target.venue_name} on {target.date}"
        body = (
            f"Your reservation has been booked!\n\n"
            f"Venue:        {target.venue_name}\n"
            f"Date:         {target.date}\n"
            f"Time:         {slot.start_time.strftime('%H:%M')}\n"
            f"Party size:   {target.party_size}\n"
            f"Confirmation: {resy_id}\n"
        )
        self._send_email(subject, body)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_email(self, subject: str, body: str) -> None:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self.cfg.email.from_address
        msg["To"] = self.cfg.email.to_address

        try:
            with smtplib.SMTP(self.cfg.email.smtp_server, self.cfg.email.smtp_port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(self.cfg.email.from_address, self.smtp_password)
                smtp.sendmail(
                    self.cfg.email.from_address,
                    [self.cfg.email.to_address],
                    msg.as_string(),
                )
            logger.info("Email notification sent to %s", self.cfg.email.to_address)
        except Exception as exc:
            logger.error("Failed to send email: %s", exc)
            raise

