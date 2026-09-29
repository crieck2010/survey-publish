# API reference — survey-publish 0.1.0

Python API for embedding the publishing engine (reel-studio, survey-schedule).
Everything below is importable from the `publish` package. Stdlib + `requests`
only — no UI framework imports anywhere under `src/`.

## Quick example

```python
from publish.registry import get_adapter
from publish.models import PublishRequest
from publish.base import PublishError

request = PublishRequest(
    video_path="reel.mp4",
    title="Gulf Stream this week",
    caption="Weekly ocean-current update from satellite data.",
    hashtags=["oceans", "surveying"],      # bare tags; rendered as #tag
    platform_options={"privacy": "public"},
)
try:
    result = get_adapter("youtube").publish(request)
except PublishError as exc:
    print("failed:", exc)                  # already human-actionable
else:
    print("published:", result.url_or_id)
```

## Models (`publish.models`)

### `PublishRequest`

| Field | Type | Meaning |
|---|---|---|
| `video_path` | `str` | Local path to the finished MP4. Published as-is; never modified. |
| `title` | `str` | Platform title / post title. |
| `caption` | `str` | Body text. Hashtags are appended automatically — do not duplicate them here. |
| `hashtags` | `list[str]` | Bare tags (`"surveying"`, not `"#surveying"`). |
| `platform_options` | `dict` | Per-platform knobs (see table below). |

Helpers: `tag_list()` (normalized bare tags), `hashtag_string()` (`"#a #b"`),
`full_caption()` (caption + blank line + hashtags).

### `PublishResult`

| Field | Type | Meaning |
|---|---|---|
| `platform` | `str` | Registry key (`"youtube"`, …). |
| `ok` | `bool` | `True` on success. `publish()` raises instead of returning `ok=False`. |
| `url_or_id` | `str \| None` | Watch URL / permalink / media id / publish id, per platform. |
| `error` | `str \| None` | Set only on aggregator-recorded failures (e.g. `publish-all`). |

`PublishResult.success(platform, url_or_id)` / `.failure(platform, error)`
constructors are provided for aggregators.

### `Credentials`

`platform, client_id, client_secret, access_token, refresh_token, expires_at,
extra: dict`. `extra` carries platform specifics (page ids, account labels,
approval flags). Never log instances — token values are never printed by this
package.

## Registry (`publish.registry`)

```python
from publish.registry import get_adapter, list_platforms, PLATFORMS

list_platforms()            # ['facebook', 'instagram', 'tiktok', 'youtube']
adapter = get_adapter("tiktok")
adapter = get_adapter("tiktok", store=my_store, session=my_session)
```

- `get_adapter(name, store=None, session=None)` — case-insensitive; raises
  `UnknownPlatformError` (a `ValueError`) listing every valid name.
- `store`: a `publish.store.TokenStore` (defaults to
  `~/.survey-publish/tokens.json`). Pass a custom path to isolate credentials.
- `session`: a `requests.Session`-like; inject fakes in tests.

## Adapters (`publish.base.PlatformAdapter`)

```python
adapter.name            # "youtube" (registry key)
adapter.display_name    # "YouTube" (human label)
adapter.is_connected()  # bool -- validates stored creds against the API
adapter.connect()       # interactive OAuth installed-app flow; stores tokens
adapter.disconnect()    # deletes stored tokens
adapter.account_label() # "@handle" / channel title / Page name for UIs
result = adapter.publish(request)   # PublishResult or raises PublishError
```

Error model: `PublishError` for everything (API rejections, missing files,
bad options) with a one-line actionable message; `NotConnectedError`
(subclass) when tokens are missing, naming the exact
``survey-publish connect <platform>`` command and setup doc. Embedders should
catch `PublishError` and display `str(exc)` — never a traceback.

## `platform_options` per adapter

| Platform | Key | Values / meaning | Default |
|---|---|---|---|
| youtube | `privacy` | `public` / `unlisted` / `private` | `public` |
| youtube | `category_id` | YouTube category id | `"28"` (Science & Technology) |
| instagram | `video_url` | **Required.** Public HTTPS URL of the MP4; Meta's servers fetch it | — |
| facebook | — | (none in v0.1.0) | — |
| tiktok | `privacy_level` | Validated vs `creator_info/query` options | `"SELF_ONLY"` |

Unknown keys are ignored. Missing required keys raise `PublishError` with
instructions.

## `publish-all` pattern (for embedders)

```python
from publish.base import PublishError
from publish.models import PublishResult
from publish.registry import list_platforms, get_adapter

results = []
for name in list_platforms():
    adapter = get_adapter(name, store=my_store)
    try:
        if not adapter.is_connected():
            raise PublishError(adapter._not_connected_message())
        results.append(adapter.publish(request))
    except PublishError as exc:
        results.append(PublishResult.failure(name, str(exc)))
```

(This is exactly what `cli.cmd_publish_all` does.)

## Audio policy

**This engine never touches audio.** It publishes the MP4 byte-for-byte as
given. Audio is muxed upstream — `reel-studio` / `survey-animate` mux
`--audio_path` as AAC before the file reaches this engine. A silent file in
means a silent post out; that is the upstream tool's decision.

Platform reality for embedders to surface in UI copy:

- API uploads **cannot** attach TikTok/Instagram trending in-app sounds —
  those exist only inside the TikTok/Instagram apps and are added by editing
  in-app.
- Uploading copyrighted music risks muting, takedowns, or Content ID claims.
  Use audio the user owns or royalty-free tracks.

## Polling knobs

Adapters that poll async platform processing expose `poll_interval_s`
(default 10) and `poll_timeout_s` (default 600). Lower them in tests; leave
them alone in production.

## What `url_or_id` holds per platform

| Platform | `url_or_id` on success |
|---|---|
| youtube | `https://www.youtube.com/watch?v=<id>` |
| instagram | media `permalink`, falling back to the media id |
| facebook | `permalink_url`, falling back to the video id |
| tiktok | `publish_id` (Direct Post returns no public URL; the Display API's `video.list` scope would be needed for a share URL — not requested in v0.1.0) |
