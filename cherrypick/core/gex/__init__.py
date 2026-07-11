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


def compute_gex_profile(chain_entries: list[dict], greeks: dict, oi: dict, volume: dict,
                        spot: float, default_multiplier: int = DEFAULT_MULTIPLIER,
                        strike_scale: float = 1.0) -> dict:
    """Rich per-strike GEX profile with BOTH an OI ("positioning") and a volume ("flow") series.

    This is the "gexbot-lite" aggregation — the pure math behind the dashboard GEX panel, extracted
    from MEIC's dashboard `_build_gex_data` so the umbrella's GEX module and MEIC's own dashboard draw
    from one implementation (the drift the GEX math was extracted to prevent). Pure: the caller supplies
    an already-fetched snapshot (chain + greeks + OI + volume + spot); no DB, no streamer, no network.

    Unlike `compute_gex` (OI only, the trading-loop contract — left untouched), this returns the full
    per-strike detail SpotGamma/MenthorQ-style views need. Puts are stored **negated** so a chart draws
    calls up / puts down and the walls fall out as max(call_gex) / min(put_gex).

    Args:
        chain_entries: dicts with "strike_price", "streamer_symbol", "option_type" ("C"/"P"),
            optional "shares_per_contract" (per-option multiplier; defaults to `default_multiplier`).
        greeks: {streamer_symbol: {"gamma": float, "iv": float}} — `iv` already normalised to percent.
        oi:     {streamer_symbol: open_interest}.
        volume: {streamer_symbol: traded_volume}.
        spot:   underlying price in the CHAIN's own domain (the price used in the dollar-gamma math).
        strike_scale: multiply displayed strikes by this to map a scaled underlying (e.g. XSP→SPX ×10)
            back into the requested symbol's price domain; the GEX math itself uses the unscaled `spot`.

    Returns {"ok": True, "series": [...], "totals": {...}} or {"ok": False, "error": ...} when no strike
    carries data. `totals` = total_call_gex, total_put_gex, net_gex, gex_positive, max_gex_strike,
    zero_gamma, call_wall, put_wall.
    """
    strikes: dict[float, dict] = {}
    for entry in chain_entries:
        strike = _num(entry.get("strike_price"))
        if strike is None:
            continue
        otype = (entry.get("option_type") or "").upper()
        sym = entry.get("streamer_symbol") or ""
        mult = _num(entry.get("shares_per_contract")) or float(default_multiplier)

        oi_val = int(oi.get(sym) or 0)
        vol_val = int(volume.get(sym) or 0)
        g = greeks.get(sym) or {}
        gamma = _num(g.get("gamma") if isinstance(g, dict) else getattr(g, "gamma", None)) or 0.0
        iv = _num(g.get("iv") if isinstance(g, dict) else getattr(g, "iv", None)) or 0.0

        gex = dollar_gamma(gamma, oi_val, mult, spot)
        gex_vol = dollar_gamma(gamma, vol_val, mult, spot)
        if "P" in otype:
            gex = -gex
            gex_vol = -gex_vol

        d = strikes.setdefault(strike, {
            "call_gamma": 0.0, "call_iv": 0.0, "call_oi": 0, "call_vol": 0, "call_gex": 0.0, "call_gex_vol": 0.0,
            "put_gamma": 0.0, "put_iv": 0.0, "put_oi": 0, "put_vol": 0, "put_gex": 0.0, "put_gex_vol": 0.0,
        })
        if "C" in otype:
            d["call_gamma"], d["call_iv"], d["call_oi"], d["call_vol"] = gamma, round(iv, 2), oi_val, vol_val
            d["call_gex"], d["call_gex_vol"] = gex, gex_vol
        elif "P" in otype:
            d["put_gamma"], d["put_iv"], d["put_oi"], d["put_vol"] = gamma, round(iv, 2), oi_val, vol_val
            d["put_gex"], d["put_gex_vol"] = gex, gex_vol

    if not strikes:
        return {"ok": False, "error": "insufficient GEX data — OI/volume not yet cached (streamer must run first)"}

    series = []
    for strike in sorted(strikes):
        d = strikes[strike]
        net = d["call_gex"] + d["put_gex"]
        net_vol = d["call_gex_vol"] + d["put_gex_vol"]
        series.append({
            "strike": round(strike * strike_scale, 2),
            "call_iv": d["call_iv"], "put_iv": d["put_iv"],
            "call_oi": d["call_oi"], "put_oi": d["put_oi"],
            "call_vol": d["call_vol"], "put_vol": d["put_vol"],
            "total_vol": d["call_vol"] + d["put_vol"],
            "call_gamma": d["call_gamma"], "put_gamma": d["put_gamma"],
            "call_gex": round(d["call_gex"]),
            "put_gex": round(d["put_gex"]),   # negative value
            "net_gex": round(net),
            "abs_gex": round(abs(net)),
            "call_gex_vol": round(d["call_gex_vol"]),
            "put_gex_vol": round(d["put_gex_vol"]),
            "net_gex_vol": round(net_vol),
        })

    total_call_gex = sum(s["call_gex"] for s in series if s["call_gex"] > 0)
    total_put_gex = abs(sum(s["put_gex"] for s in series if s["put_gex"] < 0))
    net_gex_total = sum(s["net_gex"] for s in series)
    max_gex_s = max(series, key=lambda s: s["abs_gex"], default=None)
    zero_gamma = interpolate_zero_gamma(series)
    # Walls: series stores put_gex negative, so the put wall is the most-negative entry, not the largest.
    call_wall_s = max(series, key=lambda s: s["call_gex"], default=None)
    put_wall_s = min(series, key=lambda s: s["put_gex"], default=None)

    return {
        "ok": True,
        "series": series,
        "totals": {
            "total_call_gex": round(total_call_gex),
            "total_put_gex": round(total_put_gex),
            "net_gex": round(net_gex_total),
            "gex_positive": net_gex_total > 0,
            "max_gex_strike": max_gex_s["strike"] if max_gex_s else None,
            "zero_gamma": zero_gamma,
            "call_wall": call_wall_s["strike"] if call_wall_s else None,
            "put_wall": put_wall_s["strike"] if put_wall_s else None,
        },
    }
