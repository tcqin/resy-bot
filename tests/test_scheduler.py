"""Tests for bot/scheduler.py — slot selection, candidate date generation, booking logic."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

from bot.config import AppConfig, Target
from bot.resy_client import Slot
from bot.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_target(**overrides) -> Target:
    defaults = dict(
        venue_id=5286,
        venue_name="Carbone",
        start_date="2026-03-01",
        end_date="2026-04-30",
        party_size=2,
        days_of_week=["Tuesday", "Thursday"],
        time_center="19:00",
        time_radius_minutes=30,
        venue_timezone="America/New_York",
        poll_interval_seconds=30,
    )
    return Target(**{**defaults, **overrides})


def make_scheduler(targets=None, client=None) -> Scheduler:
    if targets is None:
        targets = [make_target()]
    cfg = AppConfig(targets=targets)
    client = client or MagicMock()
    sched = Scheduler(client=client, config=cfg, payment_method_id=42)
    # Prevent the real APScheduler from starting
    sched._scheduler = MagicMock()
    return sched


def make_slot(hhmm: str, date_str: str = "2026-03-15", config_id: str = "cfg-1") -> Slot:
    hour, minute = hhmm.split(":")
    year, month, day = date_str.split("-")
    return Slot(
        config_id=config_id,
        start_time=datetime(int(year), int(month), int(day), int(hour), int(minute)),
    )


# ---------------------------------------------------------------------------
# _generate_candidate_dates
# ---------------------------------------------------------------------------

def test_generate_candidate_dates_correct_days():
    """Only Tuesdays and Thursdays should be returned."""
    target = make_target(
        start_date="2026-03-01",
        end_date="2026-03-31",
        days_of_week=["Tuesday", "Thursday"],
    )
    sched = make_scheduler(targets=[target])
    candidates = sched._generate_candidate_dates(target)
    for d in candidates:
        assert d.weekday() in (1, 3)  # Tuesday=1, Thursday=3


def test_generate_candidate_dates_boundary_dates_included():
    """start_date and end_date are included if they match days_of_week."""
    # 2026-03-03 is a Tuesday; 2026-03-05 is a Thursday
    target = make_target(
        start_date="2026-03-03",
        end_date="2026-03-05",
        days_of_week=["Tuesday", "Thursday"],
    )
    sched = make_scheduler(targets=[target])
    candidates = sched._generate_candidate_dates(target)
    assert date(2026, 3, 3) in candidates
    assert date(2026, 3, 5) in candidates


def test_generate_candidate_dates_count():
    """March 2026 has 5 Tuesdays (3,10,17,24,31) and 4 Thursdays (5,12,19,26) = 9 total."""
    target = make_target(
        start_date="2026-03-01",
        end_date="2026-03-31",
        days_of_week=["Tuesday", "Thursday"],
    )
    sched = make_scheduler(targets=[target])
    candidates = sched._generate_candidate_dates(target)
    assert len(candidates) == 9


def test_generate_candidate_dates_empty_when_no_match():
    target = make_target(
        start_date="2026-03-02",  # Monday
        end_date="2026-03-02",
        days_of_week=["Tuesday"],
    )
    sched = make_scheduler(targets=[target])
    assert sched._generate_candidate_dates(target) == []


def test_generate_candidate_dates_sorted():
    target = make_target(
        start_date="2026-03-01",
        end_date="2026-03-31",
        days_of_week=["Thursday", "Tuesday"],  # reversed order — output still sorted
    )
    sched = make_scheduler(targets=[target])
    candidates = sched._generate_candidate_dates(target)
    assert candidates == sorted(candidates)


# ---------------------------------------------------------------------------
# _pick_preferred_slot
# ---------------------------------------------------------------------------

def test_pick_preferred_slot_exact_center():
    sched = make_scheduler()
    slots = [make_slot("19:00")]
    result = sched._pick_preferred_slot(slots, "19:00", 30)
    assert result is not None
    assert result.config_id == "cfg-1"


def test_pick_preferred_slot_within_window():
    sched = make_scheduler()
    slots = [make_slot("19:20", config_id="cfg-a")]
    result = sched._pick_preferred_slot(slots, "19:00", 30)
    assert result is not None
    assert result.config_id == "cfg-a"


def test_pick_preferred_slot_outside_window_returns_none():
    sched = make_scheduler()
    slots = [make_slot("21:00")]
    result = sched._pick_preferred_slot(slots, "19:00", 30)
    assert result is None


def test_pick_preferred_slot_closest_to_center_wins():
    sched = make_scheduler()
    # 18:45 is 15 min away; 19:25 is 25 min away — 18:45 should win
    slots = [
        make_slot("19:25", config_id="far"),
        make_slot("18:45", config_id="close"),
    ]
    result = sched._pick_preferred_slot(slots, "19:00", 30)
    assert result is not None
    assert result.config_id == "close"


def test_pick_preferred_slot_empty_list():
    sched = make_scheduler()
    assert sched._pick_preferred_slot([], "19:00", 30) is None


def test_pick_preferred_slot_boundary_included():
    """A slot exactly radius_minutes away should still be returned."""
    sched = make_scheduler()
    slots = [make_slot("19:30")]  # exactly 30 min from 19:00
    result = sched._pick_preferred_slot(slots, "19:00", 30)
    assert result is not None


# ---------------------------------------------------------------------------
# _attempt_booking
# ---------------------------------------------------------------------------

def test_attempt_booking_success():
    client = MagicMock()
    client.find_slots.return_value = [make_slot("19:00")]
    client.get_booking_token.return_value = "btoken-123"
    client.book.return_value = {"resy_token": "RES-456"}

    target = make_target(time_center="19:00", time_radius_minutes=30)
    sched = make_scheduler(targets=[target], client=client)

    result = sched._attempt_booking(target, "2026-03-15")

    assert result is True
    assert sched._booked is True
    client.get_booking_token.assert_called_once_with("cfg-1", "2026-03-15", 2)
    client.book.assert_called_once_with("btoken-123", 42)


def test_attempt_booking_no_preferred_slots():
    client = MagicMock()
    client.find_slots.return_value = [make_slot("22:00")]   # outside ±30min of 19:00

    target = make_target(time_center="19:00", time_radius_minutes=30)
    sched = make_scheduler(targets=[target], client=client)

    result = sched._attempt_booking(target, "2026-03-15")

    assert result is False
    assert sched._booked is False
    client.get_booking_token.assert_not_called()


def test_attempt_booking_find_slots_error():
    client = MagicMock()
    client.find_slots.side_effect = Exception("network error")

    target = make_target()
    sched = make_scheduler(targets=[target], client=client)

    result = sched._attempt_booking(target, "2026-03-15")

    assert result is False
    assert sched._booked is False


def test_attempt_booking_book_error():
    client = MagicMock()
    client.find_slots.return_value = [make_slot("19:00")]
    client.get_booking_token.return_value = "btoken"
    client.book.side_effect = Exception("payment failed")

    target = make_target(time_center="19:00")
    sched = make_scheduler(targets=[target], client=client)

    result = sched._attempt_booking(target, "2026-03-15")

    assert result is False
    assert sched._booked is False


def test_attempt_booking_book_error_leaves_unbooked():
    """A book() error must leave _booked as False."""
    client = MagicMock()
    client.find_slots.return_value = [make_slot("19:00")]
    client.get_booking_token.return_value = "btoken"
    client.book.side_effect = Exception("payment failed again")

    target = make_target(time_center="19:00")
    sched = make_scheduler(targets=[target], client=client)

    result = sched._attempt_booking(target, "2026-03-15")

    assert result is False
    assert sched._booked is False


def test_attempt_booking_cancels_all_jobs_on_success():
    """Successful booking must cancel all scheduler jobs."""
    client = MagicMock()
    client.find_slots.return_value = [make_slot("19:00")]
    client.get_booking_token.return_value = "btoken"
    client.book.return_value = {"resy_token": "RES-1"}

    target = make_target(time_center="19:00")
    sched = make_scheduler(targets=[target], client=client)

    # Simulate two jobs in the scheduler
    fake_job_a = MagicMock()
    fake_job_a.id = "poll_5286"
    fake_job_b = MagicMock()
    fake_job_b.id = "snipe_5286_2026-03-17"
    sched._scheduler.get_jobs.return_value = [fake_job_a, fake_job_b]

    sched._attempt_booking(target, "2026-03-15")

    assert sched._scheduler.remove_job.call_count == 2


# ---------------------------------------------------------------------------
# _poll_job
# ---------------------------------------------------------------------------

def test_poll_job_skips_if_already_booked():
    client = MagicMock()
    target = make_target()
    sched = make_scheduler(targets=[target], client=client)
    sched._booked = True

    sched._poll_job(target, [date(2026, 3, 17)], 30)

    client.find_slots.assert_not_called()


def test_poll_job_skips_dates_outside_window():
    """Dates more than window_days out should not trigger a booking attempt."""
    client = MagicMock()
    target = make_target()
    sched = make_scheduler(targets=[target], client=client)

    future_date = date.today() + timedelta(days=60)  # well outside window of 30
    sched._poll_job(target, [future_date], 30)

    client.find_slots.assert_not_called()


def test_poll_job_attempts_dates_within_window():
    client = MagicMock()
    client.find_slots.return_value = []
    target = make_target()
    sched = make_scheduler(targets=[target], client=client)

    near_date = date.today() + timedelta(days=5)
    sched._poll_job(target, [near_date], 30)

    client.find_slots.assert_called_once_with(target.venue_id, near_date.isoformat(), target.party_size)


def test_poll_job_stops_after_successful_booking():
    """Once a booking succeeds, remaining candidates should not be tried."""
    client = MagicMock()
    client.find_slots.return_value = [make_slot("19:00", date_str=date.today().isoformat())]
    client.get_booking_token.return_value = "tok"
    client.book.return_value = {"resy_token": "X"}

    target = make_target(time_center="19:00")
    sched = make_scheduler(targets=[target], client=client)
    sched._scheduler.get_jobs.return_value = []

    today = date.today()
    candidate_dates = [today, today + timedelta(days=7)]

    sched._poll_job(target, candidate_dates, 30)

    # Only the first date should have been queried
    assert client.find_slots.call_count == 1


# ---------------------------------------------------------------------------
# _discovery_job
# ---------------------------------------------------------------------------

def test_discovery_job_schedules_snipes_when_date_appears_on_calendar():
    """When a date first appears on the calendar, snipe jobs should be scheduled."""
    client = MagicMock()
    client.is_date_on_calendar.return_value = True  # date is now on calendar

    target = make_target(
        start_date="2026-03-01",
        end_date="2026-03-31",
        days_of_week=["Tuesday"],
        venue_timezone="America/New_York",
    )
    sched = make_scheduler(targets=[target], client=client)
    sched._discovery_prev_on_calendar[target.venue_id] = False  # was not on calendar before

    candidate_dates = [date(2026, 3, 3), date(2026, 3, 10)]
    # Mock today far in the past so release days (candidate - 30d) are in the future
    fake_today = date(2025, 1, 1)
    window_days = 30

    with patch("bot.scheduler.date") as mock_date:
        mock_date.today.return_value = fake_today
        sched._discovery_job(target, window_days, candidate_dates)

    # Snipe jobs should have been scheduled for future release days
    assert sched._scheduler.add_job.called


def test_discovery_job_no_action_when_date_already_on_calendar():
    """If the date was already on the calendar last check, no new snipes should be scheduled."""
    client = MagicMock()
    client.is_date_on_calendar.return_value = True

    target = make_target()
    sched = make_scheduler(targets=[target], client=client)
    sched._discovery_prev_on_calendar[target.venue_id] = True  # already on calendar

    candidate_dates = [date(2026, 3, 3)]
    sched._discovery_job(target, 30, candidate_dates)

    sched._scheduler.add_job.assert_not_called()


def test_discovery_job_no_action_when_date_not_on_calendar():
    """If the date still isn't on the calendar, no snipes should be scheduled."""
    client = MagicMock()
    client.is_date_on_calendar.return_value = False  # date not yet on calendar

    target = make_target()
    sched = make_scheduler(targets=[target], client=client)
    sched._discovery_prev_on_calendar[target.venue_id] = False

    candidate_dates = [date(2026, 3, 3)]
    sched._discovery_job(target, 30, candidate_dates)

    sched._scheduler.add_job.assert_not_called()


