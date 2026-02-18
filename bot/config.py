from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator


class Target(BaseModel):
    venue_id: int
    venue_name: str
    date: str                        # "YYYY-MM-DD"
    party_size: int
    time_preferences: list[str]      # ["HH:MM", ...] in priority order
    release_time: Optional[str] = None   # "HH:MM" for midnight-drop snipe; null for polling
    poll_interval_seconds: int = 60

    @field_validator("date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        from datetime import date
        date.fromisoformat(v)        # raises ValueError on bad format
        return v

    @field_validator("time_preferences")
    @classmethod
    def validate_times(cls, v: list[str]) -> list[str]:
        from datetime import time
        for t in v:
            time.fromisoformat(t)    # raises ValueError on bad format
        return v

    @field_validator("release_time")
    @classmethod
    def validate_release_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            from datetime import time
            time.fromisoformat(v)
        return v

    @property
    def snipe_mode(self) -> bool:
        return self.release_time is not None


class EmailConfig(BaseModel):
    smtp_server: str
    smtp_port: int
    from_address: str
    to_address: str


class NotificationConfig(BaseModel):
    email: EmailConfig


class AppConfig(BaseModel):
    targets: list[Target]
    notifications: NotificationConfig


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return AppConfig.model_validate(data)
