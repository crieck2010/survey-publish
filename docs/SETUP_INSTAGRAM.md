# Setup: Instagram

Connects `survey-publish` to an Instagram account for Reels publishing via the
Instagram Graph API. **Read the account requirement first** — it is the step
everyone gets wrong.

For Charlie: this is for the **new Instagram business page** (Business or
Creator account) linked to the new Facebook Page.

## 0. The account requirement (non-negotiable)

API publishing requires **both**:

1. An Instagram **Business** or **Creator** account (a personal account cannot
   publish via the API — switch in the Instagram app: Settings -> Account type
   and tools -> Switch to professional account), **and**
2. That account **linked to a Facebook Page** (in the Facebook Page:
   Settings -> Linked accounts -> Instagram; or in the Instagram app:
   Edit profile -> Page -> connect the Page).

`survey-publish connect instagram` checks this and tells you exactly what is
missing — but setting it up beforehand saves a round trip.

## 1. Create a Meta app

1. Go to [Meta for Developers](https://developers.facebook.com/) and sign in
   with the Facebook account that admins the Page.
2. **My Apps -> Create App**. Choose the **Business** app type (needed for the
   Instagram Graph API product).
3. In the app dashboard, add the **Instagram Graph API** product
   (under Add products / Use cases). Newer dashboards may list it as part of
   the Instagram API setup — follow the prompts to attach it to your Business
   portfolio.
4. Copy the **App ID** and **App secret** (Settings -> Basic).

## 2. Register the OAuth redirect URI

1. In the app dashboard go to **Facebook Login -> Settings**.
2. Under **Valid OAuth Redirect URIs**, add exactly:

   ```
   https://localhost/
   ```

   (The CLI's paste-back flow needs this registered. The browser will fail to
   load it — that is expected; you copy the address-bar URL back.)

## 3. Connect

```bash
survey-publish connect instagram
```

- Paste the App ID and App secret when prompted (or set
  `SURVEY_PUBLISH_META_CLIENT_ID` / `SURVEY_PUBLISH_META_CLIENT_SECRET`).
- Open the printed URL and authorize. You will be asked for the classic
  Instagram Graph API permissions (`instagram_basic`,
  `instagram_content_publish`) plus Page permissions.
- Copy the full `https://localhost/?code=...` address-bar URL back.
- Pick the Facebook Page when prompted (auto-selected if you admin exactly one).
- The adapter resolves the linked Instagram Business/Creator account and prints
  `Connected to Instagram as @<username>`.

## 4. Verify

```bash
survey-publish status   # instagram -> yes + @username
```

## 5. Publishing needs a public video URL

Meta's servers **fetch the video file themselves** from a public HTTPS URL —
the `/media` edge accepts no local-file upload. Every Instagram publish must
pass the URL:

```bash
survey-publish publish --platform instagram \
  --video reel.mp4 \
  --title "Gulf Stream this week" \
  --caption "..." --hashtags "oceans" \
  --opt video_url=https://example.com/reel.mp4
```

Host the finished MP4 somewhere public: your own web host, a CDN, or object
storage with a public link. (The `--video` path is still validated locally as
a sanity check.) Reels limits: caption ≤ 2,200 chars (the adapter truncates),
up to 30 hashtags.

## Notes & limits

- Containers expire 24h after creation; the adapter publishes immediately, so
  this never bites in practice.
- The adapter polls the container until `FINISHED` before publishing; a
  `ERROR`/`EXPIRED` container raises an actionable error (usual cause: the
  `video_url` is not publicly fetchable).
- **Scope uncertainty (documented):** Meta has been migrating Instagram API
  access toward a newer "Instagram Business Login" product with
  differently-named scopes. This engine requests the long-documented classic
  scopes; if Meta rejects them for a brand-new app, the fix is to enable the
  new login product and update `IG_SCOPES` in `src/publish/instagram.py`.
- Instagram and Facebook sharing: both adapters use the **same Meta app**;
  each `connect` stores its own token set (a known v0.1.0 duplication).
