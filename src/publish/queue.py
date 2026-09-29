"""Publish queue: schedule reels for later, publish on a timer.

The queue is a JSON file at ``~/.survey-publish/queue.json`` (0600, atomic
writes, same conventions as :class:`publish.store.TokenStore`). Items are
created with ``survey-publish schedule`` (or :meth:`QueueStore.enqueue` from
Python), listed with ``survey-publish queue``, and published when due by
``survey-publish tick`` -- which is designed to run every 15 minutes from
Windows Task Scheduler. See ``docs/SCHEDULING.md`` for the full picture.

Design rules:
  * stdlib only; no UI imports.
  * ``tick()`` never raises for a single item's failure -- failures are
    recorded on the item (status ``failed``, per-platform errors, successful
    URLs kept) for manual requeue. There are no silent retries.
  * Times are always the PC's local timezone. ``scheduled_at`` is stored as
    ISO-8601 *with* the local UTC offset.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import PublishRequest

DEFAULT_QUEUE_PATH = Path.home() / ".survey-publish" / "queue.json"
_REQUIRED_MODE = 0o600

#: Default daily posting slots (local time). Research-backed defaults --
#: morning commute scroll, midday break, evening wind-down. Users can always
#: schedule any custom time; these are just sensible starting points.
DEFAULT_SLOTS = ["08:30", "12:30", "18:30"]

# Item statuses. "publishing" is transient and only ever set inside tick()
# as a double-publish guard; every other transition is queued -> published,
# queued -> failed, or queued/failed -> canceled.
STATUS_QUEUED = "queued"
STATUS_PUBLISHING = "publishing"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"

_TERMINAL_STATUSES = (STATUS_PUBLISHED, STATUS_FAILED, STATUS_CANCELED)


@dataclass
class QueuedItem:
    """One scheduled reel.

    Attributes:
        id: Unique id (``uuid4().hex``); the first 8 chars are enough on the
            CLI (prefix matching).
        video_path: Local path to the finished MP4. Must exist when the item
            is due, or the publish is recorded as failed.
        title, caption, hashtags: Same semantics as
            :class:`publish.models.PublishRequest`.
        platforms: Registry names, e.g. ``["youtube", "tiktok"]``.
        scheduled_at: ISO-8601 string *with* local UTC offset, e.g.
            ``"2026-09-29T12:30:00-04:00"``.
        status: ``queued`` / ``publishing`` / ``published`` / ``failed`` /
            ``canceled``.
        attempts: How many times ``tick()`` has tried this item.
        last_error: Human-readable failure summary (``""`` when none).
        published_urls: ``{platform: url_or_id}`` for platforms that
            succeeded -- including on partial failure, so a manual requeue
            can skip platforms that already went out.
        platform_options: Per-platform knobs, same semantics as
            ``PublishRequest.platform_options`` (e.g. Instagram's required
            ``video_url``). Applied to the request ``tick()`` builds.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    video_path: str = ""
    title: str = ""
    caption: str = ""
    hashtags: List[str] = field(default_factory=list)
    platforms: List[str] = field(default_factory=list)
    scheduled_at: str = ""
    status: str = STATUS_QUEUED
    attempts: int = 0
    last_error: str = ""
    published_urls: Dict[str, str] = field(default_factory=dict)
    platform_options: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "QueuedItem":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        item = cls()
        for key, value in clean.items():
            setattr(item, key, value)
        if not item.id:
            item.id = uuid.uuid4().hex
        return item

    @property
    def scheduled_dt(self) -> Optional[datetime]:
        """``scheduled_at`` as a tz-aware datetime (naive assumed local)."""
        if not self.scheduled_at:
            return None
        try:
            dt = datetime.fromisoformat(self.scheduled_at)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_local_tz())
        return dt


def _local_tz():
    """The PC's local timezone (DST handled by the OS)."""
    return datetime.now().astimezone().tzinfo


