"""Tests for cherrypit.auth.credentials.CredentialStore."""

import keyring
import keyring.errors
import pytest

from cherrypit.auth import (
    ALL_SECRETS,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    CredentialError,
    CredentialStore,
)


def test_set_get_roundtrip_uses_prefixed_entry(mem_keyring):
    store = CredentialStore("meicagent")
    store.set_secret(CLIENT_SECRET, "sekret")
    assert store.get_secret(CLIENT_SECRET) == "sekret"
    # Stored under the "production:" prefixed entry, scoped to the service name.
    assert mem_keyring[("meicagent", "production:client_secret")] == "sekret"


def test_missing_and_present(mem_keyring):
    store = CredentialStore("meicagent")
    assert store.secrets_present() is False
    assert set(store.missing_secrets()) == {CLIENT_SECRET, REFRESH_TOKEN}
    store.set_secret(CLIENT_SECRET, "a")
    store.set_secret(REFRESH_TOKEN, "b")
    assert store.secrets_present() is True
    assert store.missing_secrets() == []


def test_status_covers_all_secrets(mem_keyring):
    store = CredentialStore("earningsagent")
    status = store.secrets_status()
    assert set(status.keys()) == set(ALL_SECRETS)
    assert all(v is False for v in status.values())


def test_legacy_service_fallback_is_read_only(mem_keyring):
    # Secret exists only under the legacy service name.
    mem_keyring[("tastytrade-mcp", "production:refresh_token")] = "legacy-token"
    store = CredentialStore("meicagent", legacy_service_names=("tastytrade-mcp",))
    assert store.get_secret(REFRESH_TOKEN) == "legacy-token"

    # Writing goes to the primary service only; the legacy entry is never modified.
    store.set_secret(REFRESH_TOKEN, "new-token")
    assert mem_keyring[("meicagent", "production:refresh_token")] == "new-token"
    assert mem_keyring[("tastytrade-mcp", "production:refresh_token")] == "legacy-token"


def test_primary_takes_precedence_over_legacy(mem_keyring):
    mem_keyring[("meicagent", "production:client_secret")] = "primary"
    mem_keyring[("tastytrade-mcp", "production:client_secret")] = "legacy"
    store = CredentialStore("meicagent", legacy_service_names=("tastytrade-mcp",))
    assert store.get_secret(CLIENT_SECRET) == "primary"


def test_no_legacy_configured_returns_none(mem_keyring):
    store = CredentialStore("earningsagent")
    assert store.get_secret(CLIENT_SECRET) is None


def test_delete_is_idempotent(mem_keyring):
    store = CredentialStore("meicagent")
    store.set_secret(CLIENT_SECRET, "x")
    store.delete_secret(CLIENT_SECRET)
    store.delete_secret(CLIENT_SECRET)  # already absent -> no raise
    assert store.get_secret(CLIENT_SECRET) is None


def test_no_keyring_backend_raises_credential_error(monkeypatch):
    def boom(*_a, **_k):
        raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "get_password", boom)
    store = CredentialStore("meicagent")
    with pytest.raises(CredentialError):
        store.get_secret(CLIENT_SECRET)
