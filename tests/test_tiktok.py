"""Tests for the TikTok adapter: init/upload/poll flow, audit gate, errors."""
import pytest

from publish.base import NotConnectedError, PublishError
from publish.models import Credentials, PublishRequest
from publish.store import TokenStore
from publish.tiktok import TikTokAdapter
from .fakes import FakeResponse, FakeSession

UPLOAD_URL = "https://open-upload.tiktokapis.com/video/?upload_id=1&upload_token=abc"


@pytest.fixture()
def store(tmp_path):
    return TokenStore(path=tmp_path / "tokens.json")


def _ok(data):
    return FakeResponse(200, {"error": {"code": "ok", "message": ""}, "data": data})


def _connected_adapter(store, session, approved=True):
    adapter = TikTokAdapter(store=store, session=session)
    store.save(
        "tiktok",
        Credentials(
            platform="tiktok",
            client_id="client-key",
            client_secret="client-secret",
            access_token="tt-access",
            refresh_token="tt-refresh",
            expires_at=9e18,
            extra={
                "open_id": "open123",
                "account_label": "@tester",
                "content_posting_approved": approved,
            },
        ),
    )
    adapter.poll_interval_s = 0
    return adapter


def _routes_for_success(session, publish_id="pid1", status="PUBLISH_COMPLETE"):
    session.add(
        "POST",
        "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
        _ok({"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}),
    )
    session.add(
        "POST",
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        _ok({"publish_id": publish_id, "upload_url": UPLOAD_URL}),
    )
    session.add("PUT", UPLOAD_URL, FakeResponse(201, ""))
    session.add(
        "POST",
        "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
        _ok({"status": status}),
    )


def test_full_init_upload_poll_sequence(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 1024)
    session = FakeSession()
    _routes_for_success(session)
    adapter = _connected_adapter(store, session)

    result = adapter.publish(
        PublishRequest(video_path=str(video), title="t", caption="cap", hashtags=["maps"])
    )

    assert result.ok and result.platform == "tiktok"
    assert result.url_or_id == "pid1"

    init = [c for c in session.calls if c["url"].endswith("/video/init/")][0]
    body = init["kwargs"]["json"]
    assert body["source_info"]["source"] == "FILE_UPLOAD"
    assert body["source_info"]["video_size"] == 1024
    assert body["source_info"]["total_chunk_count"] == 1
    assert body["post_info"]["privacy_level"] == "SELF_ONLY"
    assert "#maps" in body["post_info"]["title"]
    assert init["kwargs"]["headers"]["Authorization"] == "Bearer tt-access"

    put = session.last_call("PUT")
    assert put["url"] == UPLOAD_URL
    assert put["kwargs"]["headers"]["Content-Range"] == "bytes 0-1023/1024"
    assert put["kwargs"]["headers"]["Content-Type"] == "video/mp4"

    status_call = [c for c in session.calls if c["url"].endswith("/status/fetch/")][0]
    assert status_call["kwargs"]["json"] == {"publish_id": "pid1"}


def test_multi_chunk_upload_ranges(store, tmp_path):
    size = 6 * 1024 * 1024  # > 5 MB -> multiple chunks at test chunk size
    video = tmp_path / "big.mp4"
    video.write_bytes(b"\x00" * size)
    session = FakeSession()
    _routes_for_success(session)
    adapter = _connected_adapter(store, session)
    adapter.chunk_size = 2 * 1024 * 1024

    adapter.publish(PublishRequest(video_path=str(video), title="t"))

    ranges = [c["kwargs"]["headers"]["Content-Range"] for c in session.calls if c["method"] == "PUT"]
    assert ranges == [
        f"bytes 0-2097151/{size}",
        f"bytes 2097152-4194303/{size}",
        f"bytes 4194304-{size - 1}/{size}",
    ]


def test_audit_gate_blocks_publish_until_approved(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 16)
    adapter = _connected_adapter(store, FakeSession(), approved=False)
    with pytest.raises(PublishError) as exc_info:
        adapter.publish(PublishRequest(video_path=str(video), title="t"))
    message = str(exc_info.value)
    assert "audit" in message and "app review" in message
    assert "survey-publish connect tiktok" in message
    assert "docs/SETUP_TIKTOK.md" in message


def test_invalid_privacy_level_lists_options(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 16)
    session = FakeSession()
    session.add(
        "POST",
        "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
        _ok({"privacy_level_options": ["SELF_ONLY"]}),
    )
    adapter = _connected_adapter(store, session)
    with pytest.raises(PublishError, match="SELF_ONLY"):
        adapter.publish(
            PublishRequest(video_path=str(video), title="t",
                           platform_options={"privacy_level": "PUBLIC_TO_EVERYONE"})
        )


def test_init_error_envelope_surfaced(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 16)
    session = FakeSession()
    session.add(
        "POST",
        "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
        _ok({"privacy_level_options": ["SELF_ONLY"]}),
    )
    session.add(
        "POST",
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        FakeResponse(200, {"error": {"code": "scope_not_authorized", "message": "no scope"}}),
    )
    adapter = _connected_adapter(store, session)
    with pytest.raises(PublishError, match="scope_not_authorized"):
        adapter.publish(PublishRequest(video_path=str(video), title="t"))


def test_publish_failed_status_raises(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 16)
    session = FakeSession()
    _routes_for_success(session, status="PUBLISH_FAILED")
    adapter = _connected_adapter(store, session)
    with pytest.raises(PublishError, match="PUBLISH_FAILED"):
        adapter.publish(PublishRequest(video_path=str(video), title="t"))


def test_not_connected_message_is_actionable(store, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 16)
    adapter = TikTokAdapter(store=store, session=FakeSession())
    with pytest.raises(NotConnectedError) as exc_info:
        adapter.publish(PublishRequest(video_path=str(video), title="t"))
    message = str(exc_info.value)
    assert "survey-publish connect tiktok" in message
    assert "docs/SETUP_TIKTOK.md" in message


def test_chunk_plan_rules():
    adapter = TikTokAdapter(store=TokenStore(path="/tmp/nope.json"), session=FakeSession())
    assert adapter.chunk_plan(1024) == (1024, 1)  # < 5 MB: single chunk
    chunk, total = adapter.chunk_plan(50 * 1024 * 1024)
    assert chunk == 10 * 1024 * 1024 and total == 5
    with pytest.raises(PublishError, match="empty"):
        adapter.chunk_plan(0)


def test_build_authorize_url_uses_client_key():
    adapter = TikTokAdapter(store=TokenStore(path="/tmp/nope.json"), session=FakeSession())
    url = adapter.build_authorize_url("my-key")
    assert "client_key=my-key" in url
    assert "video.publish" in url
    assert "www.tiktok.com/v2/auth/authorize" in url
