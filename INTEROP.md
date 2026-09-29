# INTEROP.md — survey-publish embedding contract

How `reel-studio` (Streamlit app) and `survey-schedule` (runner) consume the
`survey-publish` engine. This file is the contract: anything documented here
is stable within a minor version series (0.1.x). Breaking changes bump the
minor (pre-1.0) or major version and are called out in `CHANGELOG.md`.

## Import surface

```python
from publish import __version__                 # "0.2.0"
from publish.models import PublishRequest, PublishResult, Credentials
from publish.registry import get_adapter, list_platforms, PLATFORMS
from publish.store import TokenStore
from publish.base import PlatformAdapter, PublishError, NotConnectedError
# Queue / scheduling (since 0.2.0):
from publish import QueuedItem, QueueStore, parse_schedule_time, DEFAULT_SLOTS, tick
```

Do not import adapter modules directly (`publish.youtube`, etc.) — resolve
them through `get_adapter()` so new platforms appear without code changes.

## Publishing from Python

```python
from publish.registry import get_adapter
from publish.models import PublishRequest

request = PublishRequest(
    video_path="/path/to/reel.mp4",   # finished MP4; never modified by this engine
    title="Gulf Stream this week",
    caption="Weekly ocean-current update.",
    hashtags=["oceans", "surveying"],  # bare tags; rendered as #tag
    platform_options={
        "privacy": "public",            # youtube: public|unlisted|private
        # "video_url": "https://...",   # instagram: REQUIRED public https URL
        # "privacy_level": "SELF_ONLY",  # tiktok: validated vs creator_info
    },
)
adapter = get_adapter("youtube")
result = adapter.publish(request)   # raises PublishError on failure
print(result.url_or_id)
```

Notes for embedders:

- `publish()` **raises** `PublishError` on any failure — it never returns
  `ok=False` directly. Catch `PublishError` (or the `NotConnectedError`
  subclass) and surface `str(exc)`; it is already a human-actionable message.
- `PublishResult.ok == False` only appears when *you* catch an exception and
  record it (see `cli.cmd_publish_all` for the pattern).
- `platform_options` keys are documented per adapter in `docs/API.md`. Unknown
  keys are ignored; missing required keys (e.g. Instagram's `video_url`) raise
  an actionable `PublishError`.
- `get_adapter(name, store=..., session=...)` accepts a custom `TokenStore`
  (e.g. a per-job path) and a custom HTTP session (tests inject fakes).
  `reel-studio` can pass its own store path to isolate credentials per user.
- `adapter.is_connected()` is a cheap validation call; call it before
  rendering a "Publish" button state.
- `adapter.account_label()` returns the connected account's display name
  (`@handle`, channel title, Page name) for UI labels.

## Token storage

Default: `~/.survey-publish/tokens.json`, created `0o600`, repaired to `0o600`
on load. One entry per platform key (`"youtube"`, `"instagram"`, `"facebook"`,
`"tiktok"`). `survey-schedule` running as a service account should point
`TokenStore` at that account's home (or pass an explicit path) so scheduled
jobs use the same tokens the interactive `connect` created.

Token values are never printed or logged by this package — keep it that way
in embedding code (never log `request`, `Credentials`, or adapter internals).

## Versioning policy

- **Semver.** `0.2.0` is the current release (`0.1.0` was the first public release).
- Within a minor series: `PublishRequest`/`PublishResult` fields,
  `get_adapter`/`list_platforms` signatures, the `PlatformAdapter` abstract
  surface, documented `platform_options` keys, and the `QueuedItem` /
  `QueueStore` / `tick()` / `parse_schedule_time()` surface are **stable**.
- New platforms may be added to `PLATFORMS` in a minor release (additive only).
- Adapter-internal helpers (underscore methods) are **not** part of the
  contract and may change.
- Pre-1.0, a minor bump (0.1.0 -> 0.2.0) *may* carry breaking changes; they
  will be listed under `CHANGELOG.md` with a migration note.

## Audio contract (shared with reel-studio)

`survey-publish` publishes the MP4 **exactly as given** — no muxing, no
re-encode, no loudness normalization. Audio is reel-studio's job
(`--audio_path` muxed as AAC upstream). If a silent file arrives here, the
published post is silent. See "Audio policy" in `README.md` and `docs/API.md`.

## What this engine will not do (by design)

No analytics, no comment management, no thumbnail upload, no in-app trending
sounds (impossible via API — documented in `README.md`). Scheduled posting
lives *in* this engine since 0.2.0 (`publish.queue`); `survey-schedule`
orchestrates *what* to schedule, `survey-publish` owns the queue and the
actual posting.

## Queue contract (for reel-studio / survey-schedule, since 0.2.0)

```python
from publish import QueuedItem, QueueStore, parse_schedule_time, tick, DEFAULT_SLOTS
```

- `parse_schedule_time("HH:MM" | "YYYY-MM-DD HH:MM" | ISO-8601)` -> tz-aware
  local datetime. `"HH:MM"` rolls to tomorrow when the slot already passed.
- `QueueStore(path=None)` — queue at `~/.survey-publish/queue.json` (0600);
  pass a path to isolate (tests do this).
- `enqueue(QueuedItem(...)) -> id`. `scheduled_at` must be ISO-8601 *with*
  offset; platforms are normalized lowercase/deduped; status forced `queued`.
- `tick(store, registry=None, now=None) -> {"processed","published","failed"}`.
  Publishes every due item, per-platform failures recorded on the item, never
  raises for one item's failure, no auto-retry.
- The reel-studio UI should surface `DEFAULT_SLOTS` (`["08:30", "12:30",
  "18:30"]`) as preset buttons plus a free-form time field, and
  `queue --status failed` items as "needs attention".
- Honest limits to surface in UI copy: the PC must be on and awake at publish
  time; times are the PC's local timezone; failed items need manual requeue.
