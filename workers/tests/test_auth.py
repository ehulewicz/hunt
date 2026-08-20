from __future__ import annotations

import base64
import hashlib
import time

import pytest

from personal_os_worker import auth


def test_pkce_challenge_is_unpadded_s256_of_the_verifier() -> None:
    verifier, challenge = auth._pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected.decode().rstrip("=")
    assert "=" not in challenge
    # RFC 7636 requires a verifier between 43 and 128 characters.
    assert 43 <= len(verifier) <= 128


def test_pkce_pair_is_unique_per_call() -> None:
    assert auth._pkce_pair()[0] != auth._pkce_pair()[0]


def test_session_reports_expiry_before_the_token_actually_lapses() -> None:
    inside_margin = time.time() + auth.EXPIRY_MARGIN_SECONDS / 2
    assert auth.Session("a", "r", inside_margin).expired is True
    assert auth.Session("a", "r", time.time() + 3600).expired is False


def test_session_round_trips_through_disk(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PERSONAL_OS_SESSION_FILE", str(tmp_path / "session.json"))
    original = auth.Session("access", "refresh", time.time() + 3600)
    path = auth.save_session(original)
    assert path.stat().st_mode & 0o777 == 0o600
    assert auth.load_session() == original


def test_missing_or_corrupt_session_reads_as_absent(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "session.json"
    monkeypatch.setenv("PERSONAL_OS_SESSION_FILE", str(target))
    assert auth.load_session() is None
    target.write_text("{not json")
    assert auth.load_session() is None


def test_payload_without_expires_at_falls_back_to_expires_in() -> None:
    session = auth._session_from_payload(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
    )
    assert 3500 < session.expires_at - time.time() <= 3600


def test_payload_missing_tokens_is_a_login_error() -> None:
    with pytest.raises(auth.LoginError):
        auth._session_from_payload({"access_token": "a"})


def test_active_session_without_a_stored_one_is_a_login_error(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PERSONAL_OS_SESSION_FILE", str(tmp_path / "none.json"))
    with pytest.raises(auth.LoginError):
        auth.active_session(auth.SupabaseAuth("https://example.supabase.co", "key"))
