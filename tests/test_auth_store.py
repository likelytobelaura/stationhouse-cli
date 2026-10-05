import json
import stat
import time
import urllib.error

import pytest

from stationhouse_cli import auth, config
from stationhouse_cli.store import clear_credentials, load_credentials, save_credentials
from conftest import jwt


def test_credentials_file_is_private():
    save_credentials({"email": "a@b.co", "access_token": "x", "id_token": "y", "refresh_token": "z", "expires_at": 1})
    assert stat.S_IMODE(config.credentials_path().stat().st_mode) == 0o600
    assert stat.S_IMODE(config.config_dir().stat().st_mode) == 0o700


def test_incomplete_or_corrupt_credentials_read_as_signed_out():
    assert load_credentials() is None
    config.config_dir().mkdir(parents=True)
    config.credentials_path().write_text("not json")
    assert load_credentials() is None
    config.credentials_path().write_text(json.dumps({"email": "a@b.co"}))
    assert load_credentials() is None


def test_clear_credentials():
    save_credentials({"email": "a@b.co", "access_token": "x", "id_token": "y", "refresh_token": "z", "expires_at": 1})
    assert clear_credentials() is True
    assert clear_credentials() is False


@pytest.mark.parametrize("bad", ["", "nobody", "a@b", "a b@c.co", None])
def test_normalize_email_rejects_non_emails(bad):
    with pytest.raises(auth.AuthError):
        auth.normalize_email(bad)


def test_normalize_email_lowercases_and_trims():
    assert auth.normalize_email("  Laura@Example.COM ") == "laura@example.com"


def test_expiry_read_from_token_and_garbage_is_expired():
    assert auth._expiry(jwt(12345)) == 12345
    assert auth._expiry("garbage") == 0.0


def test_not_signed_in_tells_the_user_to_log_in():
    with pytest.raises(auth.AuthError, match="stationhouse login"):
        auth.current_credentials()


def test_fresh_token_is_not_refreshed(signed_in, monkeypatch):
    monkeypatch.setattr(auth, "_cognito_post", lambda *a: pytest.fail("refreshed a fresh token"))
    assert auth.current_credentials()["access_token"] == signed_in["access_token"]


def test_expiring_token_is_refreshed_and_stored(signed_in, monkeypatch):
    save_credentials({**signed_in, "expires_at": time.time() + 10})
    new = jwt(time.time() + 3600)
    seen = {}

    def post(target, body):  # the shape Cognito's InitiateAuth really returns (no RefreshToken on refresh)
        seen.update(target=target, body=body)
        return {"AuthenticationResult": {"AccessToken": new, "IdToken": "new-id", "ExpiresIn": 3600}}
    monkeypatch.setattr(auth, "_cognito_post", post)
    creds = auth.current_credentials()
    assert creds["access_token"] == new and creds["refresh_token"] == "refresh"
    assert load_credentials()["id_token"] == "new-id"
    assert seen["body"]["AuthFlow"] == "REFRESH_TOKEN_AUTH" and seen["body"]["AuthParameters"] == {"REFRESH_TOKEN": "refresh"}


def test_rejected_refresh_asks_for_a_new_login(signed_in, monkeypatch):
    save_credentials({**signed_in, "expires_at": 0})

    def boom(*a):
        raise urllib.error.HTTPError("u", 400, "bad", {}, None)
    monkeypatch.setattr(auth, "_cognito_post", boom)
    with pytest.raises(auth.AuthError, match="expired"):
        auth.current_credentials()
