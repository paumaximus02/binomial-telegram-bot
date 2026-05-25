"""Access control for authorized Telegram users and groups."""

from __future__ import annotations

import html
import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationHandlerStop, ContextTypes, TypeHandler

logger = logging.getLogger(__name__)

GROUP_CHAT_TYPES = frozenset({"group", "supergroup"})
PRIVATE_CHAT_TYPES = frozenset({"private"})

ALLOWED_USER_IDS: frozenset[int] = frozenset()
ALLOWED_GROUP_IDS: frozenset[int] = frozenset()


def load_id_set(env_var: str, label: str) -> frozenset[int]:
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return frozenset()

    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip().strip('"').strip("'")
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid {label} entry: {part!r}. Use comma-separated numeric IDs."
            ) from exc
    return frozenset(ids)


def reload_allowlists() -> None:
    global ALLOWED_USER_IDS, ALLOWED_GROUP_IDS
    ALLOWED_USER_IDS = load_id_set("ALLOWED_USER_IDS", "ALLOWED_USER_IDS")
    ALLOWED_GROUP_IDS = load_id_set("ALLOWED_GROUP_IDS", "ALLOWED_GROUP_IDS")


def is_access_restricted() -> bool:
    return bool(ALLOWED_USER_IDS or ALLOWED_GROUP_IDS)


def is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in GROUP_CHAT_TYPES)


def is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in PRIVATE_CHAT_TYPES)


def is_user_allowlisted(user_id: int | None) -> bool:
    if user_id is None or not ALLOWED_USER_IDS:
        return False
    return user_id in ALLOWED_USER_IDS


def is_group_allowlisted(chat_id: int | None) -> bool:
    if chat_id is None or not ALLOWED_GROUP_IDS:
        return False
    return chat_id in ALLOWED_GROUP_IDS


def is_update_authorized(update: Update) -> bool:
    if not is_access_restricted():
        return True

    chat = update.effective_chat
    user = update.effective_user

    if is_group_chat(update):
        return is_group_allowlisted(chat.id if chat else None)

    if is_private_chat(update):
        return is_user_allowlisted(user.id if user else None)

    return is_user_allowlisted(user.id if user else None)


def _is_whoami(update: Update) -> bool:
    message = update.effective_message
    if not message or not message.text:
        return False
    command = message.text.strip().split()[0].split("@")[0]
    return command == "/whoami"


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if not user or not update.effective_message:
        return

    lines = [
        f"Your Telegram user ID is <code>{user.id}</code>.",
        f"Username: @{html.escape(user.username)}" if user.username else "Username: not set",
        "",
        f"User allowlisted: {'yes' if is_user_allowlisted(user.id) else 'no'}",
    ]

    if chat and chat.type in GROUP_CHAT_TYPES:
        lines.extend(
            [
                "",
                f"Group chat ID is <code>{chat.id}</code>.",
                f"Chat title: {html.escape(chat.title or 'unknown')}",
                f"Group allowlisted: {'yes' if is_group_allowlisted(chat.id) else 'no'}",
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
                "Add the group chat ID above to ALLOWED_GROUP_IDS on Render.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "DM access is not enabled for your user ID.",
                "Add your user ID above to ALLOWED_USER_IDS on Render.",
                "Group access and DM access are configured separately.",
            ]
        )

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


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
                "Send /whoami here to get the group chat ID for ALLOWED_GROUP_IDS."
            )
        else:
            text = (
                "This bot is not enabled in private chat for your user ID.\n\n"
                "Send /whoami to get your user ID, then add it to ALLOWED_USER_IDS on Render."
            )
        await update.effective_message.reply_text(text)
    raise ApplicationHandlerStop


def register_access_control(application: Application) -> None:
    reload_allowlists()

    if ALLOWED_USER_IDS:
        logger.info("DM access enabled for user IDs: %s", ", ".join(str(i) for i in sorted(ALLOWED_USER_IDS)))
    else:
        logger.info("ALLOWED_USER_IDS is not set; private chat access is disabled when groups are restricted.")

    if ALLOWED_GROUP_IDS:
        logger.info("Group access enabled for chat IDs: %s", ", ".join(str(i) for i in sorted(ALLOWED_GROUP_IDS)))
    else:
        logger.info("ALLOWED_GROUP_IDS is not set; group access is disabled when users are restricted.")

    if not is_access_restricted():
        logger.warning("No allowlists set. The bot is open to everyone.")

    application.add_handler(TypeHandler(Update, access_gatekeeper), group=-1)
