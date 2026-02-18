"""Tests for bot/scheduler.py — slot selection and booking logic."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from bot.config import AppConfig, EmailConfig, NotificationConfig, Target
from bot.resy_client import Slot
from bot.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_target(**overrides) -> Target:
    defaults = dict(
        venue_id=5286,
        venue_name="Carbone",
        date="2026-03-15",
        party_size=2,
        time_preferences=["19:00", "19:30", "20:00"],
        release_time=None,
        poll_interval_seconds=30,
    )
    return Target(**{**defaults, **overrides})


def make_scheduler(targets=None, client=None, notifier=None) -> Scheduler:
    if targets is None:
        targets = [make_target()]
    cfg = AppConfig(
        targets=targets,
        notifications=NotificationConfig(
            email=EmailConfig(
                smtp_server="smtp.gmail.com",
                smtp_port=587,
                from_address="a@example.com",
                to_address="b@example.com",
            ),
        ),
    )
    client = client or MagicMock()
    notifier = notifier or MagicMock()
    sched = Scheduler(client=client, config=cfg, notifier=notifier, payment_method_id=42)
    # Prevent the real APScheduler from starting
    sched._scheduler = MagicMock()
    return sched


def make_slot(hhmm: str, config_id: str = "cfg-1") -> Slot:
    hour, minute = hhmm.split(":")
    return Slot(
        config_id=config_id,
        start_time=datetime(2026, 3, 15, int(hour), int(minute)),
    )


# ---------------------------------------------------------------------------
# _pick_preferred_slot
# ---------------------------------------------------------------------------

def test_pick_preferred_slot_returns_first_match():
    sched = make_scheduler()
    slots = [make_slot("19:30", "cfg-a"), make_slot("20:00", "cfg-b")]
    target = make_target(time_preferences=["19:00", "19:30", "20:00"])
    result = sched._pick_preferred_slot(slots, target.time_preferences)
    assert result is not None
    assert result.config_id == "cfg-a"   # 19:30 preferred over 20:00


def test_pick_preferred_slot_respects_priority_order():
    sched = make_scheduler()
    slots = [make_slot("20:00", "cfg-late"), make_slot("19:00", "cfg-early")]
    target = make_target(time_preferences=["19:00", "20:00"])
    result = sched._pick_preferred_slot(slots, target.time_preferences)
    assert result.config_id == "cfg-early"   # 19:00 wins even though 20:00 listed first in slots


def test_pick_preferred_slot_returns_none_when_no_match():
    sched = make_scheduler()
    slots = [make_slot("21:00")]
    result = sched._pick_preferred_slot(slots, ["19:00", "19:30"])
    assert result is None


def test_pick_preferred_slot_empty_slots():
    sched = make_scheduler()
    result = sched._pick_preferred_slot([], ["19:00"])
    assert result is None


# ---------------------------------------------------------------------------
# _attempt_booking
# ---------------------------------------------------------------------------

def test_attempt_booking_success():
    client = MagicMock()
    notifier = MagicMock()
    client.find_slots.return_value = [make_slot("19:00")]
    client.get_booking_token.return_value = "btoken-123"
    client.book.return_value = {"resy_token": "RES-456"}

    target = make_target(time_preferences=["19:00"])
    sched = make_scheduler(targets=[target], client=client, notifier=notifier)

    result = sched._attempt_booking(0, target)

    assert result is True
    assert 0 in sched._booked
    client.get_booking_token.assert_called_once_with("cfg-1", "2026-03-15", 2)
    client.book.assert_called_once_with("btoken-123", 42)
    notifier.notify_success.assert_called_once()


def test_attempt_booking_no_preferred_slots():
    client = MagicMock()
    client.find_slots.return_value = [make_slot("22:00")]   # not in preferences

    target = make_target(time_preferences=["19:00", "19:30"])
    sched = make_scheduler(targets=[target], client=client)

    result = sched._attempt_booking(0, target)

    assert result is False
    assert 0 not in sched._booked
    client.get_booking_token.assert_not_called()


def test_attempt_booking_find_slots_error():
    client = MagicMock()
    client.find_slots.side_effect = Exception("network error")

    target = make_target()
    sched = make_scheduler(targets=[target], client=client)

    result = sched._attempt_booking(0, target)

    assert result is False
    assert 0 not in sched._booked


def test_attempt_booking_book_error():
    client = MagicMock()
    client.find_slots.return_value = [make_slot("19:00")]
    client.get_booking_token.return_value = "btoken"
    client.book.side_effect = Exception("payment failed")

    target = make_target(time_preferences=["19:00"])
    sched = make_scheduler(targets=[target], client=client)

    result = sched._attempt_booking(0, target)

    assert result is False
    assert 0 not in sched._booked


def test_attempt_booking_notification_failure_does_not_unbook():
    """A notification error must not undo a successful booking."""
    client = MagicMock()
    notifier = MagicMock()
    client.find_slots.return_value = [make_slot("19:00")]
    client.get_booking_token.return_value = "btoken"
    client.book.return_value = {"resy_token": "RES-1"}
    notifier.notify_success.side_effect = Exception("SMTP down")

    target = make_target(time_preferences=["19:00"])
    sched = make_scheduler(targets=[target], client=client, notifier=notifier)

    result = sched._attempt_booking(0, target)

    assert result is True
    assert 0 in sched._booked


# ---------------------------------------------------------------------------
# _poll_job
# ---------------------------------------------------------------------------

def test_poll_job_skips_if_already_booked():
    client = MagicMock()
    target = make_target()
    sched = make_scheduler(targets=[target], client=client)
    sched._booked.add(0)

    sched._poll_job(0, target)

    client.find_slots.assert_not_called()


def test_poll_job_calls_attempt_booking():
    client = MagicMock()
    client.find_slots.return_value = []   # no slots → returns False

    target = make_target()
    sched = make_scheduler(targets=[target], client=client)

    sched._poll_job(0, target)

    client.find_slots.assert_called_once()


# ---------------------------------------------------------------------------
# start() — job registration
# ---------------------------------------------------------------------------

def test_start_registers_poll_job_for_polling_target():
    target = make_target(release_time=None)
    sched = make_scheduler(targets=[target])

    sched.start()

    add_job_calls = sched._scheduler.add_job.call_args_list
    assert len(add_job_calls) == 1
    _, kwargs = add_job_calls[0]
    assert "poll" in kwargs["id"]


def test_start_registers_snipe_job_for_snipe_target():
    target = make_target(release_time="00:00")
    sched = make_scheduler(targets=[target])

    sched.start()

    add_job_calls = sched._scheduler.add_job.call_args_list
    assert len(add_job_calls) == 1
    _, kwargs = add_job_calls[0]
    assert "snipe" in kwargs["id"]
