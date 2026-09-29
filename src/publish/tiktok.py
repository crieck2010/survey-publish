"""TikTok Content Posting API adapter (Direct Post, FILE_UPLOAD).

Flow (verified against the official TikTok developer docs, Sept 2026):
  1. ``POST https://open.tiktokapis.com/v2/post/publish/creator_info/query/``
     -> allowed ``privacy_level_options`` for the account.
  2. ``POST https://open.tiktokapis.com/v2/post/publish/video/init/`` with
     ``post_info`` (title, privacy_level, ...) and
     ``source_info: {source: "FILE_UPLOAD", video_size, chunk_size,
     total_chunk_count}`` -> ``publish_id`` + ``upload_url``.
     Errors arrive in the ``{"error": {"code": ..., "message": ...}}``
     envelope; ``code == "ok"`` means success.
  3. ``PUT`` the bytes to ``upload_url`` in sequential chunks with
     ``Content-Type: video/mp4``, ``Content-Length``, and
     ``Content-Range: bytes <first>-<last>/<total>``.
     Chunk rules: 5 MB minimum / 64 MB maximum per chunk (final chunk may
     absorb the remainder up to 128 MB); files under 5 MB go as one chunk.
     Intermediate chunks return 206, the last returns 201.
  4. Poll ``POST https://open.tiktokapis.com/v2/post/publish/status/fetch/``
     with ``{"publish_id": ...}`` until ``PUBLISH_COMPLETE`` (``FAILED`` /
     ``PUBLISH_FAILED`` are terminal).

App-review gate: TikTok only enables the Content Posting API for a developer
app that has **passed TikTok's audit/app review** for the
``video.publish`` scope. Until that happens, API posts are rejected
server-side. This adapter therefore refuses to run ``publish()`` until the
app is marked approved: ``connect()`` asks and records the answer in
``extra["content_posting_approved"]``; ``publish()`` raises an actionable
error when it is not set. (The separate "Upload to Drafts" inbox flow,
scope ``video.upload``, is not implemented in v0.1.0.)

OAuth: ``GET https://www.tiktok.com/v2/auth/authorize/`` ->
``POST https://open.tiktokapis.com/v2/oauth/token/``.
Scopes requested: ``user.info.basic,video.publish``.

Uncertainty documented honestly: some third-party integrations report that
TikTok's Direct Post endpoint additionally requires the
``brand_content_toggle`` / ``brand_organic_toggle`` flags in ``post_info``.
TikTok's own Direct Post example does not include them, so this engine sends
only the documented fields; if TikTok rejects the init call asking for those
flags, see docs/SETUP_TIKTOK.md for the workaround.

References:
  https://developers.tiktok.com/docs/en/content-posting-api-get-started
  https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content
"""
from __future__ import annotations

import math
import os
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

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
VIDEO_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

SCOPES = "user.info.basic,video.publish"

#: Redirect URI for the CLI flow. Register this exact URI in the TikTok
#: developer portal. The browser will fail to load it -- expected; the user
#: pastes the address-bar URL (carrying ``code=...``) back into the CLI.
REDIRECT_URI = "https://localhost/callback"

CHUNK_SIZE = 10 * 1024 * 1024  # inside TikTok's 5-64 MB per-chunk window
TITLE_LIMIT = 2200


