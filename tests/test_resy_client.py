"""Tests for bot/resy_client.py — API response parsing and request construction."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from bot.resy_client import ResyClient, Slot


def make_client() -> ResyClient:
    return ResyClient(api_key="test-key", auth_token="test-token")


def mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# ResyClient.__init__ — headers
# ---------------------------------------------------------------------------

def test_client_sets_auth_headers():
    client = ResyClient(api_key="mykey", auth_token="mytoken")
    headers = client.session.headers
    assert 'ResyAPI api_key="mykey"' in headers["Authorization"]
    assert headers["X-Resy-Auth-Token"] == "mytoken"


# ---------------------------------------------------------------------------
# find_slots
# ---------------------------------------------------------------------------

FIND_SLOTS_RESPONSE = {
    "results": {
        "venues": [
            {
                "slots": [
                    {
                        "config": {"token": "cfg-abc"},
                        "date": {"start": "2026-03-15 19:00:00"},
                    },
                    {
                        "config": {"token": "cfg-def"},
                        "date": {"start": "2026-03-15 20:00:00"},
                    },
                ]
            }
        ]
    }
}


def test_find_slots_returns_slots():
    client = make_client()
    with patch.object(client.session, "get", return_value=mock_response(FIND_SLOTS_RESPONSE)):
        slots = client.find_slots(venue_id=5286, date="2026-03-15", party_size=2)

    assert len(slots) == 2
    assert slots[0].config_id == "cfg-abc"
    assert slots[0].start_time == datetime(2026, 3, 15, 19, 0, 0)
    assert slots[1].config_id == "cfg-def"
    assert slots[1].start_time == datetime(2026, 3, 15, 20, 0, 0)


def test_find_slots_skips_missing_config_token():
    data = {
        "results": {
            "venues": [
                {
                    "slots": [
                        {"config": {}, "date": {"start": "2026-03-15 19:00:00"}},  # no token
                        {"config": {"token": "cfg-ok"}, "date": {"start": "2026-03-15 20:00:00"}},
                    ]
                }
            ]
        }
    }
    client = make_client()
    with patch.object(client.session, "get", return_value=mock_response(data)):
        slots = client.find_slots(5286, "2026-03-15", 2)

    assert len(slots) == 1
    assert slots[0].config_id == "cfg-ok"


def test_find_slots_skips_bad_date():
    data = {
        "results": {
            "venues": [
                {
                    "slots": [
                        {"config": {"token": "cfg-bad"}, "date": {"start": "not-a-date"}},
                        {"config": {"token": "cfg-ok"}, "date": {"start": "2026-03-15 20:00:00"}},
                    ]
                }
            ]
        }
    }
    client = make_client()
    with patch.object(client.session, "get", return_value=mock_response(data)):
        slots = client.find_slots(5286, "2026-03-15", 2)

    assert len(slots) == 1
    assert slots[0].config_id == "cfg-ok"


def test_find_slots_empty_venues():
    client = make_client()
    with patch.object(client.session, "get", return_value=mock_response({"results": {"venues": []}})):
        slots = client.find_slots(5286, "2026-03-15", 2)
    assert slots == []


def test_find_slots_raises_on_http_error():
    client = make_client()
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.HTTPError("403")
    with patch.object(client.session, "get", return_value=resp):
        with pytest.raises(requests.HTTPError):
            client.find_slots(5286, "2026-03-15", 2)


# ---------------------------------------------------------------------------
# get_booking_token
# ---------------------------------------------------------------------------

def test_get_booking_token_returns_token():
    client = make_client()
    data = {"book_token": {"value": "btoken-xyz"}}
    with patch.object(client.session, "post", return_value=mock_response(data)):
        token = client.get_booking_token("cfg-abc", "2026-03-15", 2)
    assert token == "btoken-xyz"


def test_get_booking_token_raises_if_missing():
    client = make_client()
    with patch.object(client.session, "post", return_value=mock_response({})):
        with pytest.raises(ValueError, match="book_token"):
            client.get_booking_token("cfg-abc", "2026-03-15", 2)


# ---------------------------------------------------------------------------
# book
# ---------------------------------------------------------------------------

def test_book_returns_confirmation():
    client = make_client()
    confirmation = {"resy_token": "RES-123", "reservation_id": 99}
    with patch.object(client.session, "post", return_value=mock_response(confirmation)):
        result = client.book("btoken-xyz", payment_method_id=42)
    assert result["resy_token"] == "RES-123"


def test_book_sends_correct_payload():
    client = make_client()
    mock_post = MagicMock(return_value=mock_response({"resy_token": "ok"}))
    with patch.object(client.session, "post", mock_post):
        client.book("btoken-xyz", payment_method_id=42)

    _, kwargs = mock_post.call_args
    payload = kwargs["data"]
    assert payload["book_token"] == "btoken-xyz"
    assert payload["struct_payment_method"] == '{"id":42}'
