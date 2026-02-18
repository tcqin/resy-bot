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
# is_date_on_calendar
# ---------------------------------------------------------------------------

def test_is_date_on_calendar_true_when_venue_present():
    """Returns True when the venue appears in results, even with no slots."""
    client = make_client()
    data = {"results": {"venues": [{"slots": []}]}}  # venue present, fully booked
    with patch.object(client.session, "get", return_value=mock_response(data)):
        assert client.is_date_on_calendar(5286, "2026-03-15", 2) is True


def test_is_date_on_calendar_true_with_available_slots():
    """Returns True when the venue has open slots."""
    client = make_client()
    with patch.object(client.session, "get", return_value=mock_response(FIND_SLOTS_RESPONSE)):
        assert client.is_date_on_calendar(5286, "2026-03-15", 2) is True


def test_is_date_on_calendar_false_when_no_venues():
    """Returns False when date is outside the booking window (no venues returned)."""
    client = make_client()
    data = {"results": {"venues": []}}
    with patch.object(client.session, "get", return_value=mock_response(data)):
        assert client.is_date_on_calendar(5286, "2026-03-15", 2) is False


# ---------------------------------------------------------------------------
# find_slots
# ---------------------------------------------------------------------------

FIND_SLOTS_RESPONSE = {
    "results": {
        "venues": [
            {
                "venue": {
                    "url_slug": "4-charles-prime-rib",
                    "location": {"url_slug": "new-york-ny"},
                },
                "slots": [
                    {
                        "config": {"token": "cfg-abc"},
                        "date": {"start": "2026-03-15 19:00:00"},
                    },
                    {
                        "config": {"token": "cfg-def"},
                        "date": {"start": "2026-03-15 20:00:00"},
                    },
                ],
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


# ---------------------------------------------------------------------------
# discover_venue_schedule — venue API path
# ---------------------------------------------------------------------------

def test_discover_venue_schedule_api_returns_window_and_time():
    """When the venue API returns booking_window_days and booking_start_time, use them."""
    client = make_client()
    api_data = {"booking_window_days": 28, "booking_start_time": "09:00"}
    with patch.object(client.session, "get", return_value=mock_response(api_data)):
        window, release_time = client.discover_venue_schedule(venue_id=5286, party_size=2)
    assert window == 28
    assert release_time == "09:00"


def test_discover_venue_schedule_api_window_only():
    """When only booking_window_days is present, release_time is None."""
    client = make_client()
    api_data = {"booking_window_days": 30}
    with patch.object(client.session, "get", return_value=mock_response(api_data)):
        window, release_time = client.discover_venue_schedule(5286, 2)
    assert window == 30
    assert release_time is None


def test_discover_venue_schedule_api_nested_availability():
    """Handles booking_window_days nested inside an 'availability' key."""
    client = make_client()
    api_data = {"availability": {"booking_window_days": 21, "booking_start_time": "00:00"}}
    with patch.object(client.session, "get", return_value=mock_response(api_data)):
        window, release_time = client.discover_venue_schedule(5286, 2)
    assert window == 21
    assert release_time == "00:00"


# ---------------------------------------------------------------------------
# discover_venue_schedule — template text path
# ---------------------------------------------------------------------------

FIND_RESPONSE_WITH_TEMPLATES = {
    "results": {
        "venues": [
            {
                "venue": {
                    "url_slug": "4-charles-prime-rib",
                    "location": {"url_slug": "new-york-ny"},
                },
                "slots": [],
                "templates": {
                    "1": {
                        "content": {
                            "en-us": {
                                "need_to_know": {
                                    "body": "Reservations open 28 days in advance at 9am ET."
                                }
                            }
                        }
                    }
                },
            }
        ]
    }
}


def test_discover_venue_schedule_template_fallback():
    """When venue API gives no window, falls through to /4/find template parsing."""
    client = make_client()

    def fake_get(url, **kwargs):
        if "/3/venue" in url:
            return mock_response({})   # no useful data
        return mock_response(FIND_RESPONSE_WITH_TEMPLATES)

    with patch.object(client.session, "get", side_effect=fake_get):
        window, release_time = client.discover_venue_schedule(5286, 2)

    assert window == 28
    assert release_time == "09:00"


# ---------------------------------------------------------------------------
# _extract_need_to_know_text
# ---------------------------------------------------------------------------

def test_extract_need_to_know_text_concatenates_bodies():
    find_venue = {
        "templates": {
            "1": {"content": {"en-us": {"need_to_know": {"body": "Opens 30 days ahead."}}}},
            "2": {"content": {"en-us": {"need_to_know": {"body": "Available at 9am ET."}}}},
        }
    }
    text = ResyClient._extract_need_to_know_text(find_venue)
    assert "30 days ahead" in text
    assert "9am ET" in text


def test_extract_need_to_know_text_skips_missing_body():
    find_venue = {
        "templates": {
            "1": {"content": {"en-us": {"need_to_know": {}}}},  # no body key
            "2": {"content": {"en-us": {"need_to_know": {"body": "Opens at midnight."}}}},
        }
    }
    text = ResyClient._extract_need_to_know_text(find_venue)
    assert text == "Opens at midnight."


def test_extract_need_to_know_text_empty_when_no_templates():
    assert ResyClient._extract_need_to_know_text({}) == ""


# ---------------------------------------------------------------------------
# _probe_find_venue
# ---------------------------------------------------------------------------

def test_probe_find_venue_returns_first_venue():
    client = make_client()
    with patch.object(client.session, "get", return_value=mock_response(FIND_RESPONSE_WITH_TEMPLATES)):
        result = client._probe_find_venue(venue_id=834, party_size=2)
    assert result is not None
    assert result["venue"]["url_slug"] == "4-charles-prime-rib"


def test_probe_find_venue_returns_none_when_no_venues():
    client = make_client()
    empty = {"results": {"venues": []}}
    with patch.object(client.session, "get", return_value=mock_response(empty)):
        result = client._probe_find_venue(venue_id=834, party_size=2)
    assert result is None


# ---------------------------------------------------------------------------
# discover_venue_schedule — empirical fallback
# ---------------------------------------------------------------------------

def test_discover_venue_schedule_empirical_fallback():
    """When API and templates both give nothing, probes /4/find at decreasing windows."""
    client = make_client()

    session_get_calls = []

    def fake_get(url, **kwargs):
        session_get_calls.append(url)
        if "/3/venue" in url:
            raise Exception("not found")
        find_calls = [u for u in session_get_calls if "/4/find" in u]
        # First 3 find calls are the _probe_find_venue probes (return empty → no templates)
        # 4th call (empirical, 60 days): empty; 5th call (45 days): slots present
        if len(find_calls) <= 3:
            return mock_response({"results": {"venues": []}})
        if len(find_calls) == 4:
            return mock_response({"results": {"venues": []}})
        return mock_response(FIND_SLOTS_RESPONSE)

    with patch.object(client.session, "get", side_effect=fake_get):
        window, release_time = client.discover_venue_schedule(5286, 2)

    assert window == 45
    assert release_time is None


def test_discover_venue_schedule_all_empty_defaults_to_30():
    """When nothing is found anywhere, defaults to 30 days and None release time."""
    client = make_client()

    def fake_get(url, **kwargs):
        if "/3/venue" in url:
            raise Exception("API unavailable")
        return mock_response({"results": {"venues": []}})

    with patch.object(client.session, "get", side_effect=fake_get):
        window, release_time = client.discover_venue_schedule(5286, 2)

    assert window == 30
    assert release_time is None


# ---------------------------------------------------------------------------
# _parse_window_days / _parse_release_time
# ---------------------------------------------------------------------------

def test_parse_window_days_in_advance():
    assert ResyClient._parse_window_days("reservations open 30 days in advance") == 30


def test_parse_window_days_ahead():
    assert ResyClient._parse_window_days("books up to 28 days ahead") == 28


def test_parse_window_days_none():
    assert ResyClient._parse_window_days("no relevant text here") is None


def test_parse_release_time_midnight():
    assert ResyClient._parse_release_time("releases at midnight et") == "00:00"


def test_parse_release_time_noon():
    assert ResyClient._parse_release_time("available at noon") == "12:00"


def test_parse_release_time_9am():
    assert ResyClient._parse_release_time("opens at 9am") == "09:00"


def test_parse_release_time_with_minutes():
    assert ResyClient._parse_release_time("drops at 9:30am et") == "09:30"


def test_parse_release_time_pm():
    assert ResyClient._parse_release_time("releases at 1:00pm") == "13:00"


def test_parse_release_time_none():
    assert ResyClient._parse_release_time("no time mentioned here") is None


def test_parse_release_time_uppercase_am():
    assert ResyClient._parse_release_time("with each new date becoming available at 9 AM.") == "09:00"


def test_parse_release_time_uppercase_pm():
    assert ResyClient._parse_release_time("Reservations open at 6 PM.") == "18:00"
