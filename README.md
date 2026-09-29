# survey-publish

A deterministic, dependency-light **publishing engine** that uploads finished MP4 reels to social platforms: **YouTube**, **Instagram** (Reels), **Facebook** (Page videos), and **TikTok** (Direct Post).

It is the last step of the map-reel pipeline: `survey-currents` fetches the data, `survey-viz` renders it, `reel-studio` assembles the MP4 (audio already muxed) — and `survey-publish` puts that MP4 on the platforms. It never edits the video, never muxes audio, never re-encodes: it publishes the file exactly as given.

## Design

- **Engine/UI split** — pure Python under `src/`, zero UI-framework imports. Consumed by the `reel-studio` Streamlit app and the `survey-schedule` runner via a small stable API (`publish.registry.get_adapter`, `publish.models.PublishRequest`).
- **Dependency-light** — stdlib + `requests` only.
- **Honest errors** — every failure raises a one-line actionable message naming the exact command and setup doc to follow. No tracebacks, no leaked tokens.
- **Deterministic** — same request, same code path, same API calls. Uploads are chunked/resumable where the platform supports it.

## Install

```bash
pip install git+https://github.com/crieck2010/survey-publish.git
```

Requires Python 3.9+. For development:

```bash
git clone https://github.com/crieck2010/survey-publish.git
cd survey-publish
pip install -e ".[dev]"   # dev extra not needed; plain `pip install -e .` suffices
pytest
```

## Quickstart

```bash
# 1. Connect each platform once (OAuth installed-app flow; tokens stored at
#    ~/.survey-publish/tokens.json with 0600 permissions)
survey-publish connect youtube
survey-publish connect instagram
survey-publish connect facebook
survey-publish connect tiktok

# 2. Check status
survey-publish status

# 3. Publish a finished reel
survey-publish publish --platform youtube \
  --video reel.mp4 \
  --title "Gulf Stream this week" \
  --caption "Weekly ocean-current update from satellite data." \
  --hashtags "oceans,surveying,maps"

# 4. Or publish to every connected platform at once
survey-publish publish-all \
  --video reel.mp4 \
  --title "Gulf Stream this week" \
  --caption "Weekly ocean-current update from satellite data." \
  --hashtags "oceans,surveying,maps"
```

Per-platform options ride on `--opt KEY=VALUE` (repeatable):

```bash
survey-publish publish --platform youtube --video reel.mp4 --title "T" \
  --opt privacy=unlisted --opt category_id=28
survey-publish publish --platform instagram --video reel.mp4 --title "T" \
  --opt video_url=https://example.com/reel.mp4
survey-publish publish --platform tiktok --video reel.mp4 --title "T" \
  --opt privacy_level=SELF_ONLY
```

## Platform prerequisites

| Platform | Adapter | What you need before `connect` works |
|---|---|---|
| YouTube | `youtube` | Google account with a YouTube channel + a Google Cloud OAuth client (Desktop app type). Setup: `docs/SETUP_YOUTUBE.md` |
| Instagram | `instagram` | Instagram **Business or Creator** account **linked to a Facebook Page** + a Meta app. Setup: `docs/SETUP_INSTAGRAM.md` |
| Facebook | `facebook` | A Facebook Page you can `CREATE_CONTENT` on + a Meta app. Setup: `docs/SETUP_FACEBOOK.md` |
| TikTok | `tiktok` | TikTok developer app with the Content Posting API **audit/app review passed**. Setup: `docs/SETUP_TIKTOK.md` |

## CLI reference

```
survey-publish connect <platform>      Run the OAuth flow for one platform
survey-publish disconnect <platform>   Delete stored tokens for one platform
survey-publish status                   Table of platforms: connected yes/no + account
survey-publish publish --platform NAME --video FILE --title TEXT
    [--caption TEXT] [--hashtags a,b,c] [--opt KEY=VALUE ...]
survey-publish publish-all --video FILE --title TEXT
    [--caption TEXT] [--hashtags a,b,c] [--opt KEY=VALUE ...]
    [--platforms youtube,tiktok]        Publish to every connected platform
```

