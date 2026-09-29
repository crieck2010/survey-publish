"""Instagram Graph API adapter (Reels publishing).

Flow (verified against the official Meta docs, Sept 2026):
  1. ``POST /{ig-user-id}/media`` with ``media_type=REELS``, ``video_url``
     (a *public* HTTPS URL Meta's servers fetch), and ``caption``
     -> container id.
  2. Poll ``GET /{container-id}?fields=status_code`` until ``FINISHED``
     (``ERROR``/``EXPIRED`` are terminal failures).
  3. ``POST /{ig-user-id}/media_publish`` with ``creation_id`` -> media id.

Requirements enforced by Meta, not by us:
  * The Instagram account must be a **Business or Creator** account **linked
    to a Facebook Page**. A personal account, or a business account not
    linked to the Page, cannot publish via the API. ``connect()`` checks this
    and raises an actionable error (not a traceback) when it is missing.
  * ``video_url`` must be publicly fetchable over HTTPS. There is no
    "upload a local file" parameter on the ``/media`` edge; the alternative
    is Meta's resumable-upload host (``rupload.facebook.com/ig-api-upload``),
    which this engine does not implement in v0.1.0 -- pass
    ``platform_options={"video_url": "https://..."}`` (see Honest limits).

References:
  https://developers.facebook.com/documentation/instagram-platform/content-publishing
  https://developers.facebook.com/documentation/instagram-platform/content-publishing/resumable-uploads.md
"""
from __future__ import annotations

import time

import requests

from .base import (
    MetaOAuthMixin,
    NotConnectedError,
    PlatformAdapter,
    PublishError,
    api_error_message,
)
from .models import Credentials, PublishRequest, PublishResult
from .store import TokenStore

#: Classic Facebook-Login scopes for the Instagram Graph API. See the
#: uncertainty note on MetaOAuthMixin about Meta's newer Business Login scopes.
IG_SCOPES = (
    "pages_show_list,pages_read_engagement,pages_manage_posts,"
    "instagram_basic,instagram_content_publish"
)

CAPTION_LIMIT = 2200


