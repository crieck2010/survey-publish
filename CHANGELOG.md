# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
