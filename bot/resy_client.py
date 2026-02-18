from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.resy.com"


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
