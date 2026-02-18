"""Tests for bot/notifier.py — email notification logic."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from bot.config import EmailConfig, NotificationConfig, Target
from bot.notifier import Notifier
from bot.resy_client import Slot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_notification_config() -> NotificationConfig:
    return NotificationConfig(
        email=EmailConfig(
            smtp_server="smtp.gmail.com",
            smtp_port=587,
            from_address="from@example.com",
            to_address="to@example.com",
        ),
    )


def make_target() -> Target:
    return Target(
        venue_id=5286,
        venue_name="Carbone",
        date="2026-03-15",
        party_size=2,
        time_preferences=["19:00"],
    )


def make_slot() -> Slot:
    return Slot(config_id="cfg-1", start_time=datetime(2026, 3, 15, 19, 0))


def make_notifier(cfg=None) -> Notifier:
    cfg = cfg or make_notification_config()
    return Notifier(notification_config=cfg, smtp_password="secret")


def mock_smtp_context() -> tuple[MagicMock, MagicMock]:
    """Returns (smtp_class_mock, smtp_instance_mock)."""
    smtp_instance = MagicMock()
    smtp_class = MagicMock()
    smtp_class.return_value.__enter__ = MagicMock(return_value=smtp_instance)
    smtp_class.return_value.__exit__ = MagicMock(return_value=False)
    return smtp_class, smtp_instance


# ---------------------------------------------------------------------------
# notify_success — email content
# ---------------------------------------------------------------------------

def test_notify_success_sends_email():
    notifier = make_notifier()
    smtp_class, smtp_instance = mock_smtp_context()

    with patch("bot.notifier.smtplib.SMTP", smtp_class):
        notifier.notify_success(make_target(), make_slot(), {"resy_token": "RES-999"})

    smtp_instance.starttls.assert_called_once()
    smtp_instance.login.assert_called_once_with("from@example.com", "secret")
    sendmail_args = smtp_instance.sendmail.call_args[0]
    assert sendmail_args[0] == "from@example.com"
    assert sendmail_args[1] == ["to@example.com"]
    body = sendmail_args[2]
    assert "Carbone" in body
    assert "2026-03-15" in body
    assert "19:00" in body
    assert "RES-999" in body


def test_notify_success_fallback_confirmation_key():
    """Uses reservation_id when resy_token is absent."""
    notifier = make_notifier()
    smtp_class, smtp_instance = mock_smtp_context()

    with patch("bot.notifier.smtplib.SMTP", smtp_class):
        notifier.notify_success(make_target(), make_slot(), {"reservation_id": "ALT-777"})

    body = smtp_instance.sendmail.call_args[0][2]
    assert "ALT-777" in body


def test_notify_success_uses_na_when_no_confirmation_id():
    notifier = make_notifier()
    smtp_class, smtp_instance = mock_smtp_context()

    with patch("bot.notifier.smtplib.SMTP", smtp_class):
        notifier.notify_success(make_target(), make_slot(), {})

    body = smtp_instance.sendmail.call_args[0][2]
    assert "N/A" in body


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

def test_send_email_raises_on_smtp_error():
    notifier = make_notifier()

    with patch("bot.notifier.smtplib.SMTP", side_effect=OSError("connection refused")):
        with pytest.raises(OSError):
            notifier._send_email("subject", "body")
