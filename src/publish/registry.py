"""Platform registry.

Maps platform names to adapter classes so callers (CLI, reel-studio,
survey-schedule) resolve adapters by string without importing each module.
"""
from __future__ import annotations

from .facebook import FacebookAdapter
from .instagram import InstagramAdapter
from .tiktok import TikTokAdapter
from .youtube import YouTubeAdapter

PLATFORMS = {
    "youtube": YouTubeAdapter,
    "instagram": InstagramAdapter,
    "facebook": FacebookAdapter,
    "tiktok": TikTokAdapter,
}
"""Registry of supported platforms: name -> adapter class."""


class UnknownPlatformError(ValueError):
    """Raised when a platform name is not in the registry."""


def list_platforms() -> list:
    """Sorted list of supported platform names."""
    return sorted(PLATFORMS)


def get_adapter(name: str, store=None, session=None):
    """Return an adapter instance for ``name``.

    ``store`` and ``session`` are passed through to the adapter constructor
    (useful for tests and for embedding in other apps).

    Raises:
        UnknownPlatformError: if ``name`` is not a registered platform. The
            message lists every valid name.
    """
    key = (name or "").strip().lower()
    if key not in PLATFORMS:
        valid = ", ".join(list_platforms())
        raise UnknownPlatformError(
            f"Unknown platform {name!r}. Valid platforms: {valid}."
        )
    return PLATFORMS[key](store=store, session=session)
