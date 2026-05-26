"""Telegram command, conversation, and callback handlers."""

from __future__ import annotations

import logging
from datetime import date

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from auth import whoami_command
from presets import PRESETS, build_preset_params, get_preset
from utils import (
    ApiError,
    IVParams,
    PriceParams,
    clear_chat_flow_state,
    clear_edit_target,
    escape_md,
    format_iv_result,
    format_price_result,
    load_draft_iv,
    load_draft_price,
    load_edit_session,
    load_price_session,
    parse_days_or_date,
    parse_option_type,
    parse_positive_float,
    parse_rate,
    parse_volatility,
    pop_draft_iv,
    pop_draft_price,
    pop_edit_field,
    price_option,
    solve_iv,
    store_draft_iv,
    store_draft_price,
    store_edit_field,
    store_edit_session,
    store_edit_target,
    store_iv_params,
    store_price_session,
    strip_prefix,
)

logger = logging.getLogger(__name__)

(
    PRICE_SYMBOL,
    PRICE_STRIKE,
    PRICE_VOL,
    PRICE_EXPIRY,
    PRICE_TYPE,
    IV_MARKET,
    IV_SYMBOL,
    IV_STRIKE,
    IV_EXPIRY,
    IV_TYPE,
    EDIT_SELECT,
    EDIT_VALUE,
) = range(12)

WELCOME_TEXT = (
    "👋 *Welcome to Binomial Option Pricer Bot*\n\n"
    "Price options and calculate implied volatility using your deployed "
    "binomial tree API\\.\n\n"
    "Pick a quick preset below, or try:\n"
    "• /price — step\\-by\\-step custom pricing\n"
    "• /iv — implied volatility solver\n"
    "• /help — instructions"
)

HELP_TEXT = (
    "📘 *Help*\n\n"
    "*Commands*\n"
    "• /start — welcome message and preset buttons\n"
    "• /price — build a custom option and price it\n"
    "• /iv — solve implied volatility from a market price\n"
    "• /help — show this message\n"
    "• /cancel — stop the current input flow\n\n"
    "*Quick presets*\n"
    "Tap a preset on /start to price instantly\\. After each result, use "
    "*Change Parameter* to tweak strike, time, volatility, rate, or option type "
    "and recalculate as many times as you like\\.\n\n"
    "*Custom pricing inputs*\n"
    "Symbol → strike → volatility → expiry \\(days or YYYY\\-MM\\-DD\\) → call/put\n\n"
    "*IV flow inputs*\n"
    "Market price → symbol → strike → expiry → call/put\n\n"
    "Volatility and rates accept decimals \\(0\\.25\\) or percents \\(25%\\)\\."
)


def preset_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(preset.label, callback_data=f"preset:{preset.id}")]
        for preset in PRESETS
    ]
    rows.append([InlineKeyboardButton("Custom Input", callback_data="preset:custom")])
    return InlineKeyboardMarkup(rows)


def edit_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Change Parameter", callback_data="edit:menu")]]
    )


def edit_param_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Strike", callback_data="edit:strike"),
                InlineKeyboardButton("Time", callback_data="edit:time"),
            ],
            [
                InlineKeyboardButton("Volatility", callback_data="edit:vol"),
                InlineKeyboardButton("Rate", callback_data="edit:rate"),
            ],
            [InlineKeyboardButton("Option Type", callback_data="edit:type")],
            [InlineKeyboardButton("Done", callback_data="edit:done")],
        ]
    )


def option_type_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Call", callback_data=f"{prefix}:call"),
                InlineKeyboardButton("Put", callback_data=f"{prefix}:put"),
            ]
        ]
    )


async def _reply(update: Update, text: str, **kwargs) -> Message | None:
    message = update.effective_message
    if message:
        return await message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2, **kwargs)
    return None


def _chat_id(update: Update) -> int | None:
    chat = update.effective_chat
    return chat.id if chat else None


async def _send_api_error(update: Update, exc: ApiError) -> None:
    await _reply(update, f"⚠️ *API error*\n\n{escape_md(str(exc))}")


async def _send_network_error(update: Update) -> None:
    await _reply(
        update,
        "⚠️ *Connection error*\n\nCould not reach the pricing API\\. "
        "Check `API_BASE_URL` and try again\\.",
    )


