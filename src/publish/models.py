"""Data models for survey-publish.

These are plain dataclasses with no network or UI dependencies so they can be
freely constructed by upstream tools (reel-studio, survey-schedule).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PublishRequest:
    """Everything an adapter needs to publish one video.

    Attributes:
        video_path: Local path to the finished MP4. survey-publish never
            modifies it (see AUDIO POLICY in the README).
        title: Platform title / post title.
        caption: Body text. Hashtags are appended automatically; do not
            include them here unless you want duplicates.
        hashtags: Tags *without* the leading ``#`` (``"surveying"`` not
            ``"#surveying"``). Rendered as ``#tag`` in captions and, on
            YouTube, as native video tags.
        platform_options: Adapter-specific knobs, e.g.
            ``{"privacy": "unlisted"}`` (YouTube),
            ``{"video_url": "https://..."}`` (Instagram),
            ``{"privacy_level": "SELF_ONLY"}`` (TikTok).
            See docs/API.md for the full per-platform list.
    """

    video_path: str
    title: str
    caption: str = ""
    hashtags: List[str] = field(default_factory=list)
    platform_options: dict = field(default_factory=dict)

    def tag_list(self) -> List[str]:
        """Hashtags normalized to bare tags (no ``#``, no blanks)."""
        return [h.lstrip("#").strip() for h in self.hashtags if h and h.strip()]

    def hashtag_string(self) -> str:
        """``#a #b #c`` string, or ``""`` when there are no hashtags."""
        return " ".join("#" + t for t in self.tag_list())

    def full_caption(self) -> str:
        """Caption with hashtags appended on a blank line, as platforms expect."""
        text = (self.caption or "").strip()
        tags = self.hashtag_string()
        if not tags:
            return text
        if not text:
            return tags
        return f"{text}\n\n{tags}"


@dataclass
class PublishResult:
    """Outcome of one publish call.

    ``publish()`` raises :class:`publish.base.PublishError` on any failure, so
    a returned result always has ``ok=True``. The ``ok=False`` shape exists for
    aggregators such as ``publish-all``, which catch per-platform exceptions
    and record them here instead of aborting the whole run.
    """

    platform: str
    ok: bool
    url_or_id: Optional[str] = None
    error: Optional[str] = None

    @classmethod
    def success(cls, platform: str, url_or_id: Optional[str] = None) -> "PublishResult":
        return cls(platform=platform, ok=True, url_or_id=url_or_id)

    @classmethod
    def failure(cls, platform: str, error: str) -> "PublishResult":
        return cls(platform=platform, ok=False, error=error)


@dataclass
class Credentials:
    """Stored OAuth credentials for one platform.

    ``client_id`` holds the app's client id (TikTok calls this the client key;
    the mapping is documented in tiktok.py). ``extra`` carries platform
    specifics such as page ids, account labels, and approval flags.
    Token values are never printed or logged anywhere in this package.
    """

    platform: str
    client_id: str = ""
    client_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, platform: str, data: dict) -> "Credentials":
        return cls(
            platform=platform,
            client_id=data.get("client_id", ""),
            client_secret=data.get("client_secret", ""),
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            expires_at=data.get("expires_at", 0.0) or 0.0,
            extra=data.get("extra", {}) or {},
        )
