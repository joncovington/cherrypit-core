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
