"""Entry point for the Binomial Option Pricer Telegram bot."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from telegram.ext import Application

from handlers import register_handlers
from utils import health_check

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def get_bot_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and add your token.")
    return token


async def post_init(application: Application) -> None:
    if await health_check():
        logger.info("Connected to Binomial Pricer API.")
    else:
        logger.warning("Binomial Pricer API health check failed. Bot will still start.")


def configure_event_loop() -> None:
    """Ensure a running event loop exists (required on Python 3.10+ Linux/cloud)."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def main() -> None:
    configure_event_loop()
    token = get_bot_token()
    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )
    register_handlers(application)
    logger.info("Starting binomial-telegram-bot...")
    application.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
