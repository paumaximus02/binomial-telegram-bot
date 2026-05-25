"""Preset definitions and strike/expiry resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from utils import ApiError, PriceParams, add_days, fetch_spot


class StrikeMode(str, Enum):
    ATM = "atm"
    DEEP_ITM_CALL = "deep_itm_call"
    OTM_CALL = "otm_call"


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    symbol: str
    days: int
    option_type: str
    strike_mode: StrikeMode
    vol: float = 0.30
    style: str = "american"


PRESETS: tuple[Preset, ...] = (
    Preset(
        id="amzn_call_30",
        label="AMZN ATM Call 30 days",
        symbol="AMZN",
        days=30,
        option_type="call",
        strike_mode=StrikeMode.ATM,
    ),
    Preset(
        id="amzn_put_30",
        label="AMZN ATM Put 30 days",
        symbol="AMZN",
        days=30,
        option_type="put",
        strike_mode=StrikeMode.ATM,
    ),
    Preset(
        id="tsla_itm_call",
        label="TSLA Deep ITM Call",
        symbol="TSLA",
        days=30,
        option_type="call",
        strike_mode=StrikeMode.DEEP_ITM_CALL,
        vol=0.45,
    ),
    Preset(
        id="aapl_otm_call_45",
        label="AAPL OTM Call 45 days",
        symbol="AAPL",
        days=45,
        option_type="call",
        strike_mode=StrikeMode.OTM_CALL,
        vol=0.25,
    ),
)

PRESET_BY_ID = {preset.id: preset for preset in PRESETS}


def get_preset(preset_id: str) -> Preset | None:
    return PRESET_BY_ID.get(preset_id)


def resolve_strike(spot: float, strike_mode: StrikeMode) -> float:
    if strike_mode == StrikeMode.ATM:
        return round(spot, 2)
    if strike_mode == StrikeMode.DEEP_ITM_CALL:
        return round(spot * 0.75, 2)
    if strike_mode == StrikeMode.OTM_CALL:
        return round(spot * 1.10, 2)
    return round(spot, 2)


async def build_preset_params(preset: Preset, valuation_date: date | None = None) -> PriceParams:
    valuation_date = valuation_date or date.today()
    expiry = add_days(valuation_date, preset.days)

    try:
        quote = await fetch_spot(preset.symbol)
        spot = float(quote["price"])
        symbol = str(quote["symbol"])
    except ApiError as exc:
        raise ApiError(
            f"Could not fetch live spot for {preset.symbol}: {exc}",
            exc.status_code,
        ) from exc

    strike = resolve_strike(spot, preset.strike_mode)
    return PriceParams(
        symbol=symbol,
        spot=spot,
        strike=strike,
        vol=preset.vol,
        expiry=expiry,
        valuation_date=valuation_date,
        option_type=preset.option_type,
        style=preset.style,
        preset_label=preset.label,
    )
