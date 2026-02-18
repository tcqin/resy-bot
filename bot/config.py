from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

WEEKDAY_NAMES = {
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
}


class Target(BaseModel):
    venue_id: int
    venue_name: str
    start_date: str                       # "YYYY-MM-DD"
    end_date: str                         # "YYYY-MM-DD"
    party_size: int
    days_of_week: list[str]               # e.g. ["Tuesday", "Thursday"]
    time_center: str                      # e.g. "19:00"
    time_radius_minutes: int = 30
    venue_timezone: str = "America/New_York"
    poll_interval_seconds: int = 60

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import date
        date.fromisoformat(v)
        return v

    @field_validator("time_center")
    @classmethod
    def validate_time_center(cls, v: str) -> str:
        from datetime import time
        time.fromisoformat(v)
        return v

    @field_validator("days_of_week")
    @classmethod
    def validate_days_of_week(cls, v: list[str]) -> list[str]:
        for day in v:
            if day not in WEEKDAY_NAMES:
                raise ValueError(
                    f"Invalid day of week: {day!r}. "
                    f"Must be one of {sorted(WEEKDAY_NAMES)}"
                )
        return v


class AppConfig(BaseModel):
    targets: list[Target]


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return AppConfig.model_validate(data)
