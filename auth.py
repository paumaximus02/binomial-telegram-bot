"""Access control for authorized Telegram users and groups."""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationHandlerStop, ContextTypes, TypeHandler

logger = logging.getLogger(__name__)

GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})


def load_id_set(env_var: str, label: str) -> frozenset[int]:
    raw = os.getenv(env_var, "").strip()
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
                f"Invalid {label} entry: {part!r}. Use comma-separated numeric IDs."
            ) from exc
    return frozenset(ids)


ALLOWED_USER_IDS = load_id_set("ALLOWED_USER_IDS", "ALLOWED_USER_IDS")
ALLOWED_GROUP_IDS = load_id_set("ALLOWED_GROUP_IDS", "ALLOWED_GROUP_IDS")


def is_access_restricted() -> bool:
    return bool(ALLOWED_USER_IDS or ALLOWED_GROUP_IDS)


def is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in GROUP_CHAT_TYPES)


def is_update_authorized(update: Update) -> bool:
    if not is_access_restricted():
        return True

    chat = update.effective_chat
    user = update.effective_user

    if chat and chat.type in GROUP_CHAT_TYPES:
        if ALLOWED_GROUP_IDS:
            return chat.id in ALLOWED_GROUP_IDS
        return False

    if ALLOWED_USER_IDS and user:
        return user.id in ALLOWED_USER_IDS
    return False


def _is_whoami(update: Update) -> bool:
    message = update.effective_message
    return bool(message and message.text and message.text.strip().startswith("/whoami"))


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not update.effective_message:
        return

    lines = [
        f"Your Telegram user ID is `{user.id}`.",
        f"Username: @{user.username}" if user.username else "Username: not set",
    ]

    if chat and chat.type in GROUP_CHAT_TYPES:
        lines.extend(
            [
                "",
                f"Group chat ID is `{chat.id}`.",
                f"Chat title: {chat.title or 'unknown'}",
            ]
        )

    if not is_access_restricted():
        lines.extend(["", "Access control is off (no allowlists configured)."])
    elif is_update_authorized(update):
        lines.extend(["", "You can use this bot in this chat."])
    elif is_group_chat(update):
        lines.extend(
            [
                "",
                "This group is not on the allowlist.",
                "Send the group chat ID above to the bot owner.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "This bot is private.",
                "Send your user ID to the owner to request DM access.",
            ]
        )

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def access_gatekeeper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_update_authorized(update):
        return

    if _is_whoami(update):
        return

    user = update.effective_user
    chat = update.effective_chat
    logger.warning(
        "Blocked unauthorized access user=%s chat=%s type=%s",
        user.id if user else "unknown",
        chat.id if chat else "unknown",
        chat.type if chat else "unknown",
    )

    if update.callback_query:
        await update.callback_query.answer("You are not authorized to use this bot.", show_alert=True)
        return

    if update.effective_message:
        if is_group_chat(update):
            text = (
                "This bot is not enabled in this group.\n\n"
                "An admin can send /whoami here to get the group chat ID for the allowlist."
            )
        else:
            text = (
                "This bot is private.\n\n"
                "Send /whoami to get your Telegram user ID, then ask the owner for access."
            )
        await update.effective_message.reply_text(text)
    raise ApplicationHandlerStop


def register_access_control(application: Application) -> None:
    if ALLOWED_USER_IDS:
        logger.info("DM access enabled for %d user(s).", len(ALLOWED_USER_IDS))
    if ALLOWED_GROUP_IDS:
        logger.info("Group access enabled for %d group(s).", len(ALLOWED_GROUP_IDS))
    if not is_access_restricted():
        logger.warning("No allowlists set. The bot is open to everyone.")

    application.add_handler(TypeHandler(Update, access_gatekeeper), group=-1)
