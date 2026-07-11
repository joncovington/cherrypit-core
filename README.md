# cherrypit-core

Shared core library for the **Cherrypick** trading suite. Consumed by each module (MEICAgent,
EarningsAgent, and future modules) — and by `tastytrade-mcp` — so the auth, market-data, fee, risk,
calendar, GEX, paper, and reporting concerns are implemented **once**.

> Import name is `cherrypit` (the pit = the thing at the center). Repo/package name is `cherrypit-core`.

## Design invariants (inherited from the suite plan)
1. **A library, never a service, on any loop decision path.** Loop-path code calls `cherrypit` in
   process; it must never add a network/MCP failure mode. (This is why `auth.session` builds the broker
   session lazily and lets callers inject the factory.)
2. **The core imports nothing from a consumer's `src/`.** Everything a consumer supplies (service name,
   thread-local flag, session factory, snapshots) is **injected or parameterized**, never reached back
   into. This will be enforced by an `import-linter` contract.
3. **Portable + secret-safe.** No hardcoded machine paths; credentials live in the OS keyring only.

## Layout (seeded incrementally per the roadmap)
```
cherrypit/
  auth/        credentials (CredentialStore: param service + legacy fallback)
               session     (SessionManager: param thread_local, injected session factory)
  broker/      account primitives (resolve/list/count, session injected) + option-chain
               strike helpers + order build/submit (build_order, place_order). Submission is
               fail-safe: a live order is placed only on a clean dry-run preflight.
  calendar/    trading-day / expiration calendar
  dxfeed/      on-demand DXLink collectors (quotes/greeks/last/open-interest/volume; session injected)
  fees/        commission + fee/slippage cost model
  gex/         gamma-exposure math
  risk/        account-level risk primitives (evaluate_deploy_limit — fail-closed BP deploy cap)
  # next: profiles/, paper/, db/, logging/, viz/
```

## How consumers use it
Each consumer pins `cherrypit-core` as a **git submodule** (per-repo SHA) and keeps a *thin shim*
(`src/credentials.py`, `src/session.py`) that instantiates the core class with its own parameters and
re-exports the module-level API it already imports — so the consumer refactor is minimal and the
existing `from credentials import get_secret, ...` call sites are unchanged.

```python
# example consumer src/credentials.py after cutover
from cherrypit.auth import CredentialStore, CredentialError, CLIENT_SECRET, REFRESH_TOKEN

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
