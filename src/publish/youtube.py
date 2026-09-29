"""YouTube Data API v3 adapter.

Upload path (verified against the official docs, Sept 2026):
  * Scope: ``https://www.googleapis.com/auth/youtube.upload``
  * Resumable upload: ``POST https://www.googleapis.com/upload/youtube/v3/videos``
    with ``?uploadType=resumable&part=snippet,status`` and a JSON body
    ``{snippet: {title, description, tags, categoryId},
    status: {privacyStatus, selfDeclaredMadeForKids}}``.
    The response ``Location`` header is the session URI.
  * ``PUT`` the file bytes to the session URI in chunks with
    ``Content-Range: bytes <first>-<last>/<total>``. A ``308`` response means
    "chunk received, send more"; ``200``/``201`` returns ``{"id": ...}``.

Quota (documented, not enforced client-side): each ``videos.insert`` costs
~1,600 quota units against the default 10,000 units/day project quota --
roughly 6 uploads/day unless you request a quota increase in Google Cloud
console. YouTube may also apply a separate per-day upload cap; if you hit
``uploadLimitExceeded``/``quotaExceeded``, wait and retry tomorrow.

References:
  https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
  https://developers.google.com/youtube/v3/docs/videos/insert
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import urlencode

import requests

from .base import (
    NotConnectedError,
    PlatformAdapter,
    PublishError,
    api_error_message,
)
from .models import Credentials, PublishRequest, PublishResult
from .store import TokenStore

YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_INIT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

#: Default redirect for the installed-app flow. Google's installed-app clients
#: accept loopback redirect URIs (the port is ignored when matching), so the
#: user can paste the browser address-bar URL back into the CLI -- no local
#: server required.
REDIRECT_URI = "http://127.0.0.1:8765/"

DEFAULT_CATEGORY_ID = "28"  # Science & Technology (map/data reels fit here)
DEFAULT_PRIVACY = "public"
VALID_PRIVACY = ("public", "unlisted", "private")
CHUNK_SIZE = 8 * 1024 * 1024  # multiple of 256 KiB, as the protocol requires


class YouTubeAdapter(PlatformAdapter):
    """Publish to YouTube via the Data API v3 resumable upload protocol."""

    name = "youtube"
    display_name = "YouTube"

    def __init__(self, store: "TokenStore | None" = None, session=None):
        super().__init__(store=store, session=session)
        #: Upload chunk size in bytes (lower it in tests).
        self.chunk_size = CHUNK_SIZE

    # -- OAuth ----------------------------------------------------------
    def build_authorize_url(self, client_id: str, state: str = "survey-publish") -> str:
        params = {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": YOUTUBE_SCOPE,
            "access_type": "offline",  # get a refresh token for unattended use
            "prompt": "consent",  # force re-consent so offline access is granted
            "state": state,
        }
        return AUTH_URL + "?" + urlencode(params)

    def exchange_code(self, code: str, client_id: str, client_secret: str) -> dict:
        resp = self.session.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "YouTube token exchange"))
        data = resp.json()
        if "access_token" not in data:
            raise PublishError(
                "YouTube token exchange returned no access token. "
                "The code may have expired -- run the connect flow again."
            )
        return data

    def refresh_access_token(self, creds: Credentials) -> Credentials:
        if not creds.refresh_token:
            raise NotConnectedError(
                "YouTube refresh token is missing. " + self._not_connected_message()
            )
        resp = self.session.post(
            TOKEN_URL,
            data={
                "refresh_token": creds.refresh_token,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "YouTube token refresh"))
        data = resp.json()
        creds.access_token = data["access_token"]
        creds.expires_at = time.time() + int(data.get("expires_in", 3600))
        # Google only returns a new refresh token on the first exchange.
        if data.get("refresh_token"):
            creds.refresh_token = data["refresh_token"]
        self.store.save(self.name, creds)
        return creds

    def connect(self) -> None:
        client_id, client_secret = self._prompt_client_credentials(
            "SURVEY_PUBLISH_YOUTUBE", "Google OAuth client ID", "Google OAuth client secret"
        )
        tokens = self._installed_app_flow(
            self.build_authorize_url(client_id),
            lambda code: self.exchange_code(code, client_id, client_secret),
            "After authorizing, your browser will try to open a page that fails to "
            "load -- that is expected. Copy the FULL address from the browser "
            "address bar (it contains code=...) and paste it below.",
        )
        creds = Credentials(
            platform=self.name,
            client_id=client_id,
            client_secret=client_secret,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token", ""),
            expires_at=time.time() + int(tokens.get("expires_in", 3600)),
        )
        self.store.save(self.name, creds)
        # Verify against the API and record the channel name as the label.
        channel_title = self._fetch_channel_title(creds.access_token)
        creds.extra["account_label"] = channel_title
        self.store.save(self.name, creds)
        print(f"\nConnected to YouTube as '{channel_title}'.")

    def _fetch_channel_title(self, access_token: str) -> str:
        resp = self.session.get(
            CHANNELS_URL,
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "YouTube channel lookup"))
        items = resp.json().get("items", [])
        if not items:
            raise PublishError(
                "YouTube authorized, but no channel was found on this Google account. "
                "Create a YouTube channel first (see docs/SETUP_YOUTUBE.md), then reconnect."
            )
        return items[0]["snippet"]["title"]

    def _ensure_access_token(self) -> str:
        creds = self._require_connected()
        if creds.expires_at and time.time() > creds.expires_at - 60:
            creds = self.refresh_access_token(creds)
        return creds.access_token

    def is_connected(self) -> bool:
        creds = self.store.load(self.name)
        if creds is None or not creds.access_token:
            return False
        try:
            token = self._ensure_access_token()
            resp = self.session.get(
                CHANNELS_URL,
                params={"part": "id", "mine": "true"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            return resp.status_code == 200
        except PublishError:
            return False
        except requests.RequestException:
            return False

    # -- publish --------------------------------------------------------
    def build_metadata(self, request: PublishRequest) -> dict:
        """Build the ``videos.insert`` JSON body from a PublishRequest."""
        privacy = str(request.platform_options.get("privacy", DEFAULT_PRIVACY)).lower()
        if privacy not in VALID_PRIVACY:
            raise PublishError(
                f"Invalid YouTube privacy {privacy!r}. "
                f"Valid values: {', '.join(VALID_PRIVACY)} "
                "(pass platform_options={'privacy': 'unlisted'})."
            )
        tags = [t[:30] for t in request.tag_list()][:30]
        return {
            "snippet": {
                "title": request.title[:100],
                "description": request.full_caption()[:5000],
                "tags": tags,
                "categoryId": str(request.platform_options.get("category_id", DEFAULT_CATEGORY_ID)),
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

    def initiate_upload(self, access_token: str, metadata: dict, size: int) -> str:
        """Start a resumable session; return the session URI from Location."""
        resp = self.session.post(
            UPLOAD_INIT_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            json=metadata,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": "video/mp4",
            },
            timeout=60,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "YouTube upload initiation"))
        session_uri = resp.headers.get("Location")
        if not session_uri:
            raise PublishError(
                "YouTube upload initiation returned no session URL (missing "
                "Location header). Try again; if it persists, check the API status."
            )
        return session_uri

    @staticmethod
    def _resume_offset(resp, offset: int, chunk_len: int) -> int:
        """Next byte to send after a 308, from the ``Range`` header."""
        match = re.match(r"bytes=0-(\d+)", resp.headers.get("Range", ""))
        if match:
            return int(match.group(1)) + 1
        return offset + chunk_len

    def upload_bytes(self, session_uri: str, path: str, size: int) -> str:
        """PUT the file to the session URI in chunks; return the video id."""
        offset = 0
        with open(path, "rb") as fh:
            while offset < size:
                fh.seek(offset)
                chunk = fh.read(self.chunk_size)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                resp = self.session.put(
                    session_uri,
                    data=chunk,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    },
                    timeout=300,
                )
                if resp.status_code in (200, 201):
                    video_id = resp.json().get("id")
                    if not video_id:
                        raise PublishError(
                            "YouTube upload finished but the response had no video id."
                        )
                    return video_id
                if resp.status_code == 308:
                    offset = self._resume_offset(resp, offset, len(chunk))
                    continue
                raise PublishError(api_error_message(resp, "YouTube upload"))
        raise PublishError("YouTube upload ended without a video id in the response.")

    def publish(self, request: PublishRequest) -> PublishResult:
        path = self._require_video(request)
        token = self._ensure_access_token()
        metadata = self.build_metadata(request)
        size = os.path.getsize(path)
        session_uri = self.initiate_upload(token, metadata, size)
        video_id = self.upload_bytes(session_uri, path, size)
        return PublishResult.success(
            platform=self.name,
            url_or_id=f"https://www.youtube.com/watch?v={video_id}",
        )
