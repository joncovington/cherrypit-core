"""cherrypit.profiles — named risk-profile registry + merge engine (Phase A).

Consolidates the profiling *mechanism* both suite modules share; profile *definitions* stay
per-module. The two override models unify here because EarningsAgent's merge is a strict superset of
MEICAgent's flat overlay (see plan Part 10): a top-level partial override, plus optional per-namespace
deep-merges (Earnings' `strategy_overrides`). Pure dict/JSON operations, no I/O beyond an optional
external profiles file.

Phase B adds the *attribution contract* (`attribution_tag`): every trade row carries a tag naming the
named risk profile (or parallel-shadow paper book) that opened it, and reporting groups P&L by that
tag. Phase C adds the calibration harness's *comparison engine* (`compare_profiles`): group tagged
trade rows by their attribution tag and apply a module-injected summary per group — the metric math
stays per-module (it is domain-divergent) while the grouping orchestration is shared. The promotion
advisor is a later phase.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

# Canonical sentinel for a trade row that carries no named profile (a live trade, or a
# pre-attribution row) when it surfaces in a profile-grouped rollup. See `attribution_tag`.
UNTAGGED = "unassigned"


def load_profiles(config: Mapping | None = None, *, external_path: Any = None) -> dict:
    """Return the `{name: profile_def}` registry from either an inline config or an external JSON file.

    Dual-source so neither module migrates its layout: MEICAgent keeps profiles in a separate
    `config.risk.json` (pass `external_path`); EarningsAgent keeps them inline under
    `config["profiles"]` (pass `config`). The external file's top-level `"profiles"` key is used.
    """
    if external_path is not None:
        with open(external_path) as f:
            data = json.load(f)
        return dict(data.get("profiles", {}))
    return dict((config or {}).get("profiles", {}))


def select_profile(profiles: Mapping, name: str) -> dict:
    """Return the named profile's override dict, or raise ValueError listing the known names."""
    if name not in profiles:
        raise ValueError(f"unknown profile '{name}' -- known profiles: {sorted(profiles)}")
    return dict(profiles[name])


def attribution_tag(value: Any, *, untagged: str = UNTAGGED) -> str:
    """Normalize a stored profile tag into a stable attribution group key.

    The attribution contract: every trade row carries a profile tag naming which named risk
    profile (or parallel-shadow paper book) opened it, and reporting groups P&L by that tag.
    This normalizes a *read* value to a group key — the profile name if one was set, else
    `untagged` for rows with no named profile. `None`, empty, and whitespace-only values are
    all treated as untagged.

    Column name and the untagged sentinel stay per module (both are baked into committed
    schemas, so this is a value convention, not a column rename): MEICAgent's
    `ic_trades.risk_profile` is nullable and uses the default `"unassigned"`; EarningsAgent's
    `trades.profile` is `NOT NULL DEFAULT 'default'`, so it passes `untagged="default"`.
    """
    if value is None:
        return untagged
    text = str(value).strip()
    return text or untagged


def compare_profiles(rows, *, tag_key: str, summarize, untagged: str = UNTAGGED) -> dict:
    """Group profile-tagged trade rows by their attribution tag and summarize each group.

    The calibration harness's comparison engine (plan Part 10 Phase C). It consolidates the
    *orchestration* both modules hand-roll — MEICAgent's `cmd_get_range_summary` groups
    `ic_trades` by `risk_profile` then calls `_range_stats_for_rows` per group; EarningsAgent's
    `cmd_get_pnl_summary` groups `trades` by `profile` then aggregates per group — while leaving
    the metric math injected via `summarize`, because it is deliberately domain-divergent (MEIC
    annualizes Sharpe on a daily return series; Earnings does not, on discrete event trades).

    - `rows`: iterable of mappings (a module's trade rows; sqlite3.Row or dict). Each must carry
      `tag_key`. The caller filters (e.g. to closed trades) before passing them in.
    - `tag_key`: column naming the profile tag — `"risk_profile"` (MEIC) or `"profile"` (Earnings).
    - `summarize`: `callable(list_of_rows_for_one_profile) -> value` (any JSON-able summary); the
      module's own metric bundle. Called once per profile group, never on the whole set.
    - `untagged`: sentinel for rows with no profile tag, applied via `attribution_tag` (MEIC uses
      the `"unassigned"` default; Earnings passes `"default"` to match its non-null column).

    Returns `{profile_tag: summarize(group)}`, groups in first-seen row order (deterministic and
    behaviour-preserving for the callers being consolidated). Empty `rows` -> `{}`.
    """
    groups: dict[str, list] = {}
    for r in rows:
        tag = attribution_tag(r[tag_key], untagged=untagged)
        groups.setdefault(tag, []).append(r)
    return {tag: summarize(group) for tag, group in groups.items()}


def merge_profile(base: Mapping, profile_def: Mapping, *, reserved_keys: tuple = (),
                  nested_namespaces: Mapping | None = None, validate: bool = False) -> dict:
    """Merge a profile's overrides onto `base`, returning a NEW config (base is not mutated).

    - Top-level keys in `profile_def` partially override `base`, EXCEPT: keys starting with `_`
      (comments) and keys in `reserved_keys` (e.g. `"description"`) are skipped, and keys named in
      `nested_namespaces` are handled as deep-merges (below) rather than top-level overrides.
    - `nested_namespaces`: `{profile_key: base_key}`. For each, `profile_def[profile_key]` is a
      `{entry_name: overrides}` map; each `overrides` dict is shallow-merged onto
      `base[base_key][entry_name]` (entries absent from base are skipped). This is EarningsAgent's
      `strategy_overrides -> strategies` merge; MEICAgent passes none (flat overlay).
    - `validate=True` raises KeyError for any top-level override key not already present in `base`
      (fail-closed typo guard); `_`/reserved/namespace keys are exempt.

    Generalizes MEICAgent's `_merged_params` (no namespaces) and the profile step of EarningsAgent's
    `_load_config` (with `strategy_overrides`).
    """
    nested_namespaces = dict(nested_namespaces or {})
    reserved = set(reserved_keys) | set(nested_namespaces)
    result = dict(base)

    for key, value in profile_def.items():
        if key.startswith("_") or key in reserved:
            continue
        if validate and key not in base:
            raise KeyError(f"profile key '{key}' not in base config (fail-closed validation)")
        result[key] = value

    for profile_key, base_key in nested_namespaces.items():
        overrides_map = profile_def.get(profile_key) or {}
        if not overrides_map:
            continue
        target = dict(result.get(base_key) or {})  # copy so `base` is not mutated
        for entry_name, overrides in overrides_map.items():
            if entry_name in target:
                target[entry_name] = {**target[entry_name], **overrides}
        result[base_key] = target

    return result
