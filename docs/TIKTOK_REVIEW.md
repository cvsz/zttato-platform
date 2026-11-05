# TikTok App Review Evidence

## Reviewer follow-up — 2026-10-06

The latest reviewer decision requests updated Terms of Service, Privacy Policy and Website URL. The reviewer reports that the Website URL is invalid or inaccessible. Current external checks confirm a blocker: the homepage, both legal pages, their static assets and the TikTok site-verification path return HTTP 530 with Cloudflare error 1033. Record this as `BLOCKED_EXTERNAL`; do not treat the older domain checks below as current evidence.

## Portal configuration
- Web/Desktop URL: https://zttato.zeaz.dev/
- Redirect URI: https://zttato.zeaz.dev/tiktok/callback
- Site verification URL: https://zttato.zeaz.dev/tiktok/uploading/
- Expected verification body: `tiktok-developers-site-verification=9WurARgpbnJkdus0r4bfvSZydNYNxKUC`
- **Requested scopes**: `user.info.basic`, `video.upload`, `video.publish`
- **Products implemented**: Login Kit and Content Posting API.
- **Share Kit**: Not implemented. Remove from app configuration in TikTok Developer Portal.

## App description (for submission)
```
zTTato is a web dashboard that helps creators connect their TikTok accounts and manage video publishing. Login Kit securely authorizes users, and user.info.basic displays their name and avatar. Content Posting API lets creators select a video and choose Upload as Draft (video.upload) or Direct Post (video.publish). Before posting, users review the video, caption and available privacy settings and explicitly confirm their choice. Draft uploads are completed by the user in TikTok. Direct Post submissions display the resulting status. This revision improves user consent, updates our Privacy Policy and Terms of Service, and adds our app icon and favicon.
```

## Required reviewer recording
Record a real TikTok Sandbox session, not /review-preview:
1. Open the live HTTPS app and show favicon/app icon.
2. Open Privacy Policy and Terms of Service from the same origin.
3. Start Login Kit authorization and show the TikTok consent surface.
4. Return through /tiktok/callback and show connected state with name/avatar.
5. Upload a representative MP4.
6. Demonstrate Upload as Draft (video.upload), including the resulting TikTok inbox continuation.
7. Query creator info immediately before Direct Post.
8. Show only current privacy options returned by TikTok and creator-disabled controls.
9. Explicitly confirm Direct Post (video.publish), then show status polling/result.
10. Disconnect and demonstrate deletion/control surfaces.

Never expose Client Secret, access tokens, refresh tokens, upload URLs or production credentials in the recording.

## Current external constraints
TikTok states that unaudited clients' Direct Post content is restricted to private viewing. Keep TIKTOK_APP_AUDITED=false until the client has actually passed the applicable audit. Do not describe the app as TikTok-approved before confirmation.

## Real creator UI
Use /dashboard to perform the actual authenticated flow. /review-preview remains a deliberately non-posting mockup. The dashboard queries creator choices and requires a separate explicit consent checkbox. For unaudited clients only SELF_ONLY is selectable. Review commercial disclosure and AI-generated content controls against latest TikTok UX requirements and evidence before resubmission.

## Scopes requested (only these three)
- `user.info.basic` - displays creator name and avatar on dashboard
- `video.upload` - enables Upload as Draft flow
- `video.publish` - enables Direct Post flow

**Not requested** (removed per review feedback):
- `user.info.profile` - no profile page UI
- `user.info.stats` - no stats display
- `video.list` - no video listing UI
- `Share Kit` - not implemented (web-only app)

## Domain verification

- Domain: `zttato.zeaz.dev` — `BLOCKED_EXTERNAL` as of 2026-10-06; public requests currently fail at the Cloudflare Tunnel edge with error 1033.
- URL prefix: `https://zttato.zeaz.dev/tiktok/uploading/` — `BLOCKED_EXTERNAL`; expected verification body remains `tiktok-developers-site-verification=9WurARgpbnJkdus0r4bfvSZydNYNxKUC` after the route is reachable.
- URL prefix: `https://zttato.zeaz.dev/tiktok/video/` — not rechecked because the same public origin is unavailable.

Recheck each URL from outside the origin after restoring the tunnel. Require successful HTTPS responses and the expected verification body before recording a pass.

## Basic profile and dashboard integrity

The account header fetches `GET /api/profile` using the existing `user.info.basic` grant.
The backend requests only `open_id,avatar_url,display_name` from TikTok User Info,
matches the returned `open_id` to the linked account, and returns only the display name
and a validated HTTPS TikTok CDN avatar URL to the browser. Tokens and internal IDs
are never embedded in the page or sent to the browser.

Profile loading and Content Posting creator-info loading are independent.
`/api/creator-info` remains the source of current privacy and interaction settings
for Direct Post and requires `video.publish`; a basic-profile success must not
be taken as proof that publishing options were refreshed.

The tracked `web/dashboard.html` is canonical for `/dashboard`. A previous
live screenshot included Photo Post controls and literal `\\u00b7` separators,
although those controls were absent from the tracked HTML at this commit.
Before deploying, reconcile any server-local photo UI, commit its source
and tests, and verify that the deployed HTML, JavaScript and container digest
all come from the approved revision. Do not overwrite uncommitted live work.
