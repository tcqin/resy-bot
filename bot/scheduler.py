from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger  # used by discovery job

from .config import AppConfig, Target
from .notifier import Notifier
from .resy_client import ResyClient, Slot

logger = logging.getLogger(__name__)

# Snipe mode: retry for up to this many seconds after the release time fires
SNIPE_WINDOW_SECONDS = 60
SNIPE_RETRY_INTERVAL = 0.5   # seconds between attempts during snipe burst

_WEEKDAY_MAP = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}


class Scheduler:
    def __init__(
        self,
        client: ResyClient,
        config: AppConfig,
        notifier: Notifier,
        payment_method_id: int,
    ) -> None:
        self.client = client
        self.config = config
        self.notifier = notifier
        self.payment_method_id = payment_method_id
        self._scheduler = BackgroundScheduler(timezone="UTC")
        # Single flag: True once any booking succeeds; cancels all remaining jobs
        self._booked: bool = False
        # Tracks whether the discovery probe found slots on the previous check
        # Tracks whether the probe date was on the calendar on the previous
        # discovery check (keyed by venue_id so multi-target configs work)
        self._discovery_prev_on_calendar: dict[int, bool] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        for target in self.config.targets:
            window_days, release_time_local = self.client.discover_venue_schedule(
                target.venue_id, target.party_size
            )
            logger.info(
                "Venue %s: booking_window=%d days, release_time=%s",
                target.venue_name,
                window_days,
                release_time_local or "unknown (hourly discovery enabled)",
            )

            candidate_dates = self._generate_candidate_dates(target)
            if not candidate_dates:
                logger.warning(
                    "No candidate dates for %s — check start_date/end_date/days_of_week",
                    target.venue_name,
                )
                continue

            logger.info(
                "Candidate dates for %s: %d dates (%s … %s)",
                target.venue_name,
                len(candidate_dates),
                candidate_dates[0],
                candidate_dates[-1],
            )

            if release_time_local is not None:
                # Schedule a snipe job for each candidate date's release day
                tz = pytz.timezone(target.venue_timezone)
                today = date.today()
                for candidate_date in candidate_dates:
                    release_day = candidate_date - timedelta(days=window_days)
                    if release_day > today:
                        self._schedule_snipe(
                            target, candidate_date, release_day, release_time_local, tz
                        )
            else:
                # Release time unknown — start an hourly discovery job
                self._schedule_discovery(target, window_days, candidate_dates)

            # Always schedule a polling job (handles post-window fallback)
            self._schedule_polling(target, candidate_dates, window_days)

        self._scheduler.start()
        logger.info("Scheduler started with %d job(s).", len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down.")

    # ------------------------------------------------------------------
    # Job scheduling helpers
    # ------------------------------------------------------------------

    def _schedule_snipe(
        self,
        target: Target,
        candidate_date: date,
        release_day: date,
        release_time_local: str,
        tz: pytz.BaseTzInfo,
    ) -> None:
        hour, minute = release_time_local.split(":")
        local_dt = tz.localize(
            datetime(
                release_day.year, release_day.month, release_day.day,
                int(hour), int(minute), 0,
            )
        )
        utc_dt = local_dt.astimezone(pytz.utc)
        job_id = f"snipe_{target.venue_id}_{candidate_date.isoformat()}"
        self._scheduler.add_job(
            self._snipe_job,
            trigger=CronTrigger(
                year=utc_dt.year,
                month=utc_dt.month,
                day=utc_dt.day,
                hour=utc_dt.hour,
                minute=utc_dt.minute,
                second=0,
                timezone="UTC",
            ),
            args=[target, candidate_date.isoformat()],
            id=job_id,
            name=f"Snipe {target.venue_name} {candidate_date}",
            max_instances=1,
            misfire_grace_time=10,
        )
        logger.info(
            "Scheduled snipe for %s on %s — release day %s at %s local / %s UTC",
            target.venue_name,
            candidate_date,
            release_day,
            release_time_local,
            utc_dt.strftime("%H:%M"),
        )

    def _schedule_discovery(
        self,
        target: Target,
        window_days: int,
        candidate_dates: list[date],
    ) -> None:
        self._discovery_prev_on_calendar[target.venue_id] = False
        job_id = f"discover_{target.venue_id}"
        self._scheduler.add_job(
            self._discovery_job,
            trigger=IntervalTrigger(hours=1),
            args=[target, window_days, candidate_dates],
            id=job_id,
            name=f"Discover {target.venue_name}",
            max_instances=1,
        )
        logger.info("Scheduled hourly discovery job for %s", target.venue_name)

    def _schedule_polling(
        self,
        target: Target,
        candidate_dates: list[date],
        window_days: int,
    ) -> None:
        job_id = f"poll_{target.venue_id}"
        self._scheduler.add_job(
            self._poll_job,
            # Fire at :00:15, :10:15, :20:15, :30:15, :40:15, :50:15 every hour
            trigger=CronTrigger(minute="*/10", second=15, timezone="UTC"),
            args=[target, candidate_dates, window_days],
            id=job_id,
            name=f"Poll {target.venue_name}",
            max_instances=1,
            # Run immediately on startup; subsequent runs follow the cron schedule
            next_run_time=datetime.now(pytz.utc),
        )
        logger.info(
            "Scheduled polling job for %s — running now, then every 10 min at :X0:15",
            target.venue_name,
        )

    # ------------------------------------------------------------------
    # Job callables
    # ------------------------------------------------------------------

    def _snipe_job(self, target: Target, date_str: str) -> None:
        """Burst-retry booking for up to SNIPE_WINDOW_SECONDS after release fires."""
        if self._booked:
            return
        logger.info(
            "Snipe window opened for %s %s — bursting for %ds",
            target.venue_name,
            date_str,
            SNIPE_WINDOW_SECONDS,
        )
        deadline = time.monotonic() + SNIPE_WINDOW_SECONDS
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            logger.debug("Snipe attempt %d for %s %s", attempt, target.venue_name, date_str)
            if self._attempt_booking(target, date_str):
                return
            time.sleep(SNIPE_RETRY_INTERVAL)
        logger.warning(
            "Snipe window closed for %s %s without a successful booking.",
            target.venue_name,
            date_str,
        )

    def _poll_job(
        self, target: Target, candidate_dates: list[date], window_days: int
    ) -> None:
        """Check each candidate date that's currently within the booking window."""
        if self._booked:
            return
        today = date.today()
        for candidate_date in candidate_dates:
            if self._booked:
                return
            days_until = (candidate_date - today).days
            if 0 <= days_until <= window_days:
                if self._attempt_booking(target, candidate_date.isoformat()):
                    return

    def _discovery_job(
        self, target: Target, window_days: int, candidate_dates: list[date]
    ) -> None:
        """Probe whether the next candidate date has appeared on the calendar.

        Resy shows a date on the calendar (with 0 available slots) the moment
        reservations are released — slots may already be gone.  So we track
        whether the probe date is *on the calendar at all*, not whether it has
        open slots.  The first time it appears is our inferred release time.
        """
        if self._booked:
            return
        today = date.today()
        probe_date = today + timedelta(days=window_days)
        try:
            on_calendar = self.client.is_date_on_calendar(
                target.venue_id, probe_date.isoformat(), target.party_size
            )
        except Exception as exc:
            logger.debug("Discovery probe failed for %s: %s", target.venue_name, exc)
            return

        prev_on_calendar = self._discovery_prev_on_calendar.get(target.venue_id, False)
        self._discovery_prev_on_calendar[target.venue_id] = on_calendar

        if on_calendar and not prev_on_calendar:
            tz = pytz.timezone(target.venue_timezone)
            now_local = datetime.now(tz)
            inferred_release_time = f"{now_local.hour:02d}:00"
            logger.info(
                "Discovery: %s appeared on calendar for %s at ~%s local — scheduling snipes",
                probe_date,
                target.venue_name,
                inferred_release_time,
            )
            # Remove the discovery job
            try:
                self._scheduler.remove_job(f"discover_{target.venue_id}")
            except Exception:
                pass
            # Schedule snipe jobs for all future candidate dates
            for candidate_date in candidate_dates:
                release_day = candidate_date - timedelta(days=window_days)
                if release_day > today:
                    self._schedule_snipe(
                        target, candidate_date, release_day, inferred_release_time, tz
                    )

    # ------------------------------------------------------------------
    # Core booking logic
    # ------------------------------------------------------------------

    def _attempt_booking(self, target: Target, date_str: str) -> bool:
        """Try to find and book a preferred slot for date_str.

        Returns True on success, False otherwise.
        """
        try:
            slots = self.client.find_slots(target.venue_id, date_str, target.party_size)
        except Exception as exc:
            logger.error("find_slots failed for %s: %s", target.venue_name, exc)
            return False

        slot = self._pick_preferred_slot(
            slots, target.time_center, target.time_radius_minutes
        )
        if slot is None:
            logger.info(
                "No preferred slots for %s on %s (center=%s ±%dmin)",
                target.venue_name,
                date_str,
                target.time_center,
                target.time_radius_minutes,
            )
            return False

        logger.info(
            "Preferred slot found: %s at %s on %s — attempting to book",
            target.venue_name,
            slot.start_time.strftime("%H:%M"),
            date_str,
        )

        try:
            book_token = self.client.get_booking_token(
                slot.config_id, date_str, target.party_size
            )
            confirmation = self.client.book(book_token, self.payment_method_id)
        except Exception as exc:
            logger.error("Booking failed for %s: %s", target.venue_name, exc)
            return False

        logger.info(
            "Booking succeeded for %s on %s! Confirmation: %s",
            target.venue_name,
            date_str,
            confirmation,
        )
        self._booked = True
        self._cancel_all_jobs()

        try:
            self.notifier.notify_success(target, slot, confirmation)
        except Exception as exc:
            logger.error("Notification failed (booking still succeeded): %s", exc)

        return True

    def _pick_preferred_slot(
        self, slots: list[Slot], time_center: str, radius_minutes: int
    ) -> Slot | None:
        """Return the slot closest to time_center within ±radius_minutes, or None."""
        center_h, center_m = time_center.split(":")
        center_total = int(center_h) * 60 + int(center_m)

        best_slot: Slot | None = None
        best_distance = float("inf")

        for slot in slots:
            slot_total = slot.start_time.hour * 60 + slot.start_time.minute
            distance = abs(slot_total - center_total)
            if distance <= radius_minutes and distance < best_distance:
                best_distance = distance
                best_slot = slot

        return best_slot

    def _generate_candidate_dates(self, target: Target) -> list[date]:
        """Return all dates in [start_date, end_date] that fall on days_of_week."""
        start = date.fromisoformat(target.start_date)
        end = date.fromisoformat(target.end_date)
        wanted = {_WEEKDAY_MAP[day] for day in target.days_of_week}

        candidates: list[date] = []
        current = start
        while current <= end:
            if current.weekday() in wanted:
                candidates.append(current)
            current += timedelta(days=1)
        return candidates

    def _cancel_all_jobs(self) -> None:
        """Remove every job from the scheduler."""
        try:
            for job in list(self._scheduler.get_jobs()):
                try:
                    self._scheduler.remove_job(job.id)
                except Exception:
                    pass
            logger.info("All scheduler jobs cancelled.")
        except Exception as exc:
            logger.warning("Error cancelling jobs: %s", exc)
