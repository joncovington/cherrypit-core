# Consumer cutover — wiring cherrypit-core into a module (auth first)

This is the **deliberate, verified** step that connects a live module (MEICAgent / EarningsAgent /
tastytrade-mcp) to `cherrypit-core`. It is intentionally separate from building the library, because
these repos run real (and, for MEIC, live-enabled) workflows. Do it per module, verifying after each.

> **No credentials need to be re-added.** The core reads the *same* keyring service names and
> `production:` entry prefix the modules already use, so existing stored secrets keep working. The
> cutover is a code refactor only.

## 1. Add the submodule (per repo)
```bash
cd <module repo>
git submodule add <cherrypit-core remote-or-path> src/_core
git -C src/_core checkout <pinned SHA>
git add .gitmodules src/_core
```
The module puts `src/_core` on `sys.path` (it already inserts `src/` for `import credentials`), so
`import cherrypit` resolves. A fresh `git clone --recursive` pulls the pinned SHA; a non-recursive
clone is fixed by `git submodule update --init`.

## 2. Replace each module's `credentials.py` with a thin shim
The shim preserves the exact module-level API every call site already imports
(`from credentials import get_secret, missing_secrets, CLIENT_SECRET, ...`), so **no other file
changes**.

```python
# MEICAgent/src/credentials.py  (after cutover)
from cherrypit.auth import (
    CredentialStore, CredentialError,
    CLIENT_SECRET, REFRESH_TOKEN, ACCOUNT_NUMBER, REQUIRED_SECRETS, ALL_SECRETS,
)

store = CredentialStore("meicagent", legacy_service_names=("tastytrade-mcp",))
get_secret = store.get_secret
set_secret = store.set_secret
delete_secret = store.delete_secret
secrets_present = store.secrets_present
missing_secrets = store.missing_secrets
secrets_status = store.secrets_status
```
EarningsAgent is identical but `CredentialStore("earningsagent")` (no legacy fallback).

## 3. Replace each module's `session.py` with a thin shim
```python
# MEICAgent/src/session.py  (after cutover)  — thread-local, for the streamer's dual event loops
from cherrypit.auth import SessionManager
import credentials  # the shim above exposes `store`

_mgr = SessionManager(credentials.store, thread_local=True)
get_session = _mgr.get_session
reset_session = _mgr.reset_session
```
EarningsAgent is identical but `thread_local=False` (short-lived subprocesses, one process-wide session).

## 4. Verify (after each module, before moving on)
- **Unit:** run the module's own suite — `pytest tests/test_credentials.py tests/test_session.py`
  (plus `test_tt.py`). They should pass unchanged against the shims.
- **Live smoke (read-only):** `python src/tt.py get_connection_status` and
  `python src/tt.py get_quote --symbol XSP` (MEIC) / `--symbol AAPL` (Earnings) — confirms the same
  keyring secrets still authenticate and the session still streams.
- **Cherrypick doctor:** `cd ../Cherrypick && python cherrypick.py doctor` stays ALL GREEN
  (broker/keyring check exercises the shimmed path end to end).
- **Clone check:** `git clone --recursive` resolves `src/_core`; a non-recursive clone is repaired by
  `git submodule update --init`.

## 5. Only then delete the old inline implementations
Once the shims pass, the old bodies are gone (replaced by the shims above). Commit the module with the
submodule SHA pinned. Repeat for the next module. `tastytrade-mcp` follows the same pattern but stays a
non-suite consumer (interactive surface only, never on a loop path).
