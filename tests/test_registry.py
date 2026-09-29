"""Tests for the platform registry."""
import pytest

from publish import registry
from publish.registry import UnknownPlatformError, get_adapter, list_platforms


def test_list_platforms_has_all_four():
    assert list_platforms() == ["facebook", "instagram", "tiktok", "youtube"]


def test_get_adapter_resolves_each_platform():
    assert get_adapter("youtube").name == "youtube"
    assert get_adapter("instagram").name == "instagram"
    assert get_adapter("facebook").name == "facebook"
    assert get_adapter("tiktok").name == "tiktok"


def test_get_adapter_is_case_insensitive_and_trims():
    assert get_adapter("  YouTube ").name == "youtube"


def test_unknown_platform_error_lists_valid_names():
    with pytest.raises(UnknownPlatformError) as exc_info:
        get_adapter("myspace")
    message = str(exc_info.value)
    for name in ("youtube", "instagram", "facebook", "tiktok"):
        assert name in message


def test_unknown_platform_is_value_error():
    with pytest.raises(ValueError):
        get_adapter("")


def test_get_adapter_passes_store_and_session():
    sentinel_store, sentinel_session = object(), object()
    adapter = get_adapter("youtube", store=sentinel_store, session=sentinel_session)
    assert adapter.store is sentinel_store
    assert adapter.session is sentinel_session


def test_platforms_dict_matches_list():
    assert sorted(registry.PLATFORMS) == list_platforms()