async def send_price_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    params: PriceParams,
    *,
    label: str | None = None,
) -> bool:
    try:
        data = await price_option(params)
    except ApiError as exc:
        await _send_api_error(update, exc)
        return False
    except httpx.RequestError:
        logger.exception("Network error while pricing option")
        await _send_network_error(update)
        return False

    params.spot = float(data["spot"])
    params.strike = float(data["strike"])
    params.rate = float(data["rate"])
    params.vol = float(data["vol"])

    text = format_price_result(data, label or params.preset_label)
    chat_id = _chat_id(update)
    if chat_id is None:
        return False

    sent = await _reply(update, text, reply_markup=edit_menu_keyboard())
    if sent is None:
        return False

    store_price_session(context, chat_id, sent.message_id, params)
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, WELCOME_TEXT, reply_markup=preset_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP_TEXT)


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is not None:
        clear_chat_flow_state(context, chat_id)
    await _reply(update, "Cancelled\\. Send /start or /price to begin again\\.")
    return ConversationHandler.END


async def preset_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END

    await query.answer()
    preset_id = strip_prefix(query.data, "preset:")

    if preset_id == "custom":
        await query.message.reply_text(
            "Let's build a custom option\\.\n\n"
            "Enter the *stock symbol* \\(e\\.g\\. AAPL\\)\\:",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        chat_id = query.message.chat_id
        store_draft_price(context, chat_id, PriceParams(valuation_date=date.today()))
        return PRICE_SYMBOL

    preset = get_preset(preset_id)
    if preset is None:
        await query.message.reply_text("Unknown preset\\. Try /start again\\.")
        return ConversationHandler.END

    await query.message.reply_text(
        f"Calculating *{escape_md(preset.label)}*\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    try:
        params = await build_preset_params(preset)
    except ApiError as exc:
        await _send_api_error(update, exc)
        return ConversationHandler.END
    except httpx.RequestError:
        await _send_network_error(update)
        return ConversationHandler.END

    await send_price_result(update, context, params, label=preset.label)
    return ConversationHandler.END


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END
    store_draft_price(context, chat_id, PriceParams(valuation_date=date.today()))
    await _reply(
        update,
        "Custom pricing started\\.\n\nEnter the *stock symbol* \\(e\\.g\\. AAPL\\)\\:",
    )
    return PRICE_SYMBOL


async def price_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END

    symbol = (update.message.text or "").strip().upper()
    if not symbol.isalpha() or len(symbol) > 10:
        await _reply(update, "Please enter a valid ticker symbol \\(e\\.g\\. AMZN\\)\\.")
        return PRICE_SYMBOL

    draft = load_draft_price(context, chat_id)
    if draft is None:
        await _reply(update, "Session expired\\. Send /price to start again\\.")
        return ConversationHandler.END

    draft.symbol = symbol
    await _reply(
        update,
        f"Symbol set to *{escape_md(symbol)}*\\.\n\n"
        "Enter the *strike price* \\(e\\.g\\. 185\\)\\:",
    )
    return PRICE_STRIKE


async def price_strike(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END

    draft = load_draft_price(context, chat_id)
    if draft is None:
        await _reply(update, "Session expired\\. Send /price to start again\\.")
        return ConversationHandler.END

    try:
        draft.strike = parse_positive_float(update.message.text or "", "Strike")
    except ValueError as exc:
        await _reply(update, escape_md(str(exc)))
        return PRICE_STRIKE

    await _reply(
        update,
        "Enter *volatility* as a decimal \\(0\\.25\\) or percent \\(25%\\)\\:",
    )
    return PRICE_VOL


async def price_vol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END

    draft = load_draft_price(context, chat_id)
    if draft is None:
        await _reply(update, "Session expired\\. Send /price to start again\\.")
        return ConversationHandler.END

    try:
        draft.vol = parse_volatility(update.message.text or "")
    except ValueError as exc:
        await _reply(update, escape_md(str(exc)))
        return PRICE_VOL

    await _reply(
        update,
        "Enter *time to expiry* as days \\(30\\) or a date \\(YYYY\\-MM\\-DD\\)\\:",
    )
    return PRICE_EXPIRY


async def price_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END

    draft = load_draft_price(context, chat_id)
    if draft is None:
        await _reply(update, "Session expired\\. Send /price to start again\\.")
        return ConversationHandler.END

    try:
        draft.expiry = parse_days_or_date(update.message.text or "", draft.valuation_date)
    except ValueError as exc:
        await _reply(update, escape_md(str(exc)))
        return PRICE_EXPIRY

    await _reply(
        update,
        "Choose the *option type*\\:",
        reply_markup=option_type_keyboard("price_type"),
    )
    return PRICE_TYPE


async def price_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END

    await query.answer()
    chat_id = query.message.chat_id
    option_type = strip_prefix(query.data, "price_type:")
    draft = pop_draft_price(context, chat_id)
    if draft is None:
        await query.message.reply_text("Session expired\\. Send /price to start again\\.")
        return ConversationHandler.END

    draft.option_type = option_type
    draft.preset_label = "Custom Option"

    await query.message.reply_text(
        "Calculating your custom option\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    await send_price_result(update, context, draft, label="Custom Option")
    return ConversationHandler.END


async def iv_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END
    store_draft_iv(context, chat_id, IVParams(valuation_date=date.today()))
    await _reply(
        update,
        "Implied volatility flow started\\.\n\n"
        "Enter the observed *market option price* \\(e\\.g\\. 5\\.20\\)\\:",
    )
    return IV_MARKET


async def iv_market_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END

    draft = load_draft_iv(context, chat_id)
    if draft is None:
        await _reply(update, "Session expired\\. Send /iv to start again\\.")
        return ConversationHandler.END

    try:
        draft.market_price = parse_positive_float(update.message.text or "", "Market price")
    except ValueError as exc:
        await _reply(update, escape_md(str(exc)))
        return IV_MARKET

    await _reply(update, "Enter the *stock symbol* \\(e\\.g\\. AAPL\\)\\:")
    return IV_SYMBOL


async def iv_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END

    symbol = (update.message.text or "").strip().upper()
    if not symbol.isalpha() or len(symbol) > 10:
        await _reply(update, "Please enter a valid ticker symbol\\.")
        return IV_SYMBOL

    draft = load_draft_iv(context, chat_id)
    if draft is None:
        await _reply(update, "Session expired\\. Send /iv to start again\\.")
        return ConversationHandler.END

    draft.symbol = symbol
    await _reply(update, "Enter the *strike price*\\:")
    return IV_STRIKE


async def iv_strike(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END

    draft = load_draft_iv(context, chat_id)
    if draft is None:
        await _reply(update, "Session expired\\. Send /iv to start again\\.")
        return ConversationHandler.END

    try:
        draft.strike = parse_positive_float(update.message.text or "", "Strike")
    except ValueError as exc:
        await _reply(update, escape_md(str(exc)))
        return IV_STRIKE

    await _reply(
        update,
        "Enter *time to expiry* as days \\(30\\) or a date \\(YYYY\\-MM\\-DD\\)\\:",
    )
    return IV_EXPIRY


async def iv_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END

    draft = load_draft_iv(context, chat_id)
    if draft is None:
        await _reply(update, "Session expired\\. Send /iv to start again\\.")
        return ConversationHandler.END

    try:
        draft.expiry = parse_days_or_date(update.message.text or "", draft.valuation_date)
    except ValueError as exc:
        await _reply(update, escape_md(str(exc)))
        return IV_EXPIRY

    await _reply(
        update,
        "Choose the *option type*\\:",
        reply_markup=option_type_keyboard("iv_type"),
    )
    return IV_TYPE


async def iv_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END

    await query.answer()
    chat_id = query.message.chat_id
    draft = pop_draft_iv(context, chat_id)
    if draft is None:
        await query.message.reply_text("Session expired\\. Send /iv to start again\\.")
        return ConversationHandler.END

    draft.option_type = strip_prefix(query.data, "iv_type:")

    try:
        data = await solve_iv(draft)
    except ApiError as exc:
        await _send_api_error(update, exc)
        return ConversationHandler.END
    except httpx.RequestError:
        await _send_network_error(update)
        return ConversationHandler.END

    store_iv_params(context, chat_id, draft)
    await _reply(update, format_iv_result(data))
    return ConversationHandler.END


async def edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.message:
        return ConversationHandler.END

    await query.answer()
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    params = load_price_session(context, chat_id, message_id)
    if params is None:
        await query.message.reply_text(
            "This result is no longer editable\\. Run /start or /price again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    store_edit_target(context, chat_id, message_id)
    await query.message.reply_text(
        "Which parameter would you like to change?",
        reply_markup=edit_param_keyboard(),
    )
    return EDIT_SELECT


async def edit_select_param(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END

    await query.answer()
    action = strip_prefix(query.data, "edit:")

    if action == "done":
        clear_edit_target(context)
        await query.message.reply_text(
            "All set\\. Tap /start for presets or /price for a new custom run\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    if action == "type":
        await query.message.reply_text(
            "Select the new option type:",
            reply_markup=option_type_keyboard("edit_type"),
        )
        return EDIT_SELECT

    prompts = {
        "strike": "Enter the new *strike price* \\(e\\.g\\. 180\\)\\:",
        "time": "Enter the new *time to expiry* as days \\(30\\) or date \\(YYYY\\-MM\\-DD\\)\\:",
        "vol": "Enter the new *volatility* \\(0\\.25 or 25%\\)\\:",
        "rate": "Enter the new *risk\\-free rate* \\(0\\.05 or 5%\\)\\:",
    }
    if action not in prompts:
        return ConversationHandler.END

    chat_id = query.message.chat_id
    if load_edit_session(context) is None:
        await query.message.reply_text(
            "This result is no longer editable\\. Run /start or /price again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    store_edit_field(context, chat_id, action)
    await query.message.reply_text(prompts[action], parse_mode=ParseMode.MARKDOWN_V2)
    return EDIT_VALUE


async def edit_type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not query or not query.data:
        return ConversationHandler.END

    await query.answer()
    params = load_edit_session(context)
    if params is None:
        await query.message.reply_text(
            "This result is no longer editable\\. Run /start or /price again\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ConversationHandler.END

    params.option_type = strip_prefix(query.data, "edit_type:")
    store_edit_session(context, params)

    await query.message.reply_text("Recalculating\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
    await send_price_result(update, context, params)
    clear_edit_target(context)
    return ConversationHandler.END


async def edit_receive_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = _chat_id(update)
    if chat_id is None:
        return ConversationHandler.END

    params = load_edit_session(context)
    field = pop_edit_field(context, chat_id)
    if params is None or field is None:
        await _reply(update, "Edit session expired\\. Use /start or /price again\\.")
        return ConversationHandler.END

    text = update.message.text or ""
    try:
        if field == "strike":
            params.strike = parse_positive_float(text, "Strike")
        elif field == "time":
            params.expiry = parse_days_or_date(text, params.valuation_date)
        elif field == "vol":
            params.vol = parse_volatility(text)
        elif field == "rate":
            params.rate = parse_rate(text)
        else:
            await _reply(update, "Unknown parameter\\. Try *Change Parameter* again\\.")
            return ConversationHandler.END
    except ValueError as exc:
        await _reply(update, escape_md(str(exc)))
        store_edit_field(context, chat_id, field)
        return EDIT_VALUE

    store_edit_session(context, params)
    await _reply(update, "Recalculating\\.\\.\\.")
    await send_price_result(update, context, params)
    clear_edit_target(context)
    return ConversationHandler.END


def build_price_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("price", price_command),
            CallbackQueryHandler(preset_selected, pattern=r"^preset:"),
        ],
        states={
            PRICE_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_symbol)],
            PRICE_STRIKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_strike)],
            PRICE_VOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_vol)],
            PRICE_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_expiry)],
            PRICE_TYPE: [CallbackQueryHandler(price_type_selected, pattern=r"^price_type:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )


def build_iv_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("iv", iv_command)],
        states={
            IV_MARKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, iv_market_price)],
            IV_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, iv_symbol)],
            IV_STRIKE: [MessageHandler(filters.TEXT & ~filters.COMMAND, iv_strike)],
            IV_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, iv_expiry)],
            IV_TYPE: [CallbackQueryHandler(iv_type_selected, pattern=r"^iv_type:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )


def build_edit_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_menu, pattern=r"^edit:menu$")],
        states={
            EDIT_SELECT: [
                CallbackQueryHandler(edit_select_param, pattern=r"^edit:"),
                CallbackQueryHandler(edit_type_selected, pattern=r"^edit_type:"),
            ],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_receive_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        allow_reentry=True,
    )


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("whoami", whoami_command))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(build_edit_conversation())
    application.add_handler(build_iv_conversation())
    application.add_handler(build_price_conversation())