class TikTokAdapter(PlatformAdapter):
    """Direct-post videos to TikTok via the Content Posting API."""

    name = "tiktok"
    display_name = "TikTok"

    def __init__(self, store: "TokenStore | None" = None, session=None):
        super().__init__(store=store, session=session)
        self.chunk_size = CHUNK_SIZE

    # -- OAuth ----------------------------------------------------------
    def build_authorize_url(self, client_key: str, state: str = "survey-publish") -> str:
        params = {
            "client_key": client_key,
            "response_type": "code",
            "scope": SCOPES,
            "redirect_uri": REDIRECT_URI,
            "state": state,
        }
        return AUTH_URL + "?" + urlencode(params)

    def exchange_code(self, code: str, client_key: str, client_secret: str) -> dict:
        resp = self.session.post(
            TOKEN_URL,
            json={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "TikTok token exchange"))
        data = resp.json()
        if "access_token" not in data:
            raise PublishError(
                "TikTok token exchange returned no access token. "
                "The code may have expired -- run the connect flow again."
            )
        return data

    def refresh_access_token(self, creds: Credentials) -> Credentials:
        if not creds.refresh_token:
            raise NotConnectedError(
                "TikTok refresh token is missing. " + self._not_connected_message()
            )
        resp = self.session.post(
            TOKEN_URL,
            json={
                "client_key": creds.client_id,
                "client_secret": creds.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": creds.refresh_token,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "TikTok token refresh"))
        data = resp.json()
        creds.access_token = data["access_token"]
        creds.expires_at = time.time() + int(data.get("expires_in", 86400))
        if data.get("refresh_token"):
            creds.refresh_token = data["refresh_token"]
        self.store.save(self.name, creds)
        return creds

    def _ensure_access_token(self) -> Credentials:
        creds = self._require_connected()
        if creds.expires_at and time.time() > creds.expires_at - 60:
            creds = self.refresh_access_token(creds)
        return creds

    def _api_post(self, url: str, token: str, payload: dict, context: str) -> dict:
        """POST to a TikTok JSON endpoint; unwrap the error envelope."""
        resp = self.session.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=payload,
            timeout=60,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, context))
        body = resp.json()
        err = (body.get("error") or {}) if isinstance(body, dict) else {}
        code = err.get("code") or "unknown"
        if code != "ok":
            message = err.get("message") or "no message"
            raise PublishError(f"{context} failed. TikTok said: {message} (code: {code})")
        return body.get("data", {})

    def connect(self) -> None:
        # TikTok names the id "client key"; we store it in Credentials.client_id.
        client_key, client_secret = self._prompt_client_credentials(
            "SURVEY_PUBLISH_TIKTOK", "TikTok client key", "TikTok client secret"
        )
        tokens = self._installed_app_flow(
            self.build_authorize_url(client_key),
            lambda code: self.exchange_code(code, client_key, client_secret),
            "After authorizing, your browser will fail to load the redirect page "
            "-- that is expected. Copy the FULL address from the browser address "
            "bar (it contains code=...) and paste it below.",
        )
        creds = Credentials(
            platform=self.name,
            client_id=client_key,
            client_secret=client_secret,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token", ""),
            expires_at=time.time() + int(tokens.get("expires_in", 86400)),
            extra={"open_id": tokens.get("open_id", ""), "scope": tokens.get("scope", "")},
        )
        self.store.save(self.name, creds)

        display_name = self._fetch_display_name(creds.access_token)
        approved = self._prompt_app_approval()
        creds.extra["account_label"] = "@" + display_name if display_name else ""
        creds.extra["content_posting_approved"] = approved
        self.store.save(self.name, creds)
        print(f"\nConnected to TikTok as @{display_name}.")

    def _fetch_display_name(self, access_token: str) -> str:
        resp = self.session.post(
            USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"fields": ["open_id", "display_name"]},
            timeout=30,
        )
        data = self._unwrap_user_info(resp)
        return (data.get("user") or {}).get("display_name", "")

    def _unwrap_user_info(self, resp) -> dict:
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "TikTok user info"))
        body = resp.json()
        err = (body.get("error") or {}) if isinstance(body, dict) else {}
        if err.get("code") != "ok":
            raise PublishError(
                "TikTok user info failed. TikTok said: "
                + str(err.get("message") or err.get("code"))
            )
        return body.get("data", {})

    @staticmethod
    def _prompt_app_approval() -> bool:
        print(
            "\nTikTok only enables API posting after your developer app passes "
            "TikTok's audit/app review for the Content Posting API."
        )
        answer = input("Has this app passed the audit/app review? (y/N): ").strip().lower()
        approved = answer in ("y", "yes")
        if not approved:
            print(
                "Recorded as NOT approved. publish() will refuse to run until you "
                "reconnect after approval -- see docs/SETUP_TIKTOK.md."
            )
        return approved

    def is_connected(self) -> bool:
        try:
            creds = self._ensure_access_token()
        except PublishError:
            return False
        try:
            resp = self.session.post(
                USER_INFO_URL,
                headers={"Authorization": f"Bearer {creds.access_token}"},
                json={"fields": ["open_id"]},
                timeout=15,
            )
            self._unwrap_user_info(resp)
            return True
        except PublishError:
            return False
        except requests.RequestException:
            return False

    # -- publish --------------------------------------------------------
    def _require_approved(self, creds: Credentials) -> None:
        if not creds.extra.get("content_posting_approved"):
            raise PublishError(
                "TikTok direct posting is not enabled for this app yet. TikTok "
                "requires your developer app to pass the Content Posting API "
                "audit/app review before the API will accept posts -- until then "
                "the platform rejects them server-side.\n"
                "Apply for review in the TikTok developer portal, then run "
                "`survey-publish connect tiktok` again and answer 'y' to the "
                "approval question. Full steps: docs/SETUP_TIKTOK.md."
            )

    def query_creator_info(self, token: str) -> dict:
        return self._api_post(CREATOR_INFO_URL, token, {}, "TikTok creator info")

    def resolve_privacy_level(self, token: str, requested: str) -> str:
        """Validate the requested privacy level against the account's options."""
        info = self.query_creator_info(token)
        options = info.get("privacy_level_options") or []
        level = (requested or "SELF_ONLY").upper()
        if options and level not in options:
            raise PublishError(
                f"Invalid TikTok privacy_level {level!r} for this account. "
                f"Allowed: {', '.join(options)}. "
                "Pass platform_options={'privacy_level': '<one of these>'}."
            )
        return level

    def chunk_plan(self, size: int) -> "tuple[int, int]":
        """Return ``(chunk_size, total_chunk_count)`` per TikTok's chunk rules."""
        if size <= 0:
            raise PublishError("Video file is empty.")
        if size < 5 * 1024 * 1024:
            return size, 1
        chunk_size = self.chunk_size
        total = math.ceil(size / chunk_size)
        if total > 1000:
            chunk_size = math.ceil(size / 1000)
            total = math.ceil(size / chunk_size)
        return chunk_size, total

    def init_upload(self, token: str, title: str, privacy_level: str, size: int) -> dict:
        chunk_size, total = self.chunk_plan(size)
        data = self._api_post(
            VIDEO_INIT_URL,
            token,
            {
                "post_info": {
                    "title": title[:TITLE_LIMIT],
                    "privacy_level": privacy_level,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total,
                },
            },
            "TikTok video init",
        )
        if "publish_id" not in data or "upload_url" not in data:
            raise PublishError("TikTok video init returned no publish_id/upload_url.")
        return data

    def upload_chunks(self, upload_url: str, path: str, size: int) -> None:
        chunk_size, total = self.chunk_plan(size)
        with open(path, "rb") as fh:
            for index in range(total):
                first = index * chunk_size
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                last = first + len(chunk) - 1
                resp = self.session.put(
                    upload_url,
                    data=chunk,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {first}-{last}/{size}",
                    },
                    timeout=300,
                )
                # 206 = chunk accepted, more to come; 201 = final chunk accepted.
                if resp.status_code not in (200, 201, 206):
                    raise PublishError(api_error_message(resp, "TikTok chunk upload"))

    def wait_for_publish(self, token: str, publish_id: str) -> None:
        """Poll the publish status until TikTok reaches a terminal state."""
        deadline = time.time() + self.poll_timeout_s
        while True:
            data = self._api_post(
                STATUS_FETCH_URL, token, {"publish_id": publish_id}, "TikTok status fetch"
            )
            status = str(data.get("status", "")).upper()
            if status == "PUBLISH_COMPLETE":
                return
            if "FAIL" in status:
                raise PublishError(
                    f"TikTok publishing failed (status {status}). "
                    "Check the video spec in docs/SETUP_TIKTOK.md and retry."
                )
            if time.time() > deadline:
                raise PublishError(
                    f"Timed out waiting for TikTok to finish publishing "
                    f"(publish_id {publish_id}). It may still complete -- check "
                    "the TikTok app before retrying."
                )
            self._sleep(self.poll_interval_s)

    def publish(self, request: PublishRequest) -> PublishResult:
        path = self._require_video(request)
        creds = self._ensure_access_token()
        self._require_approved(creds)
        token = creds.access_token

        requested = str(request.platform_options.get("privacy_level", "SELF_ONLY"))
        privacy_level = self.resolve_privacy_level(token, requested)
        size = os.path.getsize(path)

        init = self.init_upload(token, request.full_caption(), privacy_level, size)
        self.upload_chunks(init["upload_url"], path, size)
        self.wait_for_publish(token, init["publish_id"])
        # The Direct Post API returns a publish_id, not a public URL. A share
        # URL needs the separate Display API (scope video.list), which this
        # engine does not request -- see docs/SETUP_TIKTOK.md.
        return PublishResult.success(platform=self.name, url_or_id=init["publish_id"])