def parse_schedule_time(s: str, now: Optional[datetime] = None) -> datetime:
    """Parse a schedule time into a tz-aware local datetime.

    Accepted formats:
      * ``"HH:MM"`` -- next occurrence in local time. If that time today has
        already passed (or is right now), it rolls to tomorrow.
      * ``"YYYY-MM-DD HH:MM"`` -- that calendar day and time, local.
      * Full ISO-8601 -- ``"2026-09-29T12:30:00-04:00"``. A naive value
        (no offset) is assumed to be local time.

    Raises:
        ValueError: with an actionable message when the input cannot be
            parsed.
    """
    text = (s or "").strip()
    if not text:
        raise ValueError(
            "Empty schedule time. Use 'HH:MM' (e.g. '12:30'), "
            "'YYYY-MM-DD HH:MM' (e.g. '2026-09-29 12:30'), or full ISO-8601 "
            "(e.g. '2026-09-29T12:30:00-04:00')."
        )
    now = now or datetime.now().astimezone()
    local = now.tzinfo or _local_tz()

    # "HH:MM" -> next occurrence in local time.
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if hour > 23 or minute > 59:
            raise ValueError(
                f"Bad time {text!r}: hour must be 0-23 and minute 0-59 "
                "(e.g. '08:30')."
            )
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            # That slot today already passed (or is right now) -> tomorrow.
            candidate += timedelta(days=1)
        return candidate

    # ISO-8601, with or without offset (naive assumed local).
    iso = text
    if iso[-1:] in ("Z", "z"):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        dt = None
    if dt is None:
        raise ValueError(
            f"Could not parse schedule time {text!r}. Accepted formats: "
            "'HH:MM' (e.g. '12:30'), 'YYYY-MM-DD HH:MM' "
            "(e.g. '2026-09-29 12:30'), or full ISO-8601 "
            "(e.g. '2026-09-29T12:30:00-04:00')."
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local)
    return dt


def _normalize_platforms(platforms) -> List[str]:
    cleaned: List[str] = []
    for p in platforms or []:
        key = (p or "").strip().lower()
        if key and key not in cleaned:
            cleaned.append(key)
    if not cleaned:
        raise ValueError(
            "At least one platform is required (e.g. platforms=['youtube', 'tiktok'])."
        )
    return cleaned


def _normalize_scheduled_at(scheduled_at: str) -> str:
    """Validate and normalize to ISO-8601 with a UTC offset."""
    text = (scheduled_at or "").strip()
    if not text:
        raise ValueError("scheduled_at is required (ISO-8601 with local offset).")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"Bad scheduled_at {text!r}: expected ISO-8601, e.g. "
            "'2026-09-29T12:30:00-04:00'. Use parse_schedule_time() to build one."
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tz())
    return dt.isoformat()


