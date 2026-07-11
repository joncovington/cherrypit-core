"""cherrypick.core.gex — standalone GEX (gamma exposure) engine.

A pure function over an option-chain snapshot (per-strike gamma + open interest + spot); no broker, no
streamer, no network — it consumes a snapshot the caller already fetched (same discipline as the paper
engine). Extracted from MEICAgent's `gex_math.py` (dollar-gamma / zero-gamma math, itself pulled out
after tt.py's and dashboard.py's hand-maintained copies drifted ~75x apart) plus tt.py's `_compute_gex`
orchestration, so every consumer — strike placement, stop tightening, the dashboard GEX panel, alerts —
draws from one implementation (plan Part 15).

Output fields match MEIC's `get_gex` exactly: net_gex, gex_positive, call_wall, put_wall, gamma_flip,
strikes_with_data, per_strike.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MULTIPLIER = 100


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def dollar_gamma(gamma: float, quantity: float, multiplier: float, spot: float) -> float:
    """Dollar-gamma-per-1%-move: gamma * quantity * contract size * spot^2 * 0.01. `quantity` is
    typically open interest (positioning); substitute traded volume for a flow reading."""
    return gamma * quantity * multiplier * spot * spot * 0.01


def interpolate_zero_gamma(strikes: list[dict]) -> float | None:
    """Interpolate the strike where CUMULATIVE net GEX crosses zero.

    `strikes` must be sorted ascending by "strike", each with a "net_gex" key. Scans the running
    cumulative sum (not adjacent strikes): an individual strike's net_gex can flip sign from local
    OI/volume noise without aggregate dealer exposure actually flipping.
    """
    cumulative = 0.0
    prev_cumulative = 0.0
    prev_strike = None
    for i, s in enumerate(strikes):
        prev_cumulative = cumulative
        cumulative += s["net_gex"]
        if i > 0 and ((prev_cumulative < 0 <= cumulative) or (prev_cumulative >= 0 > cumulative)):
            denom = cumulative - prev_cumulative
            t = (-prev_cumulative / denom) if denom != 0 else 0.5
            return round(prev_strike + t * (s["strike"] - prev_strike), 2)
        prev_strike = s["strike"]
    return None


def compute_gex(chain_entries: list[dict], greeks: dict, oi: dict, spot: float,
                multiplier: int = DEFAULT_MULTIPLIER) -> dict:
    """Compute a GEX profile from an option-chain snapshot.

    Args:
        chain_entries: dicts with "strike_price", "streamer_symbol", "option_type" (e.g. "...C..").
        greeks: {streamer_symbol: greeks} where greeks is a dict or object exposing `gamma`.
        oi: {streamer_symbol: open_interest}.
        spot: underlying price.

    Returns net_gex, gex_positive, call_wall, put_wall, gamma_flip, strikes_with_data, per_strike —
    or {"ok": False, "error": ...} when no strike has both greeks and non-zero OI.
    """
    per_strike: dict[float, dict] = {}

    for entry in chain_entries:
        strike = _num(entry.get("strike_price"))
        sym = entry.get("streamer_symbol")
        if sym is None or strike is None or strike <= 0:
            continue
        g = greeks.get(sym)
        open_interest = oi.get(sym)
        if g is None or open_interest is None or open_interest == 0:
            continue
        gamma = _num(g.get("gamma") if isinstance(g, dict) else getattr(g, "gamma", None))
        if gamma is None:
            continue
        gex_val = dollar_gamma(gamma, open_interest, multiplier, spot)
        opt_type = entry.get("option_type", "")
        is_call = "C" in opt_type.upper()
        if strike not in per_strike:
            per_strike[strike] = {"strike": strike, "call_gex": 0.0, "put_gex": 0.0}
        if is_call:
            per_strike[strike]["call_gex"] += gex_val
        else:
            per_strike[strike]["put_gex"] += gex_val

    if not per_strike:
        return {"ok": False, "error": "insufficient GEX data — OI not yet cached (streamer must run first)"}

    strikes_sorted = sorted(per_strike.values(), key=lambda x: x["strike"])
    for s in strikes_sorted:
        s["net_gex"] = s["call_gex"] - s["put_gex"]

    net_gex = sum(s["net_gex"] for s in strikes_sorted)
    call_wall = max(strikes_sorted, key=lambda x: x["call_gex"])["strike"]
    put_wall = max(strikes_sorted, key=lambda x: x["put_gex"])["strike"]
    gamma_flip = interpolate_zero_gamma(strikes_sorted)

    return {
        "ok": True,
        "net_gex": round(net_gex, 2),
        "gex_positive": net_gex > 0,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "gamma_flip": gamma_flip,
        "strikes_with_data": len(per_strike),
        "per_strike": [
            {
                "strike": s["strike"],
                "call_gex": round(s["call_gex"], 2),
                "put_gex": round(s["put_gex"], 2),
                "net_gex": round(s["net_gex"], 2),
            }
            for s in strikes_sorted
        ],
    }
