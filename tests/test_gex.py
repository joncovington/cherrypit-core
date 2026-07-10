"""Tests for cherrypit.gex.

Ports MEICAgent's gex_math golden-master cases (dollar_gamma / interpolate_zero_gamma) and adds a
full compute_gex profile check with hand-computed values.
"""

from cherrypit import gex


# --- dollar_gamma / interpolate_zero_gamma (ported golden master) -------------------------------
def test_dollar_gamma_formula():
    assert gex.dollar_gamma(gamma=0.05, quantity=1000, multiplier=100, spot=600.0) == \
        0.05 * 1000 * 100 * 600.0 * 600.0 * 0.01


def test_dollar_gamma_zero_when_spot_zero():
    assert gex.dollar_gamma(0.05, 1000, 100, 0.0) == 0.0


def test_interpolate_zero_gamma_crosses_between_two_strikes():
    strikes = [{"strike": 595, "net_gex": -100}, {"strike": 600, "net_gex": 300},
               {"strike": 605, "net_gex": -50}]
    result = gex.interpolate_zero_gamma(strikes)
    assert result is not None and 595 < result < 600


def test_interpolate_zero_gamma_none_when_no_crossing():
    assert gex.interpolate_zero_gamma([{"strike": 595, "net_gex": 100}, {"strike": 600, "net_gex": 50}]) is None


def test_interpolate_zero_gamma_uses_cumulative_not_adjacent():
    strikes = [{"strike": 595, "net_gex": 100}, {"strike": 600, "net_gex": -10},
               {"strike": 605, "net_gex": 5}]  # adjacent flip, cumulative stays positive
    assert gex.interpolate_zero_gamma(strikes) is None


def test_interpolate_zero_gamma_single_strike_no_crossing():
    assert gex.interpolate_zero_gamma([{"strike": 600, "net_gex": 100}]) is None


# --- compute_gex full profile -------------------------------------------------------------------
CHAIN = [
    {"strike_price": 600, "streamer_symbol": "C600", "option_type": ".SPXWc"},
    {"strike_price": 600, "streamer_symbol": "P600", "option_type": ".SPXWp"},
    {"strike_price": 610, "streamer_symbol": "C610", "option_type": ".SPXWc"},
]
GREEKS = {"C600": {"gamma": 0.01}, "P600": {"gamma": 0.01}, "C610": {"gamma": 0.05}}
OI = {"C600": 100, "P600": 300, "C610": 50}
# dollar_gamma(g, oi, 100, 600) = g*oi*360000
# C600 call=360000 ; P600 put=1_080_000 ; C610 call=900000


def test_compute_gex_full_profile():
    out = gex.compute_gex(CHAIN, GREEKS, OI, spot=600.0)
    assert out["ok"] is True
    assert out["net_gex"] == 180000.0          # (360000-1080000) + 900000
    assert out["gex_positive"] is True
    assert out["call_wall"] == 610             # 610 has the largest call_gex (900000)
    assert out["put_wall"] == 600
    assert out["gamma_flip"] == 608.0          # cumulative -720000 -> +180000 crosses at 600 + 0.8*10
    assert out["strikes_with_data"] == 2
    assert out["per_strike"] == [
        {"strike": 600, "call_gex": 360000.0, "put_gex": 1080000.0, "net_gex": -720000.0},
        {"strike": 610, "call_gex": 900000.0, "put_gex": 0.0, "net_gex": 900000.0},
    ]


def test_compute_gex_accepts_greeks_objects_not_just_dicts():
    class G:
        def __init__(self, gamma):
            self.gamma = gamma
    out = gex.compute_gex(CHAIN, {"C600": G(0.01), "P600": G(0.01), "C610": G(0.05)}, OI, spot=600.0)
    assert out["ok"] is True and out["net_gex"] == 180000.0


def test_compute_gex_skips_zero_oi_and_missing_greeks():
    greeks = {"C600": {"gamma": 0.01}}  # only one symbol has greeks
    oi = {"C600": 0, "P600": 300}       # C600 has zero OI -> skipped; P600 has no greeks -> skipped
    out = gex.compute_gex(CHAIN, greeks, oi, spot=600.0)
    assert out["ok"] is False and "insufficient" in out["error"]


def test_compute_gex_empty_returns_not_ok():
    assert gex.compute_gex([], {}, {}, spot=600.0)["ok"] is False
