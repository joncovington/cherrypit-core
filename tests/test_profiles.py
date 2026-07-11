"""Tests for cherrypit.profiles — dual-source registry + the generalized merge engine."""

import json

import pytest

from cherrypit import profiles


# --------------------------------------------------------------------------- load_profiles
def test_load_profiles_inline():
    cfg = {"profiles": {"a": {"x": 1}, "b": {"x": 2}}, "other": 9}
    assert profiles.load_profiles(cfg) == {"a": {"x": 1}, "b": {"x": 2}}


def test_load_profiles_inline_missing_returns_empty():
    assert profiles.load_profiles({}) == {}
    assert profiles.load_profiles(None) == {}


def test_load_profiles_external_file(tmp_path):
    f = tmp_path / "config.risk.json"
    f.write_text(json.dumps({"active_profile": "a", "profiles": {"a": {"x": 1}}}))
    assert profiles.load_profiles(external_path=f) == {"a": {"x": 1}}


# --------------------------------------------------------------------------- select_profile
def test_select_profile_returns_copy():
    profs = {"a": {"x": 1}}
    got = profiles.select_profile(profs, "a")
    assert got == {"x": 1}
    got["x"] = 99
    assert profs["a"]["x"] == 1  # returned a copy, source unmutated


def test_select_profile_unknown_raises():
    with pytest.raises(ValueError, match="unknown profile 'z'"):
        profiles.select_profile({"a": {}}, "z")


# --------------------------------------------------------------------------- attribution_tag
def test_attribution_tag_returns_name_when_set():
    assert profiles.attribution_tag("aggressive") == "aggressive"


def test_attribution_tag_none_uses_default_sentinel():
    assert profiles.attribution_tag(None) == "unassigned"
    assert profiles.attribution_tag(None) == profiles.UNTAGGED


def test_attribution_tag_empty_and_whitespace_treated_as_untagged():
    assert profiles.attribution_tag("") == "unassigned"
    assert profiles.attribution_tag("   ") == "unassigned"


def test_attribution_tag_strips_surrounding_whitespace():
    assert profiles.attribution_tag("  balanced ") == "balanced"


def test_attribution_tag_custom_untagged_sentinel():
    # EarningsAgent's schema stores a non-null "default" sentinel, not NULL.
    assert profiles.attribution_tag(None, untagged="default") == "default"
    assert profiles.attribution_tag("balanced", untagged="default") == "balanced"


# --------------------------------------------------------------------------- compare_profiles
def _count(group):
    return {"n": len(group), "pnl": sum(r["pnl"] for r in group)}


def test_compare_profiles_groups_by_tag_and_summarizes():
    rows = [
        {"risk_profile": "aggressive", "pnl": 10},
        {"risk_profile": "conservative", "pnl": -5},
        {"risk_profile": "aggressive", "pnl": 3},
    ]
    table = profiles.compare_profiles(rows, tag_key="risk_profile", summarize=_count)
    assert table == {
        "aggressive": {"n": 2, "pnl": 13},
        "conservative": {"n": 1, "pnl": -5},
    }


def test_compare_profiles_summarize_called_once_per_group():
    calls = []
    def summarize(group):
        calls.append(len(group))
        return len(group)
    rows = [{"p": "a"}, {"p": "a"}, {"p": "b"}]
    profiles.compare_profiles(rows, tag_key="p", summarize=summarize)
    assert sorted(calls) == [1, 2]      # one call per group, never on the whole set


def test_compare_profiles_untagged_rows_group_under_sentinel():
    rows = [{"risk_profile": None, "pnl": 1}, {"risk_profile": "aggressive", "pnl": 2}]
    table = profiles.compare_profiles(rows, tag_key="risk_profile", summarize=_count)
    assert table["unassigned"] == {"n": 1, "pnl": 1}
    assert table["aggressive"] == {"n": 1, "pnl": 2}


def test_compare_profiles_custom_untagged_sentinel():
    rows = [{"profile": None, "pnl": 1}]
    table = profiles.compare_profiles(rows, tag_key="profile", summarize=_count, untagged="default")
    assert set(table) == {"default"}


def test_compare_profiles_preserves_first_seen_order():
    rows = [{"p": "z"}, {"p": "a"}, {"p": "z"}]
    table = profiles.compare_profiles(rows, tag_key="p", summarize=len)
    assert list(table) == ["z", "a"]    # first-seen order, not sorted


def test_compare_profiles_empty_rows():
    assert profiles.compare_profiles([], tag_key="risk_profile", summarize=_count) == {}


# --------------------------------------------------------------------------- recommend_promotion
_LADDER = ["conservative", "moderate", "aggressive", "very-aggressive"]
_GOOD = {"sample": 40, "win_rate": 0.65, "days": 20}


def test_recommend_promotion_eligible_when_all_thresholds_met():
    v = profiles.recommend_promotion(_GOOD, "conservative", _LADDER)
    assert v["eligible"] is True
    assert v["next"] == "moderate"
    assert v["recommendation"] == "graduate:moderate"
    assert all(c["pass"] for c in v["checks"].values())


def test_recommend_promotion_holds_when_win_rate_below_threshold():
    v = profiles.recommend_promotion({"sample": 40, "win_rate": 0.55, "days": 20},
                                     "conservative", _LADDER)
    assert v["eligible"] is False
    assert v["recommendation"] == "hold"
    assert v["checks"]["win_rate"]["pass"] is False
    assert "win_rate" in v["reason"]


