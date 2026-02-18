from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.resy.com"

# Days-out windows probed when doing empirical booking-window discovery (largest first)
_EMPIRICAL_PROBE_WINDOWS = [60, 45, 30, 28, 21, 14, 7]


@dataclass
class Slot:
    config_id: str
    start_time: datetime
    token: Optional[str] = None    # populated after get_booking_token()


class ResyClient:
    def __init__(self, api_key: str, auth_token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f'ResyAPI api_key="{api_key}"',
                "X-Resy-Auth-Token": auth_token,
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Origin": "https://resy.com",
                "Referer": "https://resy.com/",
            }
        )

    def is_date_on_calendar(self, venue_id: int, date: str, party_size: int) -> bool:
        """Return True if the venue appears in /4/find results for date.

        A venue appearing means the date is within the booking window, even if
        all slots are already taken.  This is the right signal for release-time
        discovery — a new date appearing on the calendar (fully booked or not)
        marks the moment reservations were released.
        """
        params = {
            "lat": 0,
            "long": 0,
            "day": date,
            "party_size": party_size,
            "venue_id": venue_id,
        }
        resp = self.session.get(f"{BASE_URL}/4/find", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        venues = data.get("results", {}).get("venues", [])
        return len(venues) > 0

    def find_slots(self, venue_id: int, date: str, party_size: int) -> list[Slot]:
        """GET /4/find — returns available slots for the venue/date/party_size."""
        params = {
            "lat": 0,
            "long": 0,
            "day": date,
            "party_size": party_size,
            "venue_id": venue_id,
        }
        resp = self.session.get(f"{BASE_URL}/4/find", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        slots: list[Slot] = []
        venues = data.get("results", {}).get("venues", [])
        for venue in venues:
            for slot_data in venue.get("slots", []):
                config = slot_data.get("config", {})
                config_id = config.get("token")
                if not config_id:
                    continue
                date_str = slot_data.get("date", {}).get("start", "")
                try:
                    start_time = datetime.fromisoformat(date_str)
                except ValueError:
                    logger.warning("Could not parse slot date: %s", date_str)
                    continue
                slots.append(Slot(config_id=config_id, start_time=start_time))

        logger.debug("find_slots returned %d slots for venue %s", len(slots), venue_id)
        return slots

    def get_booking_token(self, config_id: str, date: str, party_size: int) -> str:
        """POST /3/details — exchange a slot config_id for a short-lived booking token."""
        payload = {
            "config_id": config_id,
            "day": date,
            "party_size": party_size,
        }
        resp = self.session.post(f"{BASE_URL}/3/details", json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        book_token = data.get("book_token", {}).get("value")
        if not book_token:
            raise ValueError(f"No book_token in /3/details response: {data}")
        return book_token

    def book(self, book_token: str, payment_method_id: int) -> dict:
        """POST /3/book — complete the reservation."""
        payload = {
            "book_token": book_token,
            "struct_payment_method": f'{{"id":{payment_method_id}}}',
            "source_id": "resy.com-venue-details",
        }
        resp = self.session.post(
            f"{BASE_URL}/3/book",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def discover_venue_schedule(
        self, venue_id: int, party_size: int
    ) -> tuple[int, str | None]:
        """Discover booking window (days) and optional release time for a venue.

        Returns ``(booking_window_days, release_time_local)`` where
        ``release_time_local`` is ``"HH:MM"`` in the venue's local timezone, or
        ``None`` if it could not be determined.

        Discovery order:
          1. Resy venue API  (``GET /3/venue``)
          2. Resy website page scrape (text parsing for human-readable schedules)
          3. Empirical ``/4/find`` probing at increasing look-ahead windows
        """
        result = self._discover_venue_schedule_inner(venue_id, party_size)
        window_days, release_time = result
        print(
            f"[venue {venue_id}] Booking window: {window_days} days | "
            f"Release time: {release_time if release_time is not None else 'unknown'}"
        )
        return result

    def _discover_venue_schedule_inner(
        self, venue_id: int, party_size: int
    ) -> tuple[int, str | None]:
        # ------------------------------------------------------------------ #
        # Step 1 — venue API                                                   #
        # ------------------------------------------------------------------ #
        venue_data: dict = {}
        try:
            resp = self.session.get(
                f"{BASE_URL}/3/venue", params={"venue_id": venue_id}, timeout=10
            )
            resp.raise_for_status()
            venue_data = resp.json()

            window = (
                venue_data.get("booking_window_days")
                or venue_data.get("availability", {}).get("booking_window_days")
            )
            release_time = (
                venue_data.get("booking_start_time")
                or venue_data.get("availability", {}).get("booking_start_time")
            )
            if window is not None:
                logger.info(
                    "Venue API: discovered window=%d days, release_time=%s for venue %s",
                    window,
                    release_time or "unknown",
                    venue_id,
                )
                return int(window), release_time or None
        except Exception as exc:
            logger.debug("Venue API probe failed for %s: %s", venue_id, exc)

        # ------------------------------------------------------------------ #
        # Step 2 — scrape the Resy venue page for human-readable schedule     #
        # ------------------------------------------------------------------ #
        scrape_window, scrape_time = self._scrape_venue_page(venue_id, venue_data)
        if scrape_window is not None or scrape_time is not None:
            logger.info(
                "Page scrape: discovered window=%s days, release_time=%s for venue %s",
                scrape_window,
                scrape_time or "unknown",
                venue_id,
            )
            return scrape_window or 30, scrape_time

        # ------------------------------------------------------------------ #
        # Step 3 — empirical /4/find probing                                  #
        # ------------------------------------------------------------------ #
        today = date.today()
        for days_out in _EMPIRICAL_PROBE_WINDOWS:
            probe_date = today + timedelta(days=days_out)
            try:
                slots = self.find_slots(venue_id, probe_date.isoformat(), party_size)
                if slots:
                    logger.info(
                        "Empirical discovery: slots found at %d days out for venue %s",
                        days_out,
                        venue_id,
                    )
                    return days_out, None
            except Exception as exc:
                logger.debug("Empirical probe at %d days failed: %s", days_out, exc)

        logger.warning(
            "Could not determine booking window for venue %s; defaulting to 30 days",
            venue_id,
        )
        return 30, None

    # ---------------------------------------------------------------------- #
    # Internal helpers                                                         #
    # ---------------------------------------------------------------------- #

    def _scrape_venue_page(
        self, venue_id: int, venue_data: dict
    ) -> tuple[int | None, str | None]:
        """Fetch the venue's resy.com page and parse booking schedule from text.

        Returns ``(window_days, release_time_hhmm)``; either value may be
        ``None`` if not found.  ``release_time_hhmm`` is in 24-hour format.
        """
        venue_url = self._build_venue_url(venue_id, venue_data)
        if not venue_url:
            logger.debug("Cannot build venue URL for %s — skipping page scrape", venue_id)
            return None, None

        try:
            resp = requests.get(
                venue_url,
                timeout=10,
                headers={"User-Agent": self.session.headers.get("User-Agent", "")},
            )
            resp.raise_for_status()
            text = resp.text.lower()
        except Exception as exc:
            logger.debug("Page scrape request failed for %s: %s", venue_url, exc)
            return None, None

        window_days = self._parse_window_days(text)
        release_time = self._parse_release_time(text)
        return window_days, release_time

    def _build_venue_url(self, venue_id: int, venue_data: dict) -> str | None:
        """Construct the resy.com venue page URL from venue API data or fall back
        to the venue_id query-param form."""
        # Prefer a full URL from the API response if present
        direct_url = venue_data.get("url") or venue_data.get("venue_url")
        if direct_url:
            return direct_url

        # Try to construct from city code + slug
        location = venue_data.get("location") or {}
        city = location.get("city_code") or location.get("city") or ""
        slug = (
            venue_data.get("url_slug")
            or venue_data.get("slug")
            or venue_data.get("venue_slug")
            or ""
        )
        if city and slug:
            return f"https://resy.com/cities/{city.lower()}/{slug}"

        # Last resort: numeric-ID-based URL (may redirect)
        return f"https://resy.com/venues/{venue_id}"

    @staticmethod
    def _parse_window_days(text: str) -> int | None:
        """Extract booking-window days from page text.

        Matches patterns like:
          - "30 days in advance"
          - "books up to 28 days ahead"
          - "available 14 days before"
        """
        patterns = [
            r"(\d+)\s*days?\s+in\s+advance",
            r"(\d+)\s*days?\s+ahead",
            r"(\d+)\s*days?\s+before",
            r"up\s+to\s+(\d+)\s*days?",
            r"books?\s+(\d+)\s*days?",
        ]
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _parse_release_time(text: str) -> str | None:
        """Extract release time from page text in 24-hour "HH:MM" format.

        Matches patterns like:
          - "opens at 9am"
          - "released at 9:00am et"
          - "available at midnight"
          - "reservations released at 12:00am"
        """
        # Handle "midnight" and "noon" shorthands
        if re.search(r"(?:opens?|releases?|available|drops?)\s+at\s+midnight", text):
            return "00:00"
        if re.search(r"(?:opens?|releases?|available|drops?)\s+at\s+noon", text):
            return "12:00"

        # Generic HH[:MM] am/pm pattern
        m = re.search(
            r"(?:opens?|releases?|available|drops?)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
            text,
        )
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ampm = m.group(3)
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            return f"{hour:02d}:{minute:02d}"

        return None
