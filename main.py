#!/usr/bin/env python3
"""Entry point for the Resy reservation bot."""
from __future__ import annotations

import logging
import os
import signal
import sys
import time

from dotenv import load_dotenv

from bot.config import load_config
from bot.notifier import Notifier
from bot.resy_client import ResyClient
from bot.scheduler import Scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        logger.error("Missing required environment variable: %s", name)
        sys.exit(1)
    return value


def main() -> None:
    load_dotenv()

    resy_api_key = _require_env("RESY_API_KEY")
    resy_auth_token = _require_env("RESY_AUTH_TOKEN")
    payment_method_id = int(_require_env("RESY_PAYMENT_METHOD_ID"))
    smtp_password = _require_env("SMTP_PASSWORD")

    config = load_config("config.yaml")
    logger.info("Loaded %d target(s) from config.yaml", len(config.targets))

    client = ResyClient(api_key=resy_api_key, auth_token=resy_auth_token)

    notifier = Notifier(
        notification_config=config.notifications,
        smtp_password=smtp_password,
    )

    scheduler = Scheduler(
        client=client,
        config=config,
        notifier=notifier,
        payment_method_id=payment_method_id,
    )

    def _shutdown(signum: int, frame: object) -> None:
        logger.info("Received signal %d — shutting down.", signum)
        scheduler.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    scheduler.start()
    logger.info("Bot is running. Press Ctrl+C to stop.")

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
