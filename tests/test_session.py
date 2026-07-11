"""Tests for cherrypick.core.auth.session.SessionManager (injected factory, no broker/network)."""

import threading

import pytest

from cherrypick.core.auth import (
    CLIENT_SECRET,
    REFRESH_TOKEN,
    CredentialError,
    CredentialStore,
    SessionManager,
)


class _Counter:
    """A session factory that returns a fresh sentinel per call and counts calls."""

    def __init__(self):
        self.calls = 0

    def __call__(self, client_secret, refresh_token, is_test):
        self.calls += 1
        return {"id": self.calls, "cs": client_secret, "rt": refresh_token, "is_test": is_test}


@pytest.fixture
def store_with_secrets(mem_keyring):
    store = CredentialStore("meicagent")
    store.set_secret(CLIENT_SECRET, "cs-value")
    store.set_secret(REFRESH_TOKEN, "rt-value")
    return store


def test_builds_once_and_caches(store_with_secrets):
    factory = _Counter()
    mgr = SessionManager(store_with_secrets, thread_local=False, session_factory=factory)
    s1 = mgr.get_session()
    s2 = mgr.get_session()
    assert s1 is s2
    assert factory.calls == 1
    assert s1["cs"] == "cs-value" and s1["rt"] == "rt-value" and s1["is_test"] is False


def test_reset_forces_rebuild(store_with_secrets):
    factory = _Counter()
    mgr = SessionManager(store_with_secrets, session_factory=factory)
    mgr.get_session()
    mgr.reset_session()
    mgr.get_session()
    assert factory.calls == 2


def test_missing_credentials_raises(mem_keyring):
    empty = CredentialStore("meicagent")  # nothing stored
    mgr = SessionManager(empty, session_factory=_Counter())
    with pytest.raises(CredentialError):
        mgr.get_session()


def test_is_test_flag_passthrough(store_with_secrets):
    factory = _Counter()
    mgr = SessionManager(store_with_secrets, session_factory=factory, is_test=True)
    assert mgr.get_session()["is_test"] is True


def test_process_global_shares_one_session_across_threads(store_with_secrets):
    factory = _Counter()
    mgr = SessionManager(store_with_secrets, thread_local=False, session_factory=factory)
    results = {}

    def worker(name):
        results[name] = mgr.get_session()

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # One shared session across all threads.
    assert factory.calls == 1
    assert len({id(v) for v in results.values()}) == 1


def test_thread_local_builds_one_session_per_thread(store_with_secrets):
    factory = _Counter()
    mgr = SessionManager(store_with_secrets, thread_local=True, session_factory=factory)
    results = {}

    def worker(name):
        results[name] = mgr.get_session()

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # A distinct session per thread.
    assert factory.calls == 4
    assert len({id(v) for v in results.values()}) == 4