Exit code `0` on success, `1` on failure; errors print one actionable line to stderr.

## Python API

```python
from publish.registry import get_adapter
from publish.models import PublishRequest

request = PublishRequest(
    video_path="reel.mp4",
    title="Gulf Stream this week",
    caption="Weekly ocean-current update.",
    hashtags=["oceans", "surveying"],
    platform_options={"privacy": "public"},  # per-platform knobs
)
result = get_adapter("youtube").publish(request)
print(result.url_or_id)  # https://www.youtube.com/watch?v=...
```

Full reference: `docs/API.md`. Embedding contract: `INTEROP.md`.

## Audio policy

**survey-publish does not mux audio — it publishes the MP4 exactly as given.** Audio is muxed upstream: `reel-studio` / `survey-animate` already mux `--audio_path` as AAC before the file reaches this engine. If your reel is silent, the published post is silent; that is upstream's decision, not this engine's.

Platform reality, documented so there are no surprises:

- **API uploads cannot attach TikTok/Instagram trending in-app sounds.** Those sounds live inside the TikTok/Instagram apps and can only be added by editing in-app. Anything uploaded via API carries only the audio already in the file.
- **Uploading copyrighted music risks muting, takedowns, or Content ID claims.** Use audio you own or royalty-free tracks (the same rule reel-studio documents for `--audio_path`).

## Honest limits

- **YouTube quota**: each upload costs ~1,600 quota units against the default 10,000 units/day project quota — roughly 6 uploads/day unless you request a quota increase. YouTube may also enforce a separate per-day upload cap (`uploadLimitExceeded`); space automated uploads out.
- **Instagram needs a public video URL**: Meta's servers fetch the file from `video_url` — the API has no local-file upload on the `/media` edge. Pass `platform_options={"video_url": "https://..."}` (CLI: `--opt video_url=...`). Local-only files cannot be published to Instagram by this engine in v0.1.0.
- **Instagram account eligibility**: Business/Creator account linked to a Facebook Page, or the API refuses. `connect()` checks and tells you exactly what to fix.
- **Facebook multipart cap**: direct uploads are capped at 1 GB by Meta; larger files need Meta's resumable-upload API (not implemented in v0.1.0 — the adapter tells you).
- **TikTok app review gate**: `publish()` refuses to run until your TikTok developer app has passed the Content Posting API audit/app review. `connect` records your answer; reconnect after approval.
- **TikTok returns a `publish_id`, not a public URL**: the share URL needs the separate Display API (`video.list` scope), which this engine does not request.
- **Tokens live on disk** at `~/.survey-publish/tokens.json` (0600). They are never printed or logged. Page tokens are long-lived; YouTube/TikTok tokens refresh automatically.
- **No analytics, no scheduling, no comments** in v0.1.0 — publishing only.

## Project layout

```
src/publish/      Engine (stdlib + requests only)
  models.py       PublishRequest / PublishResult / Credentials
  base.py         PlatformAdapter ABC + shared OAuth helpers + error types
  registry.py     PLATFORMS dict, get_adapter(), list_platforms()
  store.py        ~/.survey-publish/tokens.json (0600) token store
  youtube.py      YouTube Data API v3 resumable upload
  instagram.py    Instagram Graph API Reels (container -> poll -> publish)
  facebook.py     Facebook Graph API Page video (multipart upload)
  tiktok.py       TikTok Content Posting API Direct Post (FILE_UPLOAD)
  cli.py          `survey-publish` command-line interface
tests/            pytest suite (all HTTP stubbed; no network, no credentials)
docs/             Per-platform setup guides + API reference
INTEROP.md        Embedding contract for reel-studio / survey-schedule
```

## License

MIT — see `LICENSE`.
