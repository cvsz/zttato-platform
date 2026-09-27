# Production Readiness Gates

Production-ready is an evidence claim, not a branch name.

Use independent gates. Passing one gate does not imply that every other gate has passed.

`[x]` records only the scope stated in the linked evidence. Isolated synthetic staging
passes do not establish live production controls or external TikTok approval. See
[2026-09-27 release-gate evidence](evidence/2026-09-27-release-gates.md).

## Scope model

```text
Commerce Sources
      ↓
Product Ingestion / Normalization
      ↓
Affiliate Core
      ↓
Content / AI / Media
      ↓
Publishing Intent
      ↓
Distribution Providers
      ↓
TikTok Integration
```

See [ARCHITECTURE_BOUNDARIES.md](ARCHITECTURE_BOUNDARIES.md), [SCOPE_AND_RESPONSIBILITY_MATRIX.md](SCOPE_AND_RESPONSIBILITY_MATRIX.md) and [AI_MASTER_PRODUCTION_PROMPT.md](AI_MASTER_PRODUCTION_PROMPT.md).

## Gate vocabulary

Use only:

- `PASS`
- `FAIL`
- `BLOCKED_EXTERNAL`
- `NOT_TESTED`
- `NOT_APPLICABLE`

Do not infer production readiness from source-code existence, documentation, mocked tests, or a successful local startup.

## P0 — application/platform

- [ ] Main branch ruleset/protection requires review and green CI/security checks.
- [ ] Production environment validation passes with HTTPS, PostgreSQL and persistent encryption key.
- [ ] Cloudflare/edge route exposes required public/legal/callback routes while admin surfaces remain protected.
- [ ] Legal entity, contact and postal address are real and counsel has reviewed public policies.
- [x] PostgreSQL backup plus isolated restore is executed and timestamped (isolated synthetic staging only).
- [x] Media retention/deletion behavior is verified (isolated synthetic staging only).
- [x] Known-good container/image rollback is executed and timestamped (isolated synthetic staging only).
- [ ] Token encryption key backup/rotation and incident procedure is tested.
- [ ] Monitoring/alerts cover 5xx, OAuth failures, provider errors, disk/media capacity and DB health (rule logic tested; live Prometheus/Alertmanager routing and receiver remain unverified).
- [ ] No credentials are present in repository history or release artifacts.

## P0 — security

- [ ] Secret scan is clean or every finding is resolved/rotated and documented.
- [ ] OAuth state is cryptographically secure, short-lived and single-use.
- [ ] Access/refresh tokens are encrypted at rest and never logged or returned to clients.
- [ ] Tenant/resource authorization is enforced server-side.
- [ ] SSRF, CSRF, IDOR, path traversal, malicious upload and open redirect controls are tested.
- [x] Rate limiting/resource exhaustion controls are verified (application quotas and local/staging tests only).
- [ ] Production errors do not expose stack traces, credentials or internal secrets.
- [ ] Provider credentials are isolated from generic Affiliate services and browser code.

## P0 — architecture

- [ ] Affiliate Core works without TikTok credentials/configuration.
- [ ] Commerce providers are adapters and their schemas do not become canonical domain models.
- [ ] TikTok is a distribution integration, not the Affiliate Core.
- [ ] Product identity is internal/source-neutral.
- [ ] Product and Offer are separate concepts.
- [ ] Content and Media are provider-neutral.
- [ ] Publishing uses a provider-neutral contract.
- [ ] Adding a commerce provider does not require rewriting Affiliate Core.
- [ ] Adding a distribution provider does not require rewriting Affiliate Core.
- [ ] Provider failures are isolated from canonical business state.

## P0 — Affiliate Core

Affiliate readiness is independent of TikTok readiness.

- [ ] Canonical Product model is source-neutral.
- [ ] Commerce provider IDs are not used as internal Product IDs.
- [ ] Product and Offer are separate concepts.
- [ ] Affiliate Links are independent of a specific distribution platform.
- [ ] Campaigns and content are platform-neutral.
- [ ] Media assets are reusable across distribution providers.
- [ ] Affiliate analytics are independent of TikTok availability.
- [ ] Affiliate functionality remains operational when TikTok is disabled/unavailable.
- [ ] Provider failures cannot corrupt Affiliate Core state.
- [ ] Tenant isolation is tested for all business resources.

## P0 — Commerce Integrations

- [ ] External provider APIs are isolated behind adapters/capability interfaces.
- [ ] External responses are validated and normalized before persistence.
- [ ] Pagination/cursors are handled correctly.
- [ ] Synchronization is idempotent.
- [ ] Retryable and permanent errors are distinguished.
- [ ] Provider outages do not delete or corrupt canonical products.
- [ ] Deduplication has deterministic identity rules.
- [ ] Ambiguous product matches do not silently merge unrelated products.

