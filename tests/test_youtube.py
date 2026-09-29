"""Tests for the YouTube adapter: resumable-upload request building, errors."""
import pytest

from publish.base import NotConnectedError, PublishError
from publish.models import Credentials, PublishRequest
from publish.store import TokenStore
from publish.youtube import YouTubeAdapter
from .fakes import FakeResponse, FakeSession, error_response


@pytest.fixture()
def store(tmp_path):
    return TokenStore(path=tmp_path / "tokens.json")


@pytest.fixture()
def video(tmp_path):
    path = tmp_path / "reel.mp4"
    path.write_bytes(b"\x00" * 1024)
    return str(path)


def _connected_adapter(store, session, **extra):
    adapter = YouTubeAdapter(store=store, session=session)
    store.save(
        "youtube",
        Credentials(
            platform="youtube",
            client_id="cid",
            client_secret="csec",
            access_token="ya29.test",
            refresh_token="refresh",
            expires_at=9e18,  # far future: no refresh attempted
            extra={"account_label": "Test Channel", **extra},
        ),
    )
    return adapter


def _request(video, **kwargs):
    data = {
        "video_path": video,
        "title": "Test reel",
        "caption": "A test caption",
        "hashtags": ["surveying", "maps"],
    }
    data.update(kwargs)
    return PublishRequest(**data)


SESSION_URI = "https://www.googleapis.com/upload/youtube/v3/upload_session/abc"


def test_initiate_and_single_chunk_put(store, video):
    session = FakeSession()
    session.add(
        "POST",
        "https://www.googleapis.com/upload/youtube/v3/videos",
        FakeResponse(200, {}, headers={"Location": SESSION_URI}),
    )
    session.add("PUT", SESSION_URI, FakeResponse(200, {"id": "vid123"}))
    adapter = _connected_adapter(store, session)

    result = adapter.publish(_request(video))

    assert result.ok and result.platform == "youtube"
    assert result.url_or_id == "https://www.youtube.com/watch?v=vid123"

    init = session.last_call("POST")
    assert init["kwargs"]["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    body = init["kwargs"]["json"]
    assert body["snippet"]["title"] == "Test reel"
    assert body["snippet"]["description"] == "A test caption\n\n#surveying #maps"
    assert body["snippet"]["tags"] == ["surveying", "maps"]
    assert body["snippet"]["categoryId"] == "28"
    assert body["status"]["privacyStatus"] == "public"
    headers = init["kwargs"]["headers"]
    assert headers["X-Upload-Content-Length"] == "1024"
    assert headers["X-Upload-Content-Type"] == "video/mp4"
    assert headers["Authorization"] == "Bearer ya29.test"

    put = session.last_call("PUT")
    assert put["url"] == SESSION_URI
    assert put["kwargs"]["headers"]["Content-Range"] == "bytes 0-1023/1024"
    assert put["kwargs"]["headers"]["Content-Length"] == "1024"


def test_chunked_upload_with_308_resume(store, tmp_path):
    video = tmp_path / "big.mp4"
    video.write_bytes(b"\x00" * 10)
    session = FakeSession()
    session.add(
        "POST",
        "https://www.googleapis.com/upload/youtube/v3/videos",
        FakeResponse(200, {}, headers={"Location": SESSION_URI}),
    )

    def put_route(kwargs):
        crange = kwargs["headers"]["Content-Range"]
        if crange == "bytes 0-3/10":
            return FakeResponse(308, None, headers={"Range": "bytes=0-3"})
        if crange == "bytes 4-7/10":
            return FakeResponse(308, None, headers={"Range": "bytes=0-7"})
        if crange == "bytes 8-9/10":
            return FakeResponse(200, {"id": "vid999"})
        raise AssertionError(f"unexpected range {crange}")

    session.add("PUT", SESSION_URI, put_route)
    adapter = _connected_adapter(store, session)
    adapter.chunk_size = 4

    result = adapter.publish(_request(str(video)))
    assert result.url_or_id == "https://www.youtube.com/watch?v=vid999"
    ranges = [c["kwargs"]["headers"]["Content-Range"] for c in session.calls if c["method"] == "PUT"]
    assert ranges == ["bytes 0-3/10", "bytes 4-7/10", "bytes 8-9/10"]


def test_initiate_failure_raises_actionable_error(store, video):
    session = FakeSession()
    session.add(
        "POST",
        "https://www.googleapis.com/upload/youtube/v3/videos",
        error_response(403, "quotaExceeded"),
    )
    adapter = _connected_adapter(store, session)
    with pytest.raises(PublishError, match="(?i)quotaExceeded"):
        adapter.publish(_request(video))


def test_missing_location_header_raises(store, video):
    session = FakeSession()
    session.add("POST", "https://www.googleapis.com/upload/youtube/v3/videos",
                FakeResponse(200, {}))
    adapter = _connected_adapter(store, session)
    with pytest.raises(PublishError, match="Location"):
        adapter.publish(_request(video))


def test_not_connected_message_is_actionable(store, video):
    adapter = YouTubeAdapter(store=store, session=FakeSession())
    with pytest.raises(NotConnectedError) as exc_info:
        adapter.publish(_request(video))
    message = str(exc_info.value)
    assert "survey-publish connect youtube" in message
    assert "docs/SETUP_YOUTUBE.md" in message
    assert "ya29" not in message  # no token leakage


def test_invalid_privacy_rejected(store, video):
    session = FakeSession()
    adapter = _connected_adapter(store, session)
    with pytest.raises(PublishError, match="public.*unlisted.*private|Invalid YouTube privacy"):
        adapter.publish(_request(video, platform_options={"privacy": "everyone"}))


def test_missing_video_file(store, tmp_path):
    session = FakeSession()
    adapter = _connected_adapter(store, session)
    with pytest.raises(PublishError, match="not found"):
        adapter.publish(_request(str(tmp_path / "missing.mp4")))


def test_expired_token_triggers_refresh(store, video):
    session = FakeSession()
    session.add(
        "POST",
        "https://oauth2.googleapis.com/token",
        FakeResponse(200, {"access_token": "ya29.fresh", "expires_in": 3600}),
    )
    session.add(
        "POST",
        "https://www.googleapis.com/upload/youtube/v3/videos",
        FakeResponse(200, {}, headers={"Location": SESSION_URI}),
    )
    session.add("PUT", SESSION_URI, FakeResponse(200, {"id": "vid1"}))
    adapter = _connected_adapter(store, session)
    # Force expiry.
    creds = store.load("youtube")
    creds.expires_at = 1.0
    store.save("youtube", creds)

    adapter.publish(_request(video))
    init = session.last_call("POST")
    assert init["kwargs"]["headers"]["Authorization"] == "Bearer ya29.fresh"
    assert store.load("youtube").access_token == "ya29.fresh"


def test_build_authorize_url_has_offline_access():
    adapter = YouTubeAdapter(store=TokenStore(path="/tmp/nope.json"), session=FakeSession())
    url = adapter.build_authorize_url("my-client-id")
    assert "access_type=offline" in url
    assert "youtube.upload" in url
    assert "accounts.google.com" in url
