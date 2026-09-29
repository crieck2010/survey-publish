# Setup: Facebook

Connects `survey-publish` to a Facebook Page for video publishing via the
Facebook Graph API. You need a Page you can perform the `CREATE_CONTENT` task
on (Page admin works) and a Meta app.

For Charlie: this is for the **new Facebook creator Page**.

## 1. Create the Page (if needed)

1. Go to [facebook.com/pages/create](https://www.facebook.com/pages/create)
   and create the creator Page.
2. Make sure your Facebook account has the **CREATE_CONTENT** task on the Page
   (Page admins have it by default: Page -> Settings -> Page setup ->
   Page access).

## 2. Create a Meta app

1. Go to [Meta for Developers](https://developers.facebook.com/) and sign in.
2. **My Apps -> Create App** (Business type is fine).
3. Copy the **App ID** and **App secret** (Settings -> Basic).
   You can reuse the same Meta app as the Instagram setup.

## 3. Register the OAuth redirect URI

1. **Facebook Login -> Settings -> Valid OAuth Redirect URIs**, add:

   ```
   https://localhost/
   ```

## 4. Connect

```bash
survey-publish connect facebook
```

- Paste the App ID / App secret (or set `SURVEY_PUBLISH_META_CLIENT_ID` /
  `SURVEY_PUBLISH_META_CLIENT_SECRET`).
- Authorize in the browser; copy the full `https://localhost/?code=...`
  address-bar URL back.
- Pick the Page (auto-selected if you admin exactly one). The adapter stores
  the **Page access token** and prints `Connected to Facebook Page '<name>'`.

## 5. Verify

```bash
survey-publish status   # facebook -> yes + Page name
```

## Notes & limits

- Uploads go to `graph-video.facebook.com` as multipart form posts with the
  Page token — the long-documented direct form of the Page `/videos` edge.
- **1 GB cap**: Meta caps multipart (non-resumable) uploads at 1 GB / ~20 min.
  Larger reels need Meta's Resumable Upload API (`POST /<APP_ID>/uploads` then
  publish with the `fbuploader_video_file_chunk` handle) — not implemented in
  v0.1.0; the adapter raises an actionable error telling you exactly this.
- Permissions granted: `pages_show_list`, `pages_read_engagement`,
  `pages_manage_posts` — posting only, nothing else.
- The adapter returns the video's `permalink_url` when available, else the
  video id.
