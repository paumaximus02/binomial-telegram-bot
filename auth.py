"""Access control for authorized Telegram users."""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationHandlerStop, ContextTypes, TypeHandler

logger = logging.getLogger(__name__)


def load_allowed_user_ids() -> frozenset[int]:
    raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    if not raw:
        return frozenset()

    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid ALLOWED_USER_IDS entry: {part!r}. Use comma-separated numeric IDs."
            ) from exc
    return frozenset(ids)


ALLOWED_USER_IDS = load_allowed_user_ids()


def is_access_restricted() -> bool:
    return bool(ALLOWED_USER_IDS)


def is_authorized(user_id: int | None) -> bool:
    if user_id is None:
        return False
    if not ALLOWED_USER_IDS:
        return True
    return user_id in ALLOWED_USER_IDS


def _is_whoami(update: Update) -> bool:
    message = update.effective_message
    return bool(message and message.text and message.text.strip().startswith("/whoami"))


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.effective_message:
        return

    lines = [
        f"Your Telegram user ID is `{user.id}`.",
        f"Username: @{user.username}" if user.username else "Username: not set",
    ]
    if is_access_restricted() and not is_authorized(user.id):
        lines.append("")
        lines.append("This bot is private. Send your user ID to the owner to request access.")
    elif is_access_restricted():
        lines.append("")
        lines.append("You are on the allowlist.")
    else:
        lines.append("")
        lines.append("Access control is off (ALLOWED_USER_IDS is not set).")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def access_gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if is_authorized(user.id if user else None):
        return

    if _is_whoami(update):
        return

    logger.warning("Blocked unauthorized user %s", user.id if user else "unknown")

    if update.callback_query:
        await update.callback_query.answer("You are not authorized to use this bot.", show_alert=True)
        return

    if update.effective_message:
        await update.effective_message.reply_text(
            "This bot is private.\n\n"
            "Send /whoami to get your Telegram user ID, then ask the owner for access."
        )
    raise ApplicationHandlerStop


def register_access_control(application: Application) -> None:
    if ALLOWED_USER_IDS:
        logger.info("Access control enabled for %d user(s).", len(ALLOWED_USER_IDS))
    else:
        logger.warning("ALLOWED_USER_IDS is not set. The bot is open to everyone.")

    application.add_handler(TypeHandler(Update, access_gatekeeper), group=-1)
