# cherrypick-core

Shared core library for the **Cherrypick** trading suite. Consumed by each module (MEICAgent,
EarningsAgent, and future modules) — and by `tastytrade-mcp` — so the auth, market-data, fee, risk,
calendar, GEX, paper, and reporting concerns are implemented **once**.

> Distribution name is `cherrypick-core`; it imports as the **`cherrypick.core`** namespace package
> (part of the suite-wide `cherrypick.*` namespace — see the packaging-naming decision). Earlier drafts
> called this `cherrypit`/`cherrypit-core`; that import root is retired.

## Design invariants (inherited from the suite plan)
1. **A library, never a service, on any loop decision path.** Loop-path code calls `cherrypick.core` in
   process; it must never add a network/MCP failure mode. (This is why `auth.session` builds the broker
   session lazily and lets callers inject the factory.)
2. **The core imports nothing from a consumer's `src/`.** Everything a consumer supplies (service name,
   thread-local flag, session factory, snapshots) is **injected or parameterized**, never reached back
   into. This will be enforced by an `import-linter` contract.
3. **Portable + secret-safe.** No hardcoded machine paths; credentials live in the OS keyring only.

## Layout (native namespace: `cherrypick/` has no `__init__.py`; the package is `cherrypick/core/`)
```
cherrypick/core/
  auth/        credentials (CredentialStore: param service + legacy fallback)
               session     (SessionManager: param thread_local, injected session factory)
  broker/      account primitives (resolve/list/count, session injected) + option-chain
               strike helpers + order build/submit (build_order, place_order). Submission is
               fail-safe: a live order is placed only on a clean dry-run preflight.
  calendar/    trading-day / expiration calendar
  dxfeed/      on-demand DXLink collectors (quotes/greeks/last/open-interest/volume; session injected)
  fees/        commission + fee/slippage cost model (ic_open_fee / ic_close_fee / ic_expire_fee)
  gex/         gamma-exposure math
  risk/        account-level risk primitives (evaluate_deploy_limit — fail-closed BP deploy cap)
  db/          SQLite engine mechanics: connect (dir/row_factory/pragmas) + additive migrations
  profiles/    named-profile registry + merge engine + attribution_tag + compare_profiles +
               recommend_promotion (calibration comparison + promotion advisor)
  # next: paper/ (synthetic-fill broker adapter + isolated paper store + loop harness)
```

## How consumers use it
Each consumer pins `cherrypick-core` as a **git submodule** at `src/_core` (per-repo SHA) and keeps a
*thin shim* (`src/credentials.py`, `src/session.py`) that instantiates the core class with its own
parameters and re-exports the module-level API it already imports — so the consumer refactor is minimal
and the existing `from credentials import get_secret, ...` call sites are unchanged.

```python
# example consumer src/credentials.py after cutover
from cherrypick.core.auth import CredentialStore, CredentialError, CLIENT_SECRET, REFRESH_TOKEN

store = CredentialStore("meicagent", legacy_service_names=("tastytrade-mcp",))
get_secret = store.get_secret
set_secret = store.set_secret
missing_secrets = store.missing_secrets
# ...
```

## Develop
```
pip install -e ".[dev]"
pytest
```
