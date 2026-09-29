"""survey-publish: deterministic multi-platform publishing engine.

Upload finished MP4 reels to YouTube, Instagram, Facebook, and TikTok from
Python or the ``survey-publish`` CLI. Pure engine: stdlib + ``requests`` only,
no UI framework imports anywhere under ``src/``.

Public surface (stable within minor versions; see INTEROP.md):
    from publish.registry import get_adapter, list_platforms
    from publish.models import PublishRequest, PublishResult, Credentials
"""

__version__ = "0.1.0"

from .base import NotConnectedError, PlatformAdapter, PublishError
from .models import Credentials, PublishRequest, PublishResult
from .registry import PLATFORMS, UnknownPlatformError, get_adapter, list_platforms
from .store import TokenStore

__all__ = [
    "__version__",
    "Credentials",
    "NotConnectedError",
    "PLATFORMS",
    "PlatformAdapter",
    "PublishError",
    "PublishRequest",
    "PublishResult",
    "TokenStore",
    "UnknownPlatformError",
    "get_adapter",
    "list_platforms",
]
