"""Lazy tastytrade OAuth session management (parameterized).

Unifies MEICAgent's thread-local session and EarningsAgent's process-global session. The only real
difference was caching scope, so `thread_local` is a constructor flag:

  - thread_local=True  (MEIC): the streamer daemon runs the DXLink connection on the main thread's
    event loop and a REST poller on a separate thread with its own loop; tastytrade's Session holds an
    httpx.AsyncClient bound to whichever loop first uses it, so a session-per-thread keeps each bound
    to a single loop (sharing one would silently hang awaits from the second loop).
  - thread_local=False (Earnings): short-lived `tt.py` subprocesses with no daemon — one process-wide
    cached session is sufficient.

The broker session is built lazily and via an injectable factory, so this module imports (and unit
tests) without `tastytrade` installed and without a live network/auth — honoring the suite invariant
that core code never forces a network dependency at import time.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .credentials import CLIENT_SECRET, REFRESH_TOKEN, CredentialError, CredentialStore

# A factory turns (client_secret, refresh_token, is_test) into a broker session object.
SessionFactory = Callable[[str, str, bool], Any]


def _default_session_factory(client_secret: str, refresh_token: str, is_test: bool) -> Any:
    from tastytrade import Session  # imported lazily so core imports without the broker SDK
    return Session(client_secret, refresh_token, is_test=is_test)


class SessionManager:
    def __init__(self, credentials: CredentialStore, thread_local: bool = False,
                 session_factory: SessionFactory | None = None, is_test: bool = False):
        self._creds = credentials
        self._thread_local = thread_local
        self._factory = session_factory or _default_session_factory
        self._is_test = is_test
        self._local = threading.local() if thread_local else None
        self._global_session: Any = None

    def _get_cached(self) -> Any:
        if self._thread_local:
            return getattr(self._local, "session", None)
        return self._global_session

    def _set_cached(self, session: Any) -> None:
        if self._thread_local:
            self._local.session = session
        else:
            self._global_session = session

    def get_session(self) -> Any:
        """Return a cached OAuth session, building it on first use (per-thread or process-wide)."""
        session = self._get_cached()
        if session is None:
            missing = self._creds.missing_secrets()
            if missing:
                raise CredentialError(
                    f"Missing credentials: {', '.join(missing)}. "
                    "Run your module's `secrets_set` command to store them."
                )
            client_secret = self._creds.get_secret(CLIENT_SECRET)
            refresh_token = self._creds.get_secret(REFRESH_TOKEN)
            session = self._factory(client_secret, refresh_token, self._is_test)
            self._set_cached(session)
        return session

    def reset_session(self) -> None:
        self._set_cached(None)
