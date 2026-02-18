from __future__ import annotations

import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import AppConfig, Target
from .notifier import Notifier
from .resy_client import ResyClient, Slot

logger = logging.getLogger(__name__)

# Snipe mode: retry for up to this many seconds after the release time fires
SNIPE_WINDOW_SECONDS = 60
SNIPE_RETRY_INTERVAL = 0.5   # seconds between attempts during snipe burst


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
        # Track which targets have been booked so we can skip further attempts
        self._booked: set[int] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        for idx, target in enumerate(self.config.targets):
            if target.snipe_mode:
                self._schedule_snipe(idx, target)
            else:
                self._schedule_polling(idx, target)
        self._scheduler.start()
        logger.info("Scheduler started with %d job(s).", len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down.")

    # ------------------------------------------------------------------
    # Job scheduling helpers
    # ------------------------------------------------------------------

    def _schedule_snipe(self, idx: int, target: Target) -> None:
        assert target.release_time is not None
        hour, minute = target.release_time.split(":")
        trigger = CronTrigger(hour=int(hour), minute=int(minute), second=0, timezone="UTC")
        job_id = f"snipe_{idx}_{target.venue_id}_{target.date}"
        self._scheduler.add_job(
            self._snipe_job,
            trigger=trigger,
            args=[idx, target],
            id=job_id,
            name=f"Snipe {target.venue_name} {target.date}",
            max_instances=1,
            misfire_grace_time=10,
        )
        logger.info(
            "Scheduled snipe job for %s on %s at UTC %s (job_id=%s)",
            target.venue_name,
            target.date,
            target.release_time,
            job_id,
        )

    def _schedule_polling(self, idx: int, target: Target) -> None:
        trigger = IntervalTrigger(seconds=target.poll_interval_seconds)
        job_id = f"poll_{idx}_{target.venue_id}_{target.date}"
        self._scheduler.add_job(
            self._poll_job,
            trigger=trigger,
            args=[idx, target],
            id=job_id,
            name=f"Poll {target.venue_name} {target.date}",
            max_instances=1,
        )
        logger.info(
            "Scheduled polling job for %s on %s every %ds (job_id=%s)",
            target.venue_name,
            target.date,
            target.poll_interval_seconds,
            job_id,
        )

    # ------------------------------------------------------------------
    # Job callables
    # ------------------------------------------------------------------

    def _snipe_job(self, idx: int, target: Target) -> None:
        """Burst-retry booking for up to SNIPE_WINDOW_SECONDS after release time fires."""
        if idx in self._booked:
            return
        logger.info(
            "Snipe window opened for %s %s — bursting for %ds",
            target.venue_name,
            target.date,
            SNIPE_WINDOW_SECONDS,
        )
        deadline = time.monotonic() + SNIPE_WINDOW_SECONDS
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            logger.debug("Snipe attempt %d for %s", attempt, target.venue_name)
            if self._attempt_booking(idx, target):
                return
            time.sleep(SNIPE_RETRY_INTERVAL)
        logger.warning(
            "Snipe window closed for %s %s without a successful booking.",
            target.venue_name,
            target.date,
        )

    def _poll_job(self, idx: int, target: Target) -> None:
        """Single booking attempt; called on each interval tick."""
        if idx in self._booked:
            return
        self._attempt_booking(idx, target)

    # ------------------------------------------------------------------
    # Core booking logic
    # ------------------------------------------------------------------

    def _attempt_booking(self, idx: int, target: Target) -> bool:
        """Try to find and book a preferred slot.

        Returns True on success, False otherwise.
        """
        try:
            slots = self.client.find_slots(target.venue_id, target.date, target.party_size)
        except Exception as exc:
            logger.error("find_slots failed for %s: %s", target.venue_name, exc)
            return False

        slot = self._pick_preferred_slot(slots, target.time_preferences)
        if slot is None:
            logger.info(
                "No preferred slots available for %s on %s",
                target.venue_name,
                target.date,
            )
            return False

        logger.info(
            "Preferred slot found: %s at %s — attempting to book",
            target.venue_name,
            slot.start_time.strftime("%H:%M"),
        )

        try:
            book_token = self.client.get_booking_token(
                slot.config_id, target.date, target.party_size
            )
            confirmation = self.client.book(book_token, self.payment_method_id)
        except Exception as exc:
            logger.error("Booking failed for %s: %s", target.venue_name, exc)
            return False

        logger.info("Booking succeeded for %s! Confirmation: %s", target.venue_name, confirmation)
        self._booked.add(idx)
        self._cancel_job(idx, target)

        try:
            self.notifier.notify_success(target, slot, confirmation)
        except Exception as exc:
            logger.error("Notification failed (booking still succeeded): %s", exc)

        return True

    def _pick_preferred_slot(self, slots: list[Slot], time_preferences: list[str]) -> Slot | None:
        """Return the highest-priority preferred slot, or None if none match."""
        slot_by_time: dict[str, Slot] = {}
        for slot in slots:
            hhmm = slot.start_time.strftime("%H:%M")
            slot_by_time[hhmm] = slot

        for preferred_time in time_preferences:
            if preferred_time in slot_by_time:
                return slot_by_time[preferred_time]
        return None

    def _cancel_job(self, idx: int, target: Target) -> None:
        prefix = "snipe" if target.snipe_mode else "poll"
        job_id = f"{prefix}_{idx}_{target.venue_id}_{target.date}"
        try:
            self._scheduler.remove_job(job_id)
            logger.info("Cancelled job %s after successful booking.", job_id)
        except Exception:
            pass   # job may have already been removed
