"""cherrypick.core.auth — keyring credentials + lazy OAuth session (parameterized for each consumer)."""

from .credentials import (
    ACCOUNT_NUMBER,
    ALL_SECRETS,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    REQUIRED_SECRETS,
    CredentialError,
    CredentialStore,
)
from .session import SessionFactory, SessionManager

__all__ = [
    "CredentialStore",
    "CredentialError",
    "SessionManager",
    "SessionFactory",
    "CLIENT_SECRET",
    "REFRESH_TOKEN",
    "ACCOUNT_NUMBER",
    "REQUIRED_SECRETS",
    "ALL_SECRETS",
]