class QueueStore:
    """JSON-backed schedule queue.

    File layout: ``{"version": 1, "items": {id: {...}}}``. Writes are atomic
    (temp file in the same directory + ``os.replace``) and the file is
    created/repaired to ``0o600``. A corrupt file is quarantined to
    ``queue.json.corrupt-<epoch>`` and the queue starts empty rather than
    crashing -- the quarantine path is returned by nothing, it is just left
    next to the queue file for inspection.
    """

    def __init__(self, path: "str | os.PathLike | None" = None):
        self.path = Path(path) if path is not None else DEFAULT_QUEUE_PATH

    # -- internal ---------------------------------------------------------
    def _read_all(self) -> dict:
        if not self.path.exists():
            return {}
        self._ensure_permissions()
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError:
            self._quarantine_corrupt()
            return {}
        except OSError:
            return {}
        if not isinstance(data, dict):
            return {}
        items = data.get("items", {})
        return items if isinstance(items, dict) else {}

    def _quarantine_corrupt(self) -> None:
        backup = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
        try:
            os.replace(str(self.path), str(backup))
        except OSError:
            pass

    def _ensure_permissions(self) -> None:
        try:
            mode = stat.S_IMODE(os.stat(self.path).st_mode)
        except OSError:
            return
        if mode != _REQUIRED_MODE:
            os.chmod(self.path, _REQUIRED_MODE)

    def _write_all(self, items: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _REQUIRED_MODE)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "items": items}, fh, indent=2, sort_keys=True)
                fh.write("\n")
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(str(tmp), str(self.path))
        os.chmod(self.path, _REQUIRED_MODE)

    def _save_item(self, item: QueuedItem) -> None:
        items = self._read_all()
        items[item.id] = item.to_dict()
        self._write_all(items)

    # -- public -----------------------------------------------------------
    def enqueue(self, item: QueuedItem) -> str:
        """Add ``item`` to the queue. Returns the item id.

        The item is normalized (platforms lowercased/deduped, ``scheduled_at``
        forced to ISO-8601 with offset, status forced to ``queued``); the
        caller's object is left untouched.

        Raises:
            ValueError: on missing video_path/title/platforms or a bad
                ``scheduled_at``.
        """
        if not (item.video_path or "").strip():
            raise ValueError("video_path is required to enqueue an item.")
        if not (item.title or "").strip():
            raise ValueError("title is required to enqueue an item.")
        stored = QueuedItem(
            id=item.id or uuid.uuid4().hex,
            video_path=item.video_path,
            title=item.title,
            caption=item.caption or "",
            hashtags=list(item.hashtags or []),
            platforms=_normalize_platforms(item.platforms),
            scheduled_at=_normalize_scheduled_at(item.scheduled_at),
            status=STATUS_QUEUED,
            platform_options=dict(item.platform_options or {}),
        )
        self._save_item(stored)
        return stored.id

    def get(self, item_id: str) -> Optional[QueuedItem]:
        """Return the item with ``item_id``, or ``None``."""
        items = self._read_all()
        data = items.get(item_id or "")
        return QueuedItem.from_dict(data) if data is not None else None

    def list_queue(self, status_filter: Optional[str] = None) -> List[QueuedItem]:
        """All items, oldest scheduled first.

        ``status_filter``: a status name (``"queued"``, ``"published"``, ...),
        or ``None``/``""``/``"all"`` for everything.
        """
        items = [QueuedItem.from_dict(d) for d in self._read_all().values()]
        if status_filter and status_filter != "all":
            items = [i for i in items if i.status == status_filter]
        items.sort(
            key=lambda i: (i.scheduled_dt or datetime.max.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        )
        return items

    def due_items(self, now: Optional[datetime] = None) -> List[QueuedItem]:
        """Queued items whose ``scheduled_at`` is at or before ``now``.

        ``now`` defaults to the current local time. Sorted oldest-first.
        """
        now = now or datetime.now().astimezone()
        due = []
        for item in self.list_queue(status_filter=STATUS_QUEUED):
            dt = item.scheduled_dt
            if dt is not None and dt <= now:
                due.append(item)
        return due

    def cancel(self, item_id: str) -> None:
        """Cancel a ``queued`` (or ``failed``) item.

        Already-canceled is a no-op (idempotent). Raises ``ValueError`` when
        the id is unknown or the item is already published / in flight.
        """
        item = self.get(item_id)
        if item is None:
            raise ValueError(f"No queued item with id {item_id!r}.")
        if item.status == STATUS_CANCELED:
            return
        if item.status not in (STATUS_QUEUED, STATUS_FAILED):
            raise ValueError(
                f"Cannot cancel item {item.id[:8]}: status is {item.status!r} "
                "(only queued or failed items can be canceled)."
            )
        item.status = STATUS_CANCELED
        self._save_item(item)

    def reschedule(self, item_id: str, new_iso: str) -> None:
        """Move a ``queued`` item to a new ISO-8601 time (with offset).

        Raises ``ValueError`` when the id is unknown, the time is bad, or the
        item is not ``queued`` (failed items are requeued via cancel +
        ``schedule``).
        """
        item = self.get(item_id)
        if item is None:
            raise ValueError(f"No queued item with id {item_id!r}.")
        if item.status != STATUS_QUEUED:
            raise ValueError(
                f"Cannot reschedule item {item.id[:8]}: status is {item.status!r} "
                "(only queued items can be rescheduled; "
                "cancel + schedule again to requeue a failed item)."
            )
        item.scheduled_at = _normalize_scheduled_at(new_iso)
        self._save_item(item)

    def mark_published(self, item_id: str, urls: Dict[str, str]) -> None:
        """Record a fully successful publish: status ``published``."""
        item = self.get(item_id)
        if item is None:
            raise ValueError(f"No queued item with id {item_id!r}.")
        item.status = STATUS_PUBLISHED
        item.published_urls = dict(urls or {})
        item.last_error = ""
        self._save_item(item)

    def mark_failed(self, item_id: str, error: str) -> None:
        """Record a failed publish: status ``failed``, attempts incremented.

        URLs of platforms that *did* succeed should already be in
        ``published_urls`` (tick sets them before calling this); they are
        kept so a manual requeue can skip platforms that already went out.
        """
        item = self.get(item_id)
        if item is None:
            raise ValueError(f"No queued item with id {item_id!r}.")
        item.status = STATUS_FAILED
        item.attempts = (item.attempts or 0) + 1
        item.last_error = (error or "")[:1000]
        self._save_item(item)

    def _set_status(self, item_id: str, status: str) -> None:
        """Internal: set status without other side effects (tick's guard)."""
        item = self.get(item_id)
        if item is None:
            raise ValueError(f"No queued item with id {item_id!r}.")
        item.status = status
        self._save_item(item)


def _resolve_get_adapter(registry):
    """Return a ``get_adapter(name)`` callable.

    ``registry`` may be ``None`` (the real ``publish.registry``), a module or
    namespace exposing ``get_adapter``, or a plain ``get_adapter`` callable.
    """
    if registry is None:
        from .registry import get_adapter

        return get_adapter
    if hasattr(registry, "get_adapter"):
        return registry.get_adapter
    if callable(registry):
        return registry
    raise TypeError(
        "registry must be None, a module with get_adapter(), or a callable."
    )


def tick(
    store: QueueStore,
    registry=None,
    now: Optional[datetime] = None,
) -> dict:
    """Publish every due queued item.

    For each due item (re-read inside the loop so a concurrent ``tick`` can't
    double-publish): flip the status to the transient ``"publishing"``, build
    a :class:`PublishRequest`, publish to each requested platform via
    ``get_adapter(name).publish(request)`` catching exceptions per platform,
    then ``mark_published`` (all platforms ok) or ``mark_failed`` (any
    failure -- no auto-retry; failed items stay visible for manual requeue).

    Never raises for a single item's failure. Returns a summary dict
    ``{"processed": n, "published": n, "failed": n}``.
    """
    get_adapter = _resolve_get_adapter(registry)
    now = now or datetime.now().astimezone()
    summary = {"processed": 0, "published": 0, "failed": 0}

    for queued in store.due_items(now):
        # Re-read: another tick may have finalized this item already.
        item = store.get(queued.id)
        if item is None or item.status != STATUS_QUEUED:
            continue
        summary["processed"] += 1
        store._set_status(item.id, STATUS_PUBLISHING)
        try:
            request = PublishRequest(
                video_path=item.video_path,
                title=item.title,
                caption=item.caption,
                hashtags=list(item.hashtags),
                platform_options=dict(item.platform_options),
            )
            per_platform: Dict[str, dict] = {}
            for platform in item.platforms:
                try:
                    adapter = get_adapter(platform)
                    result = adapter.publish(request)
                    per_platform[platform] = {
                        "ok": True,
                        "url": result.url_or_id or "",
                    }
                except Exception as exc:  # per-platform failure must not abort
                    per_platform[platform] = {"ok": False, "error": str(exc)}
            urls = {
                p: r["url"] for p, r in per_platform.items() if r["ok"]
            }
            errors = {
                p: r["error"] for p, r in per_platform.items() if not r["ok"]
            }
            # Keep successful URLs even on partial failure so a manual
            # requeue can skip platforms that already went out.
            current = store.get(item.id)
            if current is not None:
                current.published_urls = urls
                store._save_item(current)
            if errors:
                summary["failed"] += 1
                detail = "; ".join(f"{p}: {e}" for p, e in errors.items())
                store.mark_failed(item.id, detail)
            else:
                summary["published"] += 1
                store.mark_published(item.id, urls)
        except Exception as exc:  # belt and suspenders: never abort the tick
            summary["failed"] += 1
            try:
                store.mark_failed(item.id, f"tick error: {exc}")
            except Exception:
                pass
    return summary
