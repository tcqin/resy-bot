"""Tests for bot/config.py — Pydantic model validation and YAML loading."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from bot.config import AppConfig, Target, load_config


# ---------------------------------------------------------------------------
# Target validation
# ---------------------------------------------------------------------------

VALID_TARGET_KWARGS = dict(
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


def test_target_valid():
    t = Target(**VALID_TARGET_KWARGS)
    assert t.venue_id == 5286
    assert t.days_of_week == ["Tuesday", "Thursday"]
    assert t.time_center == "19:00"
    assert t.time_radius_minutes == 30


def test_target_invalid_start_date():
    with pytest.raises(ValidationError, match="start_date"):
        Target(**{**VALID_TARGET_KWARGS, "start_date": "not-a-date"})


def test_target_invalid_end_date():
    with pytest.raises(ValidationError, match="end_date"):
        Target(**{**VALID_TARGET_KWARGS, "end_date": "not-a-date"})


def test_target_invalid_time_center():
    with pytest.raises(ValidationError, match="time_center"):
        Target(**{**VALID_TARGET_KWARGS, "time_center": "25:00"})


def test_target_invalid_days_of_week():
    with pytest.raises(ValidationError, match="days_of_week"):
        Target(**{**VALID_TARGET_KWARGS, "days_of_week": ["Blursday"]})


def test_target_defaults():
    t = Target(
        venue_id=1,
        venue_name="Test",
        start_date="2026-01-01",
        end_date="2026-03-31",
        party_size=2,
        days_of_week=["Friday"],
        time_center="20:00",
    )
    assert t.time_radius_minutes == 30
    assert t.venue_timezone == "America/New_York"
    assert t.poll_interval_seconds == 60


def test_target_all_weekdays_valid():
    for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        t = Target(**{**VALID_TARGET_KWARGS, "days_of_week": [day]})
        assert t.days_of_week == [day]


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

MINIMAL_YAML = textwrap.dedent("""\
    targets:
      - venue_id: 1
        venue_name: "Test Venue"
        start_date: "2026-06-01"
        end_date: "2026-08-31"
        party_size: 2
        days_of_week:
          - "Tuesday"
          - "Thursday"
        time_center: "19:00"
        time_radius_minutes: 30
        poll_interval_seconds: 30
""")


def test_load_config(tmp_path: Path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(MINIMAL_YAML)
    cfg = load_config(cfg_file)
    assert isinstance(cfg, AppConfig)
    assert len(cfg.targets) == 1
    assert cfg.targets[0].venue_name == "Test Venue"
    assert cfg.targets[0].days_of_week == ["Tuesday", "Thursday"]


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")