class InstagramAdapter(MetaOAuthMixin, PlatformAdapter):
    """Publish Reels to an Instagram Business/Creator account."""

    name = "instagram"
    display_name = "Instagram"

    def meta_scopes(self) -> str:
        return IG_SCOPES

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
        ig_user_id, ig_username = self._resolve_instagram_account(page_id, page_token)

        creds = Credentials(
            platform=self.name,
            client_id=client_id,
            client_secret=client_secret,
            access_token=page_token,
            refresh_token="",
            expires_at=time.time() + 60 * 24 * 3600,  # long-lived user token ~60d
            extra={
                "page_id": page_id,
                "page_name": page_name,
                "ig_user_id": ig_user_id,
                "ig_username": ig_username,
                "account_label": "@" + ig_username,
            },
        )
        self.store.save(self.name, creds)
        print(f"\nConnected to Instagram as @{ig_username} (via Page '{page_name}').")

    def _resolve_instagram_account(self, page_id: str, page_token: str) -> "tuple[str, str]":
        """Return ``(ig_user_id, ig_username)`` or raise an actionable error."""
        data = self.meta_get(
            page_id, page_token, {"fields": "instagram_business_account{id,username}"}
        )
        ig = data.get("instagram_business_account")
        if not ig or not ig.get("id"):
            raise PublishError(
                "This Facebook Page has no linked Instagram Business or Creator "
                "account. API publishing requires:\n"
                "  1. an Instagram Business or Creator account (not a personal one), and\n"
                "  2. that account linked to this Facebook Page "
                "(Page Settings -> Linked accounts -> Instagram).\n"
                "Fix that in the Facebook/Instagram apps, then run "
                "`survey-publish connect instagram` again. "
                "See docs/SETUP_INSTAGRAM.md."
            )
        return ig["id"], ig.get("username", ig["id"])

    def _creds(self) -> Credentials:
        creds = self._require_connected()
        if not creds.extra.get("ig_user_id"):
            raise NotConnectedError(
                "Instagram connection is missing its linked account id. "
                + self._not_connected_message()
            )
        return creds

    def is_connected(self) -> bool:
        try:
            creds = self._creds()
        except PublishError:
            return False
        try:
            self.meta_get(
                creds.extra["ig_user_id"], creds.access_token, {"fields": "id,username"}
            )
            return True
        except PublishError:
            return False
        except requests.RequestException:
            return False

    # -- publish --------------------------------------------------------
    def create_container(self, ig_user_id: str, token: str, request: PublishRequest) -> str:
        video_url = request.platform_options.get("video_url", "")
        if not video_url:
            raise PublishError(
                "Instagram publishing needs a public HTTPS URL for the video -- "
                "Meta's servers fetch the file themselves, and the /media edge "
                "accepts no local-file upload.\n"
                "Host the finished MP4 somewhere public (your own web host, a CDN, "
                "or object storage) and pass "
                "platform_options={'video_url': 'https://example.com/reel.mp4'} "
                "(CLI: --opt video_url=https://example.com/reel.mp4).\n"
                "See docs/SETUP_INSTAGRAM.md for the full explanation."
            )
        caption = request.full_caption()[:CAPTION_LIMIT]
        try:
            data = self.meta_post(
                f"{ig_user_id}/media",
                token,
                {
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": caption,
                },
            )
        except PublishError as exc:
            message = str(exc).lower()
            if "not" in message and ("eligible" in message or "permission" in message):
                raise PublishError(
                    "Instagram rejected the publish: the account does not look like "
                    "an eligible Business/Creator account linked to the Page. "
                    "Reconnect after fixing the account type/linkage: "
                    "`survey-publish connect instagram`. See docs/SETUP_INSTAGRAM.md."
                ) from exc
            raise
        container_id = data.get("id")
        if not container_id:
            raise PublishError("Instagram container creation returned no id.")
        return container_id

    def wait_for_container(self, container_id: str, token: str) -> None:
        """Poll until the container is FINISHED (or terminally failed)."""
        deadline = time.time() + self.poll_timeout_s
        while True:
            data = self.meta_get(container_id, token, {"fields": "status_code,status"})
            status_code = (data.get("status_code") or "").upper()
            if status_code == "FINISHED":
                return
            if status_code in ("ERROR", "EXPIRED"):
                detail = data.get("status") or status_code
                raise PublishError(
                    f"Instagram could not process the video (container {status_code}). "
                    f"Meta said: {detail}. Common causes: the video_url is not "
                    "publicly fetchable, or the file is not a supported MP4."
                )
            if time.time() > deadline:
                raise PublishError(
                    f"Timed out waiting for Instagram to process the video "
                    f"(container {container_id}). It may still publish later -- "
                    "check the account before retrying."
                )
            self._sleep(self.poll_interval_s)

    def publish_container(self, ig_user_id: str, token: str, container_id: str) -> str:
        data = self.meta_post(
            f"{ig_user_id}/media_publish", token, {"creation_id": container_id}
        )
        media_id = data.get("id")
        if not media_id:
            raise PublishError("Instagram media_publish returned no media id.")
        return media_id

    def publish(self, request: PublishRequest) -> PublishResult:
        creds = self._creds()  # connection first: actionable error beats file checks
        self._require_video(request)  # sanity: the reel file should exist locally
        ig_user_id = creds.extra["ig_user_id"]
        token = creds.access_token

        container_id = self.create_container(ig_user_id, token, request)
        self.wait_for_container(container_id, token)
        media_id = self.publish_container(ig_user_id, token, container_id)
        try:
            info = self.meta_get(media_id, token, {"fields": "permalink"})
            url_or_id = info.get("permalink") or media_id
        except PublishError:
            url_or_id = media_id
        return PublishResult.success(platform=self.name, url_or_id=url_or_id)
