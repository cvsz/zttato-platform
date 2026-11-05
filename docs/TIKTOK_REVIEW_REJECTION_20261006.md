# TikTok Review Rejection Follow-up — 2026-10-06

## Reviewer feedback

The app changes were not approved for production. The reviewer asked for updated Terms of Service, Privacy Policy and Website URL, and reported that the Website URL is invalid or inaccessible, blank, unfinished, or returns an error.

## Current status

**Overall: `BLOCKED_EXTERNAL`.** Read-only requests to the configured public origin returned HTTP 530 with response body `error code: 1033`:

- `https://zttato.zeaz.dev/`
- `https://zttato.zeaz.dev/privacy-policy`
- `https://zttato.zeaz.dev/terms-of-service`
- `https://zttato.zeaz.dev/assets/styles.css`
- `https://zttato.zeaz.dev/assets/i18n.js`
- `https://zttato.zeaz.dev/tiktok/uploading/`

Cloudflare documents [error 1033](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/) as a Tunnel error where it cannot find a healthy `cloudflared` connector. The public requests do not reach the application, so repository changes alone cannot restore the live Website URL.

On-host read-only checks found `zaffiliate-tunnel` has no active connection, while the separate `zwslcore` tunnel has four. The user-level `zwslcore-cloudflared` service is active, which does not establish that it is connected to the tunnel configured for `zttato.zeaz.dev`. The public CNAME query returned no target, and the HTTP request terminates at Cloudflare, so these checks do not identify which Tunnel owns the hostname; confirm that mapping in Cloudflare. No zTTato container was found in the current Docker context; if this context is the intended origin, the app service also needs to be restored.

## Repository changes made

- Expanded the Privacy Policy to describe the TikTok account/profile fields, encrypted tokens, uploaded media, publishing settings/status, essential session data, optional invoice-intent data, sharing, deletion controls and retention behavior implemented by the application.
- Expanded the Terms of Service with service scope, authorization, content responsibilities, publishing, third-party service and availability terms.
- Expanded the public homepage with product/support context and made the public support identity render from the same validated operator settings.
- Legal pages now render the configured media and record retention periods, so public retention statements follow application configuration.
- Restricted `/assets` to frontend assets. The previous static mount exposed raw HTML files from `web/`, including legal templates with unfilled placeholders, under `/assets/*.html`.
- Replaced the old “verified” domain markers with the current external blocker. The redirect URI and portal URL values have not been changed.

## Remaining actions before resubmission

1. In Cloudflare, confirm which Tunnel owns `zttato.zeaz.dev`. Restore a healthy connector for that exact Tunnel and inspect its hostname-to-origin route. The inactive `zaffiliate-tunnel` is a candidate; do not route the hostname to `zwslcore` until its ingress points to the zTTato origin. Recheck until public requests no longer return 1033.
2. Deploy the reviewed repository revision to the intended origin after the external route is repaired. Deployment has not been performed from this audit.
3. Confirm public HTTPS success and rendered content for the homepage, Privacy Policy, Terms, frontend assets and both TikTok URL-verification paths. Confirm no legal placeholders appear.
4. Confirm the supplied registered operator name, postal address, telephone and public support email are the intended production details. They are now configured as application defaults and in `.env.example`. Confirm production retention values, hosting/billing subprocessors and backup retention with the operator, then obtain the required legal review. Production configuration still fails closed if legal identity values are overridden with missing or placeholder values.
5. In the TikTok Developer Portal, set Website URL to `https://zttato.zeaz.dev/`, Terms of Service URL to `https://zttato.zeaz.dev/terms-of-service`, and Privacy Policy URL to `https://zttato.zeaz.dev/privacy-policy` after each page is reachable and reviewed.
6. Capture current public-page evidence and the required real TikTok Sandbox walkthrough, then submit the changes through the portal.

No production deployment or review submission was performed.
