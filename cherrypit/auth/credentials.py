"""Keyring-backed credential storage for tastytrade OAuth (parameterized).

Unifies MEICAgent's and EarningsAgent's near-identical `credentials.py`. The only real differences
were (a) the keyring service name and (b) MEIC's read-only fallback to a pre-rename service name, so
both become constructor parameters of a `CredentialStore`. Secrets are read/written only through the
OS keyring — never files, env vars, or logs.
"""

from __future__ import annotations

from collections.abc import Iterable

import keyring
import keyring.errors

CLIENT_SECRET = "client_secret"
REFRESH_TOKEN = "refresh_token"
ACCOUNT_NUMBER = "account_number"

REQUIRED_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN)
ALL_SECRETS = (CLIENT_SECRET, REFRESH_TOKEN, ACCOUNT_NUMBER)

_DEFAULT_PREFIX = "production"


class CredentialError(RuntimeError):
    pass


class CredentialStore:
    """Keyring-backed OAuth secret store for one consumer.

    Args:
        service_name: primary keyring service the consumer writes/reads under (e.g. "meicagent").
        legacy_service_names: optional read-only fallbacks for secrets stored under an older service
            name (e.g. "tastytrade-mcp" before a rename). Never written to; only tried on a miss,
            in order, so existing stored credentials keep working without a re-entry.
        prefix: entry-name prefix (defaults to "production"), matching the original `_PREFIX`.
    """

    def __init__(self, service_name: str, legacy_service_names: Iterable[str] = (),
                 prefix: str = _DEFAULT_PREFIX):
        self.service_name = service_name
        self.legacy_service_names = tuple(legacy_service_names)
        self.prefix = prefix

    def _entry(self, key: str) -> str:
        return f"{self.prefix}:{key}"

    def get_secret(self, key: str) -> str | None:
        try:
            value = keyring.get_password(self.service_name, self._entry(key))
        except keyring.errors.NoKeyringError as exc:
            raise CredentialError("No keyring backend available.") from exc
        except keyring.errors.KeyringError as exc:
            raise CredentialError(f"Keyring read failed: {exc}") from exc
        if value is not None:
            return value
        # Read-only fallback to any legacy service name(s) so pre-rename credentials keep working.
        for legacy in self.legacy_service_names:
            try:
                legacy_value = keyring.get_password(legacy, self._entry(key))
            except keyring.errors.KeyringError:
                continue
            if legacy_value is not None:
                return legacy_value
        return None

    def secrets_present(self) -> bool:
        return all(self.get_secret(k) for k in REQUIRED_SECRETS)

    def missing_secrets(self) -> list[str]:
        return [k for k in REQUIRED_SECRETS if not self.get_secret(k)]

    def set_secret(self, key: str, value: str) -> None:
        try:
            keyring.set_password(self.service_name, self._entry(key), value)
        except keyring.errors.NoKeyringError as exc:
            raise CredentialError("No keyring backend available.") from exc
        except keyring.errors.KeyringError as exc:
            raise CredentialError(f"Keyring write failed: {exc}") from exc

    def delete_secret(self, key: str) -> None:
        try:
            keyring.delete_password(self.service_name, self._entry(key))
        except keyring.errors.PasswordDeleteError:
            pass  # already absent
        except keyring.errors.KeyringError as exc:
            raise CredentialError(f"Keyring delete failed: {exc}") from exc

    def secrets_status(self) -> dict[str, bool]:
        """Return {key: is_set} for all known secrets."""
        return {k: bool(self.get_secret(k)) for k in ALL_SECRETS}
