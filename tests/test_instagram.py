"""Tests for the Instagram adapter: two-step container flow, polling, errors."""
import pytest

from publish.base import NotConnectedError, PublishError
from publish.models import Credentials, PublishRequest
from publish.store import TokenStore
from publish.instagram import InstagramAdapter
from .fakes import FakeResponse, FakeSession, error_response

IG_ID = "17841400000000000"
PERMALINK = "https://www.instagram.com/reel/ABC123/"


@pytest.fixture()
def store(tmp_path):
    return TokenStore(path=tmp_path / "tokens.json")


def _connected_adapter(store, session, **extra):
    adapter = InstagramAdapter(store=store, session=session)
    store.save(
        "instagram",
        Credentials(
            platform="instagram",
            client_id="app-id",
            client_secret="app-secret",
            access_token="ig-page-token",
            extra={
                "page_id": "123",
                "ig_user_id": IG_ID,
                "ig_username": "testpage",
                "account_label": "@testpage",
                **extra,
            },
        ),
    )
    adapter.poll_interval_s = 0
    return adapter


def _request(**kwargs):
    data = {
        "video_path": "/tmp/reel.mp4",
        "title": "Test reel",
        "caption": "A test caption",
        "hashtags": ["surveying"],
        "platform_options": {"video_url": "https://example.com/reel.mp4"},
    }
    data.update(kwargs)
    return PublishRequest(**data)


def _finished_container(session):
    session.add(
        "GET",
        f"https://graph.facebook.com/v25.0/container1",
        FakeResponse(200, {"status_code": "FINISHED"}),
    )


def test_full_two_step_sequence(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 16)
    session = FakeSession()
    # NOTE: register the media_publish route BEFORE the /media route: the
    # fake matches on URL prefix, and ".../media_publish" startswith ".../media".
    session.add(
        "POST",
        f"https://graph.facebook.com/v25.0/{IG_ID}/media_publish",
        FakeResponse(200, {"id": "media1"}),
    )
    session.add(
        "POST",
        f"https://graph.facebook.com/v25.0/{IG_ID}/media",
        FakeResponse(200, {"id": "container1"}),
    )
    _finished_container(session)
    session.add(
        "GET",
        "https://graph.facebook.com/v25.0/media1",
        FakeResponse(200, {"permalink": PERMALINK}),
    )
    adapter = _connected_adapter(store, session)

    result = adapter.publish(_request(video_path=str(video)))

    assert result.ok and result.platform == "instagram"
    assert result.url_or_id == PERMALINK

    create = session.calls[0]
    assert create["method"] == "POST"
    payload = create["kwargs"]["data"]
    assert payload["media_type"] == "REELS"
    assert payload["video_url"] == "https://example.com/reel.mp4"
    assert payload["caption"] == "A test caption\n\n#surveying"
    assert payload["access_token"] == "ig-page-token"

    publish_call = [c for c in session.calls if c["url"].endswith("/media_publish")][0]
    assert publish_call["kwargs"]["data"]["creation_id"] == "container1"


def test_poll_until_finished(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 16)
    states = iter(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    session = FakeSession()
    # NOTE: media_publish route first -- the fake matches on URL prefix and
    # ".../media_publish" startswith ".../media".
    session.add(
        "POST",
        f"https://graph.facebook.com/v25.0/{IG_ID}/media_publish",
        FakeResponse(200, {"id": "media9"}),
    )
    session.add(
        "POST",
        f"https://graph.facebook.com/v25.0/{IG_ID}/media",
        FakeResponse(200, {"id": "container1"}),
    )
    session.add(
        "GET",
        "https://graph.facebook.com/v25.0/container1",
        lambda kwargs: FakeResponse(200, {"status_code": next(states)}),
    )
    session.add(
        "GET",
        "https://graph.facebook.com/v25.0/media9",
        FakeResponse(200, {}),
    )
    adapter = _connected_adapter(store, session)

    result = adapter.publish(_request(video_path=str(video)))
    assert result.ok
    polls = [c for c in session.calls if c["method"] == "GET" and "container1" in c["url"]]
    assert len(polls) == 3


def test_container_error_is_terminal(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 16)
    session = FakeSession()
    session.add(
        "POST",
        f"https://graph.facebook.com/v25.0/{IG_ID}/media",
        FakeResponse(200, {"id": "container1"}),
    )
    session.add(
        "GET",
        "https://graph.facebook.com/v25.0/container1",
        FakeResponse(200, {"status_code": "ERROR", "status": "bad video"}),
    )
    adapter = _connected_adapter(store, session)
    with pytest.raises(PublishError, match="could not process"):
        adapter.publish(_request(video_path=str(video)))


def test_missing_video_url_raises_actionable_error(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 16)
    adapter = _connected_adapter(store, FakeSession())
    with pytest.raises(PublishError) as exc_info:
        adapter.publish(_request(video_path=str(video), platform_options={}))
    message = str(exc_info.value)
    assert "public" in message and "video_url" in message


def test_not_connected_message_is_actionable(store):
    adapter = InstagramAdapter(store=store, session=FakeSession())
    with pytest.raises(NotConnectedError) as exc_info:
        adapter.publish(_request())
    message = str(exc_info.value)
    assert "survey-publish connect instagram" in message
    assert "docs/SETUP_INSTAGRAM.md" in message


def test_ineligible_account_raises_actionable_error(store):
    session = FakeSession()
    session.add(
        "GET",
        "https://graph.facebook.com/v25.0/123",
        FakeResponse(200, {}),  # no instagram_business_account linked
    )
    adapter = InstagramAdapter(store=store, session=session)
    with pytest.raises(PublishError) as exc_info:
        adapter._resolve_instagram_account("123", "tok")
    message = str(exc_info.value)
    assert "Business or Creator" in message
    assert "linked" in message


def test_resolve_instagram_account_success(store):
    session = FakeSession()
    session.add(
        "GET",
        "https://graph.facebook.com/v25.0/123",
        FakeResponse(200, {"instagram_business_account": {"id": IG_ID, "username": "biz"}}),
    )
    adapter = InstagramAdapter(store=store, session=session)
    assert adapter._resolve_instagram_account("123", "tok") == (IG_ID, "biz")


def test_is_connected_false_without_credentials(store):
    adapter = InstagramAdapter(store=store, session=FakeSession())
    assert adapter.is_connected() is False


def test_is_connected_true_when_api_ok(store):
    session = FakeSession()
    session.add(
        "GET",
        f"https://graph.facebook.com/v25.0/{IG_ID}",
        FakeResponse(200, {"id": IG_ID, "username": "testpage"}),
    )
    adapter = _connected_adapter(store, session)
    assert adapter.is_connected() is True


def test_is_connected_false_when_api_rejects(store):
    session = FakeSession()
    session.add("GET", f"https://graph.facebook.com/v25.0/{IG_ID}", error_response(400, "expired"))
    adapter = _connected_adapter(store, session)
    assert adapter.is_connected() is False
