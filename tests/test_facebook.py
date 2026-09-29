"""Tests for the Facebook adapter: multipart request building, errors."""
import pytest

from publish.base import NotConnectedError, PublishError
from publish.models import Credentials, PublishRequest
from publish.store import TokenStore
from publish.facebook import FacebookAdapter
from .fakes import FakeResponse, FakeSession, error_response

PAGE_ID = "1120765000000000"
PERMALINK = "https://www.facebook.com/watch/?v=vid123"


@pytest.fixture()
def store(tmp_path):
    return TokenStore(path=tmp_path / "tokens.json")


def _connected_adapter(store, session):
    adapter = FacebookAdapter(store=store, session=session)
    store.save(
        "facebook",
        Credentials(
            platform="facebook",
            client_id="app-id",
            client_secret="app-secret",
            access_token="page-token",
            extra={"page_id": PAGE_ID, "page_name": "Test Page", "account_label": "Test Page"},
        ),
    )
    return adapter


def test_multipart_upload_request_building(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 64)
    session = FakeSession()
    session.add(
        "POST",
        f"https://graph-video.facebook.com/v25.0/{PAGE_ID}/videos",
        FakeResponse(200, {"id": "vid123"}),
    )
    session.add(
        "GET",
        "https://graph.facebook.com/v25.0/vid123",
        FakeResponse(200, {"permalink_url": PERMALINK}),
    )
    adapter = _connected_adapter(store, session)

    result = adapter.publish(
        PublishRequest(
            video_path=str(video),
            title="Test reel",
            caption="A caption",
            hashtags=["surveying"],
        )
    )

    assert result.ok and result.platform == "facebook"
    assert result.url_or_id == PERMALINK

    call = session.last_call("POST")
    assert call["url"] == f"https://graph-video.facebook.com/v25.0/{PAGE_ID}/videos"
    files = call["kwargs"]["files"]
    assert "source" in files
    filename, fileobj, content_type = files["source"]
    assert filename == "reel.mp4"
    assert content_type == "video/mp4"
    data = call["kwargs"]["data"]
    assert data["title"] == "Test reel"
    assert data["description"] == "A caption\n\n#surveying"
    assert data["access_token"] == "page-token"


def test_upload_failure_surfaces_provider_message(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 64)
    session = FakeSession()
    session.add(
        "POST",
        f"https://graph-video.facebook.com/v25.0/{PAGE_ID}/videos",
        error_response(400, "Invalid video format"),
    )
    adapter = _connected_adapter(store, session)
    with pytest.raises(PublishError, match="Invalid video format"):
        adapter.publish(PublishRequest(video_path=str(video), title="t"))


def test_not_connected_message_is_actionable(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 8)
    adapter = FacebookAdapter(store=store, session=FakeSession())
    with pytest.raises(NotConnectedError) as exc_info:
        adapter.publish(PublishRequest(video_path=str(video), title="t"))
    message = str(exc_info.value)
    assert "survey-publish connect facebook" in message
    assert "docs/SETUP_FACEBOOK.md" in message


def test_is_connected_checks_token(store):
    good = FakeSession()
    good.add("GET", "https://graph.facebook.com/v25.0/me", FakeResponse(200, {"id": "1"}))
    assert _connected_adapter(store, good).is_connected() is True

    bad = FakeSession()
    bad.add("GET", "https://graph.facebook.com/v25.0/me", error_response(400, "expired"))
    assert _connected_adapter(store, bad).is_connected() is False


def test_disconnect_removes_tokens(store, capsys):
    adapter = _connected_adapter(store, FakeSession())
    adapter.disconnect()
    assert store.load("facebook") is None
    assert "disconnected" in capsys.readouterr().out


def test_pick_page_auto_selects_single_page(store):
    session = FakeSession()
    session.add(
        "GET",
        "https://graph.facebook.com/v25.0/me/accounts",
        FakeResponse(200, {"data": [{"id": PAGE_ID, "name": "Solo", "access_token": "tok"}]}),
    )
    adapter = FacebookAdapter(store=store, session=session)
    assert adapter.pick_page("user-token") == (PAGE_ID, "Solo", "tok")


def test_pick_page_errors_when_no_pages(store):
    session = FakeSession()
    session.add(
        "GET",
        "https://graph.facebook.com/v25.0/me/accounts",
        FakeResponse(200, {"data": []}),
    )
    adapter = FacebookAdapter(store=store, session=session)
    with pytest.raises(PublishError, match="No Facebook Pages"):
        adapter.pick_page("user-token")
