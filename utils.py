"""Shared helpers: API client, formatting, and validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = 30.0


class ApiError(Exception):
    """Raised when the Binomial Pricer API returns an error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class PriceParams:
    symbol: str | None = None
    spot: float | None = None
    strike: float = 0.0
    vol: float = 0.0
    rate: float | None = None
    expiry: date | None = None
    valuation_date: date | None = None
    option_type: str = "call"
    style: str = "american"
    q: float = 0.0
    preset_label: str | None = None

    def copy(self) -> PriceParams:
        return PriceParams(
            symbol=self.symbol,
            spot=self.spot,
            strike=self.strike,
            vol=self.vol,
            rate=self.rate,
            expiry=self.expiry,
            valuation_date=self.valuation_date,
            option_type=self.option_type,
            style=self.style,
            q=self.q,
            preset_label=self.preset_label,
        )

    def to_api_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "strike": self.strike,
            "vol": self.vol,
            "expiry": (self.expiry or add_days(date.today(), 30)).isoformat(),
            "valuation_date": (self.valuation_date or date.today()).isoformat(),
            "option_type": self.option_type,
            "style": self.style,
            "q": self.q,
        }
        if self.spot is not None:
            payload["spot"] = self.spot
        elif self.symbol:
            payload["symbol"] = self.symbol.upper()
        if self.rate is not None:
            payload["rate"] = self.rate
        return payload


@dataclass
class IVParams:
    market_price: float = 0.0
    symbol: str | None = None
    spot: float | None = None
    strike: float = 0.0
    rate: float | None = None
    expiry: date | None = None
    valuation_date: date | None = None
    option_type: str = "call"
    style: str = "american"

    def to_api_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "market_price": self.market_price,
            "strike": self.strike,
            "expiry": (self.expiry or add_days(date.today(), 30)).isoformat(),
            "valuation_date": (self.valuation_date or date.today()).isoformat(),
            "option_type": self.option_type,
            "style": self.style,
        }
        if self.spot is not None:
            payload["spot"] = self.spot
        elif self.symbol:
            payload["symbol"] = self.symbol.upper()
        if self.rate is not None:
            payload["rate"] = self.rate
        return payload


def add_days(start: date, days: int) -> date:
    return start + timedelta(days=days)


def strip_prefix(value: str, prefix: str) -> str:
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


def parse_api_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"Request failed ({response.status_code})"

    detail = data.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(x) for x in item.get("loc", []))
                msg = item.get("msg", "")
                parts.append(f"{loc}: {msg}" if loc else msg)
            else:
                parts.append(str(item))
        return "; ".join(parts) or f"Request failed ({response.status_code})"
    if detail is not None:
        return str(detail)
    return f"Request failed ({response.status_code})"