def test_discovery_job_skips_if_booked():
    client = MagicMock()
    target = make_target()
    sched = make_scheduler(targets=[target], client=client)
    sched._booked = True

    sched._discovery_job(target, 30, [date(2026, 3, 3)])

    client.find_slots.assert_not_called()


# ---------------------------------------------------------------------------
# start() — job registration
# ---------------------------------------------------------------------------

def test_start_registers_poll_and_discovery_jobs_when_release_time_unknown():
    """When release_time is None, start() should register poll + discovery jobs."""
    client = MagicMock()
    client.discover_venue_schedule.return_value = (30, None)

    target = make_target()
    sched = make_scheduler(targets=[target], client=client)

    sched.start()

    job_ids = [kwargs["id"] for _, kwargs in sched._scheduler.add_job.call_args_list]
    assert any("poll" in jid for jid in job_ids)
    assert any("discover" in jid for jid in job_ids)


def test_start_registers_snipe_and_poll_jobs_when_release_time_known():
    """When release_time is known, start() schedules snipe + poll jobs."""
    client = MagicMock()
    client.discover_venue_schedule.return_value = (30, "09:00")

    # Use a fixed date range so we control how many snipe jobs are created
    target = make_target(
        start_date="2026-04-07",  # Tuesday (far future)
        end_date="2026-04-07",
        days_of_week=["Tuesday"],
    )
    sched = make_scheduler(targets=[target], client=client)

    with patch("bot.scheduler.date") as mock_date:
        mock_date.today.return_value = date(2026, 3, 1)
        mock_date.fromisoformat = date.fromisoformat
        sched.start()

    job_ids = [kwargs["id"] for _, kwargs in sched._scheduler.add_job.call_args_list]
    assert any("snipe" in jid for jid in job_ids)
    assert any("poll" in jid for jid in job_ids)