## P0 — Content / AI / Media

- [ ] Content remains platform-neutral.
- [ ] AI provider credentials are isolated.
- [ ] AI failures do not corrupt campaign state.
- [ ] Generated content has deterministic validation and moderation/business-rule checks appropriate to the product.
- [ ] Uploaded media has size/type/content validation and safe storage keys.
- [ ] Path traversal and arbitrary file overwrite are blocked.
- [ ] FFmpeg/transcoding/resource limits are bounded.
- [x] Media storage is private by default where appropriate ([verified CIFS DACL and cutover evidence](evidence/2026-09-27-media-storage-cutover.md)).
- [ ] Media assets are reusable across distribution providers.
- [ ] Temporary media cleanup and retention are verified.

## P0 — Distribution

- [ ] Publishing uses a provider-neutral contract.
- [ ] Publish jobs are tenant-scoped.
- [ ] Publishing is idempotent (durable DB queue, HMAC request fingerprint, same-key uncertain replay protection, and safe explicit retry of confirmed pre-initiation failures verified by local synthetic regression tests; external provider reconciliation is still open).
- [ ] Duplicate requests cannot create duplicate provider operations (row-locked claims and same-key replay do not repeat the provider call in synthetic tests; real TikTok timeout-after-success reconciliation remains external).
- [ ] Retry/backoff/rate-limit behavior is bounded and observable.
- [ ] Failed distribution jobs do not corrupt Affiliate Core entities.
- [ ] Provider status reconciliation handles upstream timeout-after-success cases.

## P0 — TikTok

- [ ] Exact TikTok redirect/domain/URL verification is complete.
- [ ] Requested scopes match actual application configuration.
- [ ] Real Sandbox E2E evidence covers every requested capability.
- [ ] `/dashboard` has been verified with a real authorized TikTok Sandbox account, not only mocked API tests.
- [ ] Creator information is queried immediately before Direct Post where required.
- [ ] Direct Post requires explicit current user consent.
- [ ] Upload/Draft flow is verified where applicable.
- [ ] Publishing status is persisted and reconciled.
- [ ] Duplicate-post reconciliation is exercised.
- [ ] Commercial disclosure and AI-generated-content behavior is reviewed against the current approved client configuration.
- [ ] TikTok production approval/audit is verified externally before claiming production Direct Post capability.
- [ ] Development account credentials remain outside source control and logs.

## P1 — database and operations

- [x] Versioned Alembic migrations exist through `20260927_04` and automatic `create_all` is disabled in staging/production (isolated PostgreSQL 17 migration and restore rehearsal passed; see timestamped evidence).
- [x] Isolated PostgreSQL migration and restore have been executed and timestamped.
- [x] Scheduled cleanup removes expired browser/OAuth records and expired media (isolated synthetic staging only).
- [ ] Content Posting webhooks are validated and authenticated where adopted.
- [ ] SBOM, container vulnerability scan and provenance evidence are produced for releases.
- [x] Load/soak baseline is documented (local readiness endpoint only).
- [x] Representative encrypted-token restore test is completed without exposing token values (synthetic drill key only).
- [x] Rollback has been rehearsed for a schema-compatible release (old image with its pre-migration schema, isolated staging only).

## P1 — optional zWallet invoice intents

- [ ] Adapter URL and service token are supplied through approved runtime configuration and secret storage.
- [ ] Live adapter-to-ledger idempotency, tenant scoping, and timeout behavior are verified.
- [ ] Payment processor completion and ledger settlement are evidenced before any balance or usage feature is advertised.
- [ ] A durable Workspace/Tenant identity replaces session-level scoping before billing identities must survive browser-session changes.

## CI acceptance

- [ ] CI runs on an assigned GitHub runner and all required jobs pass.
- [ ] Unit tests pass.
- [ ] Integration tests pass.
- [ ] Migration upgrade/downgrade/re-upgrade tests pass where supported by migration policy.
- [ ] Security/secret scans pass.
- [ ] Container build and scan pass.
- [ ] Production readiness tests fail closed when required evidence/configuration is absent.
- [ ] Documentation consistency checks pass where implemented.

## External blockers

External approval or infrastructure constraints must be recorded as `BLOCKED_EXTERNAL`, not hidden by changing source code or documentation.

Examples:

- TikTok app review/audit not completed.
- Production credentials unavailable.
- Real Sandbox account unavailable.
- Legal review incomplete.
- Production domain verification incomplete.
- Isolated infrastructure restore environment unavailable.

## Release rule

Do not close a gate from documentation alone.

Do not mark the overall repository `PRODUCTION_READY` while a critical P0 gate is `FAIL` or `NOT_TESTED`.

TikTok approval may remain `BLOCKED_EXTERNAL` while Affiliate Core is independently production-ready, provided the release does not claim unavailable TikTok capabilities.