async def api_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{API_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(url, json=payload)
    if response.is_success:
        return response.json()
    raise ApiError(parse_api_error(response), response.status_code)


async def api_get(path: str) -> dict[str, Any]:
    url = f"{API_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(url)
    if response.is_success:
        return response.json()
    raise ApiError(parse_api_error(response), response.status_code)


async def health_check() -> bool:
    try:
        data = await api_get("/health")
        return data.get("status") == "ok"
    except (ApiError, httpx.RequestError):
        return False


async def fetch_spot(symbol: str) -> dict[str, Any]:
    return await api_get(f"/market/spot/{symbol.upper()}")


async def price_option(params: PriceParams) -> dict[str, Any]:
    return await api_post("/price", params.to_api_payload())


async def solve_iv(params: IVParams) -> dict[str, Any]:
    return await api_post("/iv", params.to_api_payload())


def escape_md(text: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", str(text))


def fmt_money(value: float, digits: int = 2) -> str:
    return f"${value:,.{digits}f}"


def fmt_pct(value: float, digits: int = 2) -> str:
    return f"{value * 100:.{digits}f}%"


def fmt_num(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def intrinsic_value(spot: float, strike: float, option_type: str) -> float:
    if option_type == "call":
        return max(spot - strike, 0.0)
    return max(strike - spot, 0.0)


def format_price_result(data: dict[str, Any], label: str | None = None) -> str:
    spot = float(data["spot"])
    strike = float(data["strike"])
    option_type = str(data["option_type"])
    style = str(data["style"]).title()
    price = float(data["price"])
    intrinsic = intrinsic_value(spot, strike, option_type)
    time_value = max(price - intrinsic, 0.0)
    days = int(round(float(data["time_to_expiry"]) * 365))

    header = escape_md(label) if label else "Option Pricing Result"
    symbol = data.get("symbol")
    title_line = (
        f"*{escape_md(symbol)}* · {escape_md(style)} {escape_md(option_type.title())}"
        if symbol
        else f"{escape_md(style)} {escape_md(option_type.title())}"
    )

    lines = [
        f"📊 *{header}*",
        "",
        title_line,
        "",
        f"💰 *Current Stock Price:* {escape_md(fmt_money(spot))}",
        f"🎯 *Strike:* {escape_md(fmt_money(strike))}",
        f"📅 *Time to Expiry:* {escape_md(fmt_num(data['time_to_expiry']))} yrs \\({days} days\\)",
        f"📈 *Volatility:* {escape_md(fmt_pct(data['vol']))}",
        f"💵 *Risk\\-free Rate:* {escape_md(fmt_pct(data['rate']))}",
        "",
        f"*Option Price:* {escape_md(fmt_money(price, 4))}",
        "",
        "*Greeks*",
        f"• Delta: `{escape_md(fmt_num(data['delta']))}`",
        f"• Gamma: `{escape_md(fmt_num(data['gamma'], 6))}`",
        f"• Theta: `{escape_md(fmt_num(data['theta']))}`",
        f"• Vega: `{escape_md(fmt_num(data['vega']))}`",
        "",
        "*Value Breakdown*",
        f"• Intrinsic: {escape_md(fmt_money(intrinsic, 4))}",
        f"• Time Value: {escape_md(fmt_money(time_value, 4))}",
    ]
    return "\n".join(lines)


def format_iv_result(data: dict[str, Any]) -> str:
    option_type = str(data["option_type"]).title()
    style = str(data["style"]).title()
    days = int(round(float(data["time_to_expiry"]) * 365))

    lines = [
        "📉 *Implied Volatility Result*",
        "",
        f"{escape_md(style)} {escape_md(option_type)}",
        "",
        f"💰 *Spot:* {escape_md(fmt_money(float(data['spot'])))}",
        f"🎯 *Strike:* {escape_md(fmt_money(float(data['strike'])))}",
        f"💵 *Market Price:* {escape_md(fmt_money(float(data['market_price']), 4))}",
        f"📅 *Time to Expiry:* {escape_md(fmt_num(data['time_to_expiry']))} yrs \\({days} days\\)",
        "",
        f"*Implied Volatility:* `{escape_md(fmt_pct(float(data['implied_volatility'])))}`",
    ]
    return "\n".join(lines)


def parse_positive_float(text: str, field_name: str) -> float:
    cleaned = text.strip().replace(",", "").replace("$", "")
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ValueError(f"Please enter a valid number for {field_name}.") from exc
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    return value


def parse_volatility(text: str) -> float:
    cleaned = text.strip().replace("%", "")
    try:
        value = float(cleaned.replace(",", ""))
    except ValueError as exc:
        raise ValueError("Please enter volatility as a decimal (0.25) or percent (25%).") from exc
    if "%" in text or value > 1.5:
        value /= 100.0
    if value < 0:
        raise ValueError("Volatility cannot be negative.")
    return value


def parse_rate(text: str) -> float:
    cleaned = text.strip().replace("%", "")
    try:
        value = float(cleaned.replace(",", ""))
    except ValueError as exc:
        raise ValueError("Please enter the rate as a decimal (0.05) or percent (5%).") from exc
    if "%" in text or value > 1:
        value /= 100.0
    if value < -1 or value > 1:
        raise ValueError("Rate should be between -100% and 100%.")
    return value


def parse_days_or_date(text: str, valuation_date: date | None = None) -> date:
    text = text.strip()
    valuation_date = valuation_date or date.today()

    if text.isdigit():
        return add_days(valuation_date, int(text))

    try:
        expiry = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Enter expiry as days (e.g. 30) or a date (YYYY-MM-DD).") from exc

    if expiry < valuation_date:
        raise ValueError("Expiry must be today or later.")
    return expiry


def parse_option_type(text: str) -> str:
    normalized = text.strip().lower()
    if normalized in {"call", "c"}:
        return "call"
    if normalized in {"put", "p"}:
        return "put"
    raise ValueError("Option type must be `call` or `put`.")


def store_price_params(context, params: PriceParams) -> None:
    context.user_data["price_params"] = params


def load_price_params(context) -> PriceParams | None:
    params = context.user_data.get("price_params")
    return params.copy() if isinstance(params, PriceParams) else None


def store_iv_params(context, params: IVParams) -> None:
    context.user_data["iv_params"] = params


def load_iv_params(context) -> IVParams | None:
    params = context.user_data.get("iv_params")
    return params if isinstance(params, IVParams) else None
