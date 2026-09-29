# Setup: YouTube

Connects `survey-publish` to a YouTube channel via the YouTube Data API v3.
You will create a Google Cloud OAuth client, then run `survey-publish connect
youtube` once. Tokens are stored at `~/.survey-publish/tokens.json` (0600)
and refresh automatically.

For Charlie: use the Google account that owns the **Charlie Ryder** YouTube
channel.

## 1. Create a Google Cloud project

1. Go to [Google Cloud console](https://console.cloud.google.com/) and sign in
   with the Google account that owns the Charlie Ryder channel.
2. Create a new project (any name, e.g. `survey-publish`).
3. Open **APIs & Services -> Library**, search **YouTube Data API v3**, and
   click **Enable**.

## 2. Configure the OAuth consent screen

1. Go to **APIs & Services -> OAuth consent screen**.
2. User type: **External**, click Create.
3. Fill in app name (e.g. `survey-publish`), your email, and a contact email.
4. **Scopes**: add `https://www.googleapis.com/auth/youtube.upload`.
   (Upload-only scope — the app cannot read or manage anything else.)
5. **Test users**: add the Google account that owns the channel. While the app
   is in testing mode, only test users can authorize it — that is fine for
   personal use. (Publishing to the app's verification is only needed if
   other people will authorize it.)
6. Save. Leave the app in **Testing** mode unless you later need public users.

## 3. Create the OAuth client (Desktop app)

1. Go to **APIs & Services -> Credentials -> Create Credentials -> OAuth client ID**.
2. Application type: **Desktop app**. Name it `survey-publish`.
3. Copy the **Client ID** and **Client secret**. Treat the secret like a
   password (it is stored locally in the token file, never in code).

## 4. Connect

```bash
survey-publish connect youtube
```

- Paste the client ID and secret when prompted (or set
  `SURVEY_PUBLISH_YOUTUBE_CLIENT_ID` / `SURVEY_PUBLISH_YOUTUBE_CLIENT_SECRET`
  env vars to skip the prompts).
- Open the printed URL, sign in with the channel-owning Google account, and
  authorize the `youtube.upload` scope.
- Your browser will try to open a page that fails to load — **that is
  expected**. Copy the **full address** from the browser address bar
  (it contains `code=...`) and paste it into the terminal.
- The adapter verifies by fetching your channel and prints
  `Connected to YouTube as '<channel title>'`.

## 5. Verify

```bash
survey-publish status   # youtube -> yes + channel name
```

## Notes & limits

- **Quota**: each upload costs ~1,600 units against the default 10,000
  units/day project quota — about **6 uploads/day**. Request a quota increase
  in Cloud console (**APIs & Services -> YouTube Data API v3 -> Quotas**) if
  you need more. YouTube may also enforce a separate per-day upload cap
  (`uploadLimitExceeded`); space automated uploads out.
- **Privacy**: default is `public`. Use `--opt privacy=unlisted` (or `private`)
  for drafts/review.
- **Category**: default `category_id=28` (Science & Technology). Override with
  `--opt category_id=22`, etc.
- **Shorts**: vertical 9:16 videos ≤ 60s are classified as Shorts automatically
  by YouTube — no special flag needed.
- Uploads are resumable/chunked (8 MiB chunks); a dropped connection resumes
  via the 308 protocol rather than restarting.