def test_recommend_promotion_holds_when_sample_or_days_short():
    v = profiles.recommend_promotion({"sample": 5, "win_rate": 0.9, "days": 3},
                                     "conservative", _LADDER)
    assert v["eligible"] is False
    assert v["checks"]["sample"]["pass"] is False
    assert v["checks"]["days"]["pass"] is False


def test_recommend_promotion_none_win_rate_fails_closed():
    # too few trades for a win rate -> None must not count as passing
    v = profiles.recommend_promotion({"sample": 40, "win_rate": None, "days": 20},
                                     "conservative", _LADDER)
    assert v["checks"]["win_rate"]["pass"] is False
    assert v["eligible"] is False


def test_recommend_promotion_top_rung_holds():
    v = profiles.recommend_promotion(_GOOD, "very-aggressive", _LADDER)
    assert v["next"] is None
    assert v["eligible"] is False
    assert v["recommendation"] == "hold"
    assert "top of the ladder" in v["reason"]


def test_recommend_promotion_deliberate_only_next_never_auto_recommended():
    # aggressive fully qualifies, but very-aggressive is opt-in-only -> hold, not graduate
    v = profiles.recommend_promotion(_GOOD, "aggressive", _LADDER,
                                     deliberate_only=("very-aggressive",))
    assert v["next"] == "very-aggressive"
    assert v["eligible"] is False
    assert v["recommendation"] == "hold"
    assert "deliberate" in v["reason"]


def test_recommend_promotion_rule_override():
    v = profiles.recommend_promotion({"sample": 40, "win_rate": 0.65, "days": 20},
                                     "conservative", _LADDER, rule={"min_win_rate": 0.70})
    assert v["checks"]["win_rate"]["threshold"] == 0.70
    assert v["checks"]["win_rate"]["pass"] is False


def test_recommend_promotion_unknown_current_raises():
    with pytest.raises(ValueError, match="not in ladder"):
        profiles.recommend_promotion(_GOOD, "ghost", _LADDER)


# --------------------------------------------------------------------------- merge_profile (flat / MEIC)
def test_merge_profile_flat_override_skips_underscore_and_leaves_base():
    base = {"min_iv_rank": 0.3, "max_ics": 4, "force_close_time": "15:45"}
    profile = {"min_iv_rank": 0.2, "max_ics": 6, "_note": "comment"}
    merged = profiles.merge_profile(base, profile)
    assert merged == {"min_iv_rank": 0.2, "max_ics": 6, "force_close_time": "15:45"}
    assert "_note" not in merged            # underscore comment skipped
    assert base["min_iv_rank"] == 0.3       # base not mutated


# --------------------------------------------------------------------------- merge_profile (Earnings shape)
def test_merge_profile_reserved_keys_skipped():
    base = {"risk_pct_multiplier": 1.0, "tier_floor": "Tier 2"}
    profile = {"description": "balanced book", "risk_pct_multiplier": 0.6, "tier_floor": "Tier 1"}
    merged = profiles.merge_profile(base, profile, reserved_keys=("description",))
    assert merged == {"risk_pct_multiplier": 0.6, "tier_floor": "Tier 1"}
    assert "description" not in merged


def test_merge_profile_nested_namespace_deep_merges_and_skips_unknown():
    base = {
        "risk_pct_multiplier": 1.0,
        "strategies": {"iron_fly": {"a": 1, "b": 2}, "double_calendar": {"a": 1}},
    }
    profile = {
        "risk_pct_multiplier": 0.6,
        "strategy_overrides": {
            "iron_fly": {"b": 99, "c": 3},        # merges over existing strategy
            "ghost_strategy": {"z": 1},           # not in base -> skipped
        },
    }
    merged = profiles.merge_profile(
        base, profile,
        reserved_keys=("description",),
        nested_namespaces={"strategy_overrides": "strategies"},
    )
    assert merged["risk_pct_multiplier"] == 0.6
    assert merged["strategies"]["iron_fly"] == {"a": 1, "b": 99, "c": 3}
    assert merged["strategies"]["double_calendar"] == {"a": 1}   # untouched
    assert "ghost_strategy" not in merged["strategies"]
    # base untouched
    assert base["strategies"]["iron_fly"] == {"a": 1, "b": 2}
    assert base["risk_pct_multiplier"] == 1.0


def test_merge_profile_namespace_key_not_treated_as_top_level_override():
    base = {"strategies": {"x": {"a": 1}}}
    profile = {"strategy_overrides": {"x": {"a": 2}}}
    merged = profiles.merge_profile(
        base, profile, nested_namespaces={"strategy_overrides": "strategies"})
    assert "strategy_overrides" not in merged      # consumed as a namespace, not copied
    assert merged["strategies"]["x"] == {"a": 2}


# --------------------------------------------------------------------------- merge_profile (validation)
def test_merge_profile_validate_rejects_unknown_key():
    base = {"known": 1}
    with pytest.raises(KeyError, match="typo_key"):
        profiles.merge_profile(base, {"typo_key": 5}, validate=True)


def test_merge_profile_validate_exempts_reserved_underscore_and_namespaces():
    base = {"known": 1, "strategies": {"x": {"a": 1}}}
    profile = {
        "known": 2, "_note": "c", "description": "d",
        "strategy_overrides": {"x": {"a": 9}},
    }
    merged = profiles.merge_profile(
        base, profile, reserved_keys=("description",),
        nested_namespaces={"strategy_overrides": "strategies"}, validate=True)
    assert merged["known"] == 2                     # validated + applied
    assert merged["strategies"]["x"] == {"a": 9}    # namespace merged, not validated as top-level
