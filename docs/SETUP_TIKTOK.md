# Setup: TikTok

Connects `survey-publish` to a TikTok account for Direct Post via the TikTok
Content Posting API. **Read the app-review requirement first** — nothing works
until it is satisfied.

For Charlie: this is for the **new TikTok creator channel**.

## 0. The app-review requirement (non-negotiable)

TikTok only enables the Content Posting API for developer apps that have
**passed TikTok's audit/app review** for the posting scopes. Until your app
passes, the API rejects posts server-side — no client can work around that.

This engine enforces the gate honestly: `survey-publish connect tiktok` asks
whether your app has passed review and records the answer. `publish()`
**refuses to run** until the answer is yes. Reconnect after approval.

## 1. Create a TikTok developer app

1. Go to the [TikTok for Developers portal](https://developers.tiktok.com/)
   and sign in.
2. Create an app. Note the **Client key** and **Client secret**.
   (TikTok calls the id a "client key"; the adapter stores it as `client_id`.)
3. In **Add products**, add **Content Posting API** (and **Login Kit**, which
   carries the OAuth flow).
4. Request the scopes: `user.info.basic`, `video.publish`.
5. Register the redirect URI **exactly**:

   ```
   https://localhost/callback
   ```

   (The browser will fail to load it — expected; you copy the address-bar URL
   back into the CLI.)
6. Submit the app for **audit/app review** for the Content Posting API and
   wait for approval.

## 2. Connect

```bash
survey-publish connect tiktok
```

- Paste the client key and secret (or set `SURVEY_PUBLISH_TIKTOK_CLIENT_ID` /
  `SURVEY_PUBLISH_TIKTOK_CLIENT_SECRET`).
- Open the printed `https://www.tiktok.com/v2/auth/authorize/...` URL,
  authorize, and paste the full redirect address-bar URL back.
- Answer the app-review question honestly (`y` only after TikTok approved it).
- The adapter prints `Connected to TikTok as @<display name>`.

## 3. Verify

```bash
survey-publish status   # tiktok -> yes + @display name
```

## 4. Publishing

```bash
survey-publish publish --platform tiktok \
  --video reel.mp4 \
  --title "Gulf Stream this week" \
  --caption "..." --hashtags "oceans" \
  --opt privacy_level=SELF_ONLY
```

Flow: `creator_info/query` (validates your privacy level against the
account's allowed options) -> `video/init` (`FILE_UPLOAD`) -> chunked `PUT`
(10 MiB chunks, `Content-Range` headers) -> poll `status/fetch` to
`PUBLISH_COMPLETE`. Default privacy is `SELF_ONLY` (safe); use
`PUBLIC_TO_EVERYONE` only once the account/app is cleared for it.

## Notes & limits

- **Return value is a `publish_id`, not a public URL.** A shareable URL needs
  the separate Display API (`video.list` scope), which this engine does not
  request — check the TikTok app for the live post.
- **Title limit**: 2,200 chars (adapter truncates).
- **Uncertainty (documented):** some third-party integrations report TikTok's
  Direct Post endpoint also requires `brand_content_toggle` /
  `brand_organic_toggle` in `post_info`. TikTok's own Direct Post example does
  not include them, so this engine sends only the documented fields. If init
  is rejected asking for those flags, add them to `post_info` in
  `src/publish/tiktok.py::init_upload` (two lines) and report it so the engine
  can be updated.
- The separate "Upload to Drafts" inbox flow (`video.upload` scope) is not
  implemented in v0.1.0 — this adapter direct-posts only.
