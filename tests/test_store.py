"""Tests for the token store: roundtrip, 0o600 permissions, delete."""
import json
import os
import stat

import pytest

from publish.models import Credentials
from publish.store import TokenStore


@pytest.fixture()
def store(tmp_path):
    return TokenStore(path=tmp_path / "tokens.json")


def _creds(platform="youtube", **overrides):
    data = {
        "client_id": "cid",
        "client_secret": "csecret",
        "access_token": "sekret-access-token",
        "refresh_token": "sekret-refresh-token",
        "expires_at": 1234.0,
        "extra": {"account_label": "Test Channel"},
    }
    data.update(overrides)
    return Credentials(platform=platform, **data)


def test_save_load_roundtrip(store):
    store.save("youtube", _creds())
    loaded = store.load("youtube")
    assert loaded is not None
    assert loaded.platform == "youtube"
    assert loaded.client_id == "cid"
    assert loaded.access_token == "sekret-access-token"
    assert loaded.refresh_token == "sekret-refresh-token"
    assert loaded.expires_at == 1234.0
    assert loaded.extra == {"account_label": "Test Channel"}


def test_file_created_with_0600(store, tmp_path):
    store.save("youtube", _creds())
    mode = stat.S_IMODE(os.stat(tmp_path / "tokens.json").st_mode)
    assert mode == 0o600


def test_wrong_permissions_fixed_on_load(store, tmp_path):
    store.save("youtube", _creds())
    os.chmod(tmp_path / "tokens.json", 0o644)
    loaded = store.load("youtube")
    assert loaded is not None  # still loads
    mode = stat.S_IMODE(os.stat(tmp_path / "tokens.json").st_mode)
    assert mode == 0o600  # and repairs the mode


def test_load_missing_platform_returns_none(store):
    assert store.load("tiktok") is None


def test_load_missing_file_returns_none(tmp_path):
    assert TokenStore(path=tmp_path / "nope.json").load("youtube") is None


def test_delete_removes_platform(store):
    store.save("youtube", _creds())
    assert store.delete("youtube") is True
    assert store.load("youtube") is None


def test_delete_missing_returns_false(store):
    assert store.delete("tiktok") is False


def test_platforms_lists_saved(store):
    store.save("youtube", _creds())
    store.save("tiktok", _creds(platform="tiktok"))
    assert store.platforms() == ["tiktok", "youtube"]


def test_tokens_json_contains_no_plaintext_leak_in_repr(store, tmp_path):
    # The file obviously contains the tokens (it must), but the store object
    # itself must not leak them via repr/str.
    store.save("youtube", _creds())
    assert "sekret-access-token" not in repr(store)
    assert "sekret-access-token" not in str(store)


def test_corrupt_file_loads_as_empty(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text("{not json", encoding="utf-8")
    assert TokenStore(path=path).load("youtube") is None


def test_overwrite_keeps_other_platforms(store):
    store.save("youtube", _creds())
    store.save("youtube", _creds(access_token="new-token"))
    assert store.load("youtube").access_token == "new-token"
