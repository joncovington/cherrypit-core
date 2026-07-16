"""cherrypick.core.home — the one resolver for the per-user cherrypick home.

Every package in the suite (orchestrator, meic, earnings, gex) writes its runtime data and logs under
a single per-user directory, ``~/.cherrypick`` — ``C:\\Users\\<user>\\.cherrypick`` on Windows,
``/home/<user>/.cherrypick`` on Linux/macOS. Before this module each package rolled its own path logic;
they drifted (gex defaulted into its own checkout; ``CHERRYPICK_HOME`` moved logs but not data). This is
the single source of truth so they can't drift again.

Layout under the home::

    ~/.cherrypick/
      config.json          suite config (orchestrator)
      dashboard.html       generated status page
      state/               orchestrator runtime state
      modules/             installed module checkouts
      data/<pkg>/          per-package runtime data (trade DBs, stream caches, ...)
      logs/<pkg>/          per-package logs

**Override precedence** (widest to narrowest):

* ``CHERRYPICK_HOME`` — the *master* override. Relocates the whole tree (config, state, data, logs,
  modules) in one move, uniformly across every package. This is the knob to point the suite at a
  non-default location (a test sandbox, a different volume).
* a *per-scope* env var — the finest override, naming one concrete directory directly (e.g.
  ``MEIC_DATA_DIR`` → the meic data dir, ``EARNINGS_LOGS_DIR`` → the earnings logs dir,
  ``CHERRYPICK_MODULES_HOME`` → the modules dir). When set it wins over the ``CHERRYPICK_HOME``-derived
  default for that one scope only. Used by tests to point at a tmp path and as a machine escape hatch.

``~`` and ``$VARS`` are expanded in every env value, so an override can itself be written portably.

These are **pure** functions — they compute paths and never create directories or touch the filesystem,
so importing and calling them has no side effects (mirrors ``report``/``dashboard`` staying file-only on
the reliability path). Call :func:`ensure` at the point you actually write.
"""

from __future__ import annotations

import os
from pathlib import Path


def _expand(value: str) -> Path:
    """Expand ``~`` and ``$VARS`` in an env-supplied path so overrides can be written portably."""
    return Path(os.path.expandvars(os.path.expanduser(value)))


def _env(name: str | None) -> Path | None:
    if not name:
        return None
    raw = os.environ.get(name)
    return _expand(raw) if raw else None


def home() -> Path:
    """The per-user cherrypick home. ``$CHERRYPICK_HOME`` if set (the master override), else
    ``~/.cherrypick``. Portable across OSes via :func:`pathlib.Path.home`."""
    override = _env("CHERRYPICK_HOME")
    return override if override else Path.home() / ".cherrypick"


def data_dir(package: str | None = None, *, env: str | None = None) -> Path:
    """Runtime-data home. ``$<env>`` if that per-scope var is set, else ``home()/data[/package]``.

    ``package`` scopes the directory to one module (``data/meic``); omit it for the shared ``data`` root.
    ``env`` names the narrow override that points straight at this directory (e.g. ``MEIC_DATA_DIR``)."""
    override = _env(env)
    if override:
        return override
    base = home() / "data"
    return base / package if package else base


def logs_dir(package: str | None = None, *, env: str | None = None) -> Path:
    """Logs home. ``$<env>`` if that per-scope var is set, else ``home()/logs[/package]``.

    ``env`` names the narrow override pointing straight at this directory (e.g. ``EARNINGS_LOGS_DIR``)."""
    override = _env(env)
    if override:
        return override
    base = home() / "logs"
    return base / package if package else base


def state_dir() -> Path:
    """Orchestrator runtime state (``home()/state``)."""
    return home() / "state"


def modules_dir(*, env: str | None = "CHERRYPICK_MODULES_HOME") -> Path:
    """Installed module checkouts (``home()/modules``), or ``$CHERRYPICK_MODULES_HOME`` if set."""
    override = _env(env)
    return override if override else home() / "modules"


def config_dir() -> Path:
    """Directory holding the per-module configs (``home()/config``)."""
    return home() / "config"


def config_path(package: str | None = None) -> Path:
    """The orchestrator suite config (``home()/config.json``) when *package* is ``None``; otherwise a
    module's config at ``home()/config/<package>.json`` (e.g. ``config/meic.json``)."""
    if package is None:
        return home() / "config.json"
    return config_dir() / f"{package}.json"


def dashboard_path() -> Path:
    """The generated status page (``home()/dashboard.html``)."""
    return home() / "dashboard.html"


def ensure(path: Path) -> Path:
    """Create ``path`` (as a directory) if missing and return it — the one side-effecting helper, called
    where a caller is about to write. ``exist_ok`` so it is idempotent and safe under concurrency."""
    path.mkdir(parents=True, exist_ok=True)
    return path
