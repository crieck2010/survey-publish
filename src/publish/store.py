"""Local token storage.

Tokens live at ``~/.survey-publish/tokens.json`` (override by passing an
explicit path -- the tests do this). The file is created with ``0o600``
permissions and permissions are repaired to ``0o600`` on load if they drift.

Token values are never printed or logged by this module or any adapter.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .models import Credentials

DEFAULT_TOKEN_PATH = Path.home() / ".survey-publish" / "tokens.json"
_REQUIRED_MODE = 0o600


class TokenStore:
    """JSON-backed per-platform credential store."""

    def __init__(self, path: "str | os.PathLike | None" = None):
        self.path = Path(path) if path is not None else DEFAULT_TOKEN_PATH

    # -- internal ---------------------------------------------------------
    def _read_all(self) -> dict:
        if not self.path.exists():
            return {}
        self._ensure_permissions()
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _ensure_permissions(self) -> None:
        try:
            mode = stat.S_IMODE(os.stat(self.path).st_mode)
        except OSError:
            return
        if mode != _REQUIRED_MODE:
            os.chmod(self.path, _REQUIRED_MODE)

    def _write_all(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write via os.open so the file is created with 0o600 from the start.
        fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _REQUIRED_MODE)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.chmod(self.path, _REQUIRED_MODE)

    # -- public -----------------------------------------------------------
    def save(self, platform: str, credentials: Credentials) -> None:
        """Persist credentials for ``platform`` (creates the file 0o600)."""
        data = self._read_all()
        data[platform] = credentials.to_dict()
        self._write_all(data)

    def load(self, platform: str) -> "Credentials | None":
        """Return stored credentials, or ``None`` when never connected."""
        data = self._read_all()
        if platform not in data:
            return None
        return Credentials.from_dict(platform, data[platform])

    def delete(self, platform: str) -> bool:
        """Delete stored credentials. Returns True if anything was removed."""
        data = self._read_all()
        if platform not in data:
            return False
        del data[platform]
        self._write_all(data)
        return True

    def platforms(self) -> list:
        """Names of platforms with stored credentials."""
        return sorted(self._read_all().keys())
