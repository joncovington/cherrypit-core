"""Shared fixtures for cherrypit-core tests.

Unit lane only: no real keyring backend, no broker, no network. An in-memory keyring stand-in lets
the credential + session logic be tested deterministically and offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the flat-layout `cherrypit` package importable without an install.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import keyring  # noqa: E402
import keyring.errors  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture
def mem_keyring(monkeypatch):
    """Patch keyring's password functions with an in-memory dict keyed by (service, entry)."""
    store: dict[tuple[str, str], str] = {}

    def get_password(service, entry):
        return store.get((service, entry))

    def set_password(service, entry, value):
        store[(service, entry)] = value

    def delete_password(service, entry):
        if (service, entry) not in store:
            raise keyring.errors.PasswordDeleteError("not found")
        del store[(service, entry)]

    monkeypatch.setattr(keyring, "get_password", get_password)
    monkeypatch.setattr(keyring, "set_password", set_password)
    monkeypatch.setattr(keyring, "delete_password", delete_password)
    return store
