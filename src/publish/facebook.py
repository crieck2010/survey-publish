"""Facebook Graph API adapter (Page video publishing).

Upload path (verified against the official Meta docs, Sept 2026):
  * The current Video API publishing guide recommends uploading the file via
    Meta's Resumable Upload API and then publishing with the returned handle:
    ``POST https://graph-video.facebook.com/v{version}/{page-id}/videos``
    with ``access_token``, ``title``, ``description``, and
    ``fbuploader_video_file_chunk=<handle>``.
  * For reels-sized files this engine uses the simpler, long-documented
    direct form of the same edge: a multipart ``POST`` with the ``source``
    file field, ``title``, ``description``, and the Page access token. Meta
    documents a 1 GB / 20-minute cap for multipart-or-URL uploads; larger
    files need the resumable-upload API (documented honest limit, v0.1.0).

Requirements: a Page access token for a Page the user can perform the
``CREATE_CONTENT`` task on, with ``pages_show_list``, ``pages_read_engagement``,
and ``pages_manage_posts`` granted via Facebook Login.

References:
  https://developers.facebook.com/documentation/video-api/guides/publishing
  https://developers.facebook.com/docs/graph-api/reference/page/videos/
"""
from __future__ import annotations

import os

import requests

from .base import (
    MetaOAuthMixin,
    PlatformAdapter,
    PublishError,
    api_error_message,
)
from .models import Credentials, PublishRequest, PublishResult
from .store import TokenStore

FB_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts"

#: 1 GB -- Meta's documented cap for multipart (non-resumable) video uploads.
MULTIPART_SIZE_LIMIT = 1024 * 1024 * 1024


class FacebookAdapter(MetaOAuthMixin, PlatformAdapter):
    """Publish videos to a Facebook Page."""

    name = "facebook"
    display_name = "Facebook"

    def meta_scopes(self) -> str:
        return FB_SCOPES

    # -- OAuth ----------------------------------------------------------
    def connect(self) -> None:
        client_id, client_secret = self._prompt_client_credentials(
            "SURVEY_PUBLISH_META", "Meta app ID", "Meta app secret"
        )
        short = self._installed_app_flow(
            self.build_meta_authorize_url(client_id),
            lambda code: self.exchange_meta_code(code, client_id, client_secret),
            "After authorizing, your browser will show a connection error for "
            "https://localhost/ -- that is expected. Copy the FULL address from "
            "the browser address bar (it contains code=...) and paste it below.",
        )
        long_lived = self.exchange_for_long_lived(
            short["access_token"], client_id, client_secret
        )
        user_token = long_lived.get("access_token", short["access_token"])
        page_id, page_name, page_token = self.pick_page(user_token)
        # Verify the token works against the Page.
        self.meta_get(page_id, page_token, {"fields": "id,name"})

        creds = Credentials(
            platform=self.name,
            client_id=client_id,
            client_secret=client_secret,
            access_token=page_token,
            refresh_token="",
            expires_at=0.0,  # Page tokens from a long-lived user token are long-lived
            extra={
                "page_id": page_id,
                "page_name": page_name,
                "account_label": page_name,
            },
        )
        self.store.save(self.name, creds)
        print(f"\nConnected to Facebook Page '{page_name}'.")

    def _creds(self) -> Credentials:
        creds = self._require_connected()
        if not creds.extra.get("page_id"):
            raise PublishError(
                "Facebook connection is missing its Page id. "
                + self._not_connected_message()
            )
        return creds

    def is_connected(self) -> bool:
        try:
            creds = self._creds()
        except PublishError:
            return False
        try:
            self.meta_get("me", creds.access_token, {"fields": "id"})
            return True
        except PublishError:
            return False
        except requests.RequestException:
            return False

    # -- publish --------------------------------------------------------
    def publish(self, request: PublishRequest) -> PublishResult:
        path = self._require_video(request)
        creds = self._creds()
        page_id = creds.extra["page_id"]
        token = creds.access_token

        size = os.path.getsize(path)
        if size > MULTIPART_SIZE_LIMIT:
            raise PublishError(
                f"Video is {size / 1e9:.2f} GB; multipart uploads are capped at "
                "1 GB by Meta. Use Meta's resumable-upload API for larger files "
                "(see docs/SETUP_FACEBOOK.md), or compress the reel first."
            )

        description = request.full_caption()
        with open(path, "rb") as fh:
            files = {"source": (os.path.basename(path), fh, "video/mp4")}
            data = {
                "access_token": token,
                "title": request.title[:255],
                "description": description,
            }
            resp = self.session.post(
                f"https://graph-video.facebook.com/v{self.meta_graph_version}"
                f"/{page_id}/videos",
                data=data,
                files=files,
                timeout=600,
            )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "Facebook video upload"))
        body = resp.json()
        if isinstance(body, dict) and "error" in body:
            raise PublishError(api_error_message(resp, "Facebook video upload"))
        video_id = body.get("id")
        if not video_id:
            raise PublishError("Facebook video upload returned no video id.")
        try:
            info = self.meta_get(video_id, token, {"fields": "permalink_url"})
            url_or_id = info.get("permalink_url") or video_id
        except PublishError:
            url_or_id = video_id
        return PublishResult.success(platform=self.name, url_or_id=url_or_id)
