# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-28

### Added
- Publish queue: schedule reels for later, publish on a timer
  (`src/publish/queue.py`, stdlib only).
- `QueuedItem` dataclass: id (uuid4 hex), video_path, title, caption,
  hashtags, platforms, scheduled_at (ISO-8601 with local UTC offset),
  status (`queued`/`publishing`/`published`/`failed`/`canceled`), attempts,
  last_error, published_urls (platform -> url/id), platform_options.
- `QueueStore`: JSON queue at `~/.survey-publish/queue.json`, created and
  repaired to `0o600`, atomic writes (temp file + `os.replace`); corrupt
  files are quarantined to `queue.json.corrupt-<epoch>` instead of crashing.
  API: `enqueue` / `get` / `list_queue` (oldest-first, optional status
  filter) / `due_items` / `cancel` / `reschedule` / `mark_published` /
  `mark_failed` (attempts incremented).
- `tick(store, registry=None, now=None)`: publishes every due queued item,
  one `PublishRequest` per item, per-platform exceptions caught and recorded;
  transient `publishing` status re-read inside the loop so overlapping ticks
  can't double-publish. Never raises for a single item's failure; returns
  `{"processed", "published", "failed"}`. No auto-retry — failures stay
  visible for manual requeue, with successful platforms' URLs kept in
  `published_urls` so a requeue can skip them.
- `parse_schedule_time()`: `"HH:MM"` (next occurrence local, rolls to
  tomorrow if passed), `"YYYY-MM-DD HH:MM"`, full ISO-8601 (naive assumed
  local); `ValueError` with actionable guidance on bad input.
- `DEFAULT_SLOTS = ["08:30", "12:30", "18:30"]`: research-backed default
  posting slots (morning/midday/evening scroll windows); custom times always
  allowed.
- CLI: `schedule` (`--video/--title/--caption/--hashtags/--platforms/--at`,
  optional `--date`, repeatable `--opt`), `queue [--status]`,
  `queue-cancel <id>` (also `queue cancel <id>`), `queue-reschedule <id>
  --at`, `tick` (quiet when nothing due; exit 0 unless catastrophic —
  designed for Windows Task Scheduler every 15 min).
- Docs: `docs/SCHEDULING.md` (queue model, time formats, `schtasks` example,
  honest limits), README scheduling section, API reference section, and an
  INTEROP.md embedding-contract section for the queue.
- Tests: 51 new tests in `tests/test_queue.py` (store round-trip, due logic,
  timezone edge cases, tick success/partial-failure/double-publish guard,
  CLI parsing and end-to-end); `tests/fakes.py` gained `FakePublisher` and
  `make_fake_registry` (existing fakes untouched).

### Changed
- README honest-limits: scheduling is no longer "not in scope" — the
  scheduling limit is now "the PC must be on and awake at publish time".

## [0.1.0] - 2026-09-28

### Added
- Initial public release of the `survey-publish` publishing engine.
- `PublishRequest` / `PublishResult` / `Credentials` data models (`src/publish/models.py`).
- `PlatformAdapter` ABC with shared OAuth installed-app flow, actionable error
  types (`PublishError`, `NotConnectedError`), and injectable HTTP sessions for
  testing (`src/publish/base.py`).
- `TokenStore`: tokens at `~/.survey-publish/tokens.json`, file created and
  repaired to `0o600`; token values never printed or logged (`src/publish/store.py`).
- Platform registry: `PLATFORMS`, `get_adapter()`, `list_platforms()` with
  `UnknownPlatformError` listing valid names (`src/publish/registry.py`).
- YouTube adapter: Data API v3 resumable upload (initiate + chunked PUT with
  308-resume), OAuth with offline refresh, channel verification on connect
  (`src/publish/youtube.py`).
- Instagram adapter: Graph API Reels two-step flow (container -> poll to
  FINISHED -> `media_publish` -> permalink), Business/Creator + Page-link
  eligibility check on connect (`src/publish/instagram.py`).
- Facebook adapter: Graph API Page video upload via multipart `POST` to
  `graph-video.facebook.com` with Page-token auth (`src/publish/facebook.py`).
- TikTok adapter: Content Posting API Direct Post (`FILE_UPLOAD`: init ->
  chunked PUT with `Content-Range` -> status poll to `PUBLISH_COMPLETE`),
  creator-info privacy-level validation, and an app-review gate that makes
  `publish()` refuse until the TikTok app passes audit (`src/publish/tiktok.py`).
- CLI `survey-publish`: `connect`, `disconnect`, `status`, `publish`,
  `publish-all` (per-platform failure aggregation, `--opt KEY=VALUE` for
  platform options) (`src/publish/cli.py`).
- Docs: `README.md`, `INTEROP.md`, `docs/API.md`, and per-platform setup
  guides `docs/SETUP_{YOUTUBE,INSTAGRAM,FACEBOOK,TIKTOK}.md`, including the
  audio policy (no muxing here; no in-app trending sounds via API).
- Test suite: 62 tests, all HTTP stubbed with hand-rolled fakes (no network,
  no credentials).
