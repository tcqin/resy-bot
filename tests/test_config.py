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
    date="2026-03-15",
    party_size=2,
    time_preferences=["19:00", "19:30"],
    release_time="00:00",
    poll_interval_seconds=30,
)


def test_target_valid():
    t = Target(**VALID_TARGET_KWARGS)
    assert t.venue_id == 5286
    assert t.snipe_mode is True


def test_target_snipe_mode_false_when_no_release_time():
    t = Target(**{**VALID_TARGET_KWARGS, "release_time": None})
    assert t.snipe_mode is False


def test_target_invalid_date():
    with pytest.raises(ValidationError, match="date"):
        Target(**{**VALID_TARGET_KWARGS, "date": "not-a-date"})


def test_target_invalid_time_preference():
    with pytest.raises(ValidationError, match="time_preferences"):
        Target(**{**VALID_TARGET_KWARGS, "time_preferences": ["25:00"]})


def test_target_invalid_release_time():
    with pytest.raises(ValidationError, match="release_time"):
        Target(**{**VALID_TARGET_KWARGS, "release_time": "99:99"})


def test_target_defaults():
    t = Target(
        venue_id=1,
        venue_name="Test",
        date="2026-01-01",
        party_size=2,
        time_preferences=["20:00"],
    )
    assert t.release_time is None
    assert t.poll_interval_seconds == 60
    assert t.snipe_mode is False


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

MINIMAL_YAML = textwrap.dedent("""\
    targets:
      - venue_id: 1
        venue_name: "Test Venue"
        date: "2026-06-01"
        party_size: 2
        time_preferences:
          - "19:00"
        release_time: "00:00"
        poll_interval_seconds: 30
    notifications:
      email:
        smtp_server: "smtp.gmail.com"
        smtp_port: 587
        from_address: "a@example.com"
        to_address: "b@example.com"
""")


def test_load_config(tmp_path: Path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(MINIMAL_YAML)
    cfg = load_config(cfg_file)
    assert isinstance(cfg, AppConfig)
    assert len(cfg.targets) == 1
    assert cfg.targets[0].venue_name == "Test Venue"
    assert cfg.notifications.email.smtp_server == "smtp.gmail.com"


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")
