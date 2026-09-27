# Operations

## Runtime
Run behind an HTTPS reverse proxy/Cloudflare tunnel. The app listens on loopback in Compose by default. Never expose PostgreSQL publicly.

## Secrets
Store TIKTOK_CLIENT_SECRET, APP_ENCRYPTION_KEY and POSTGRES_PASSWORD outside Git. Rotate credentials immediately if exposed in screenshots or logs. Token values, OAuth callback codes/state, TikTok publish IDs and upload URLs must never be logged. Uvicorn access logging is disabled because the Login Kit callback carries a one-time code and state in its query string; configure the HTTPS edge not to retain those query values, then verify edge logs with a synthetic canary before launch.

Compose evaluates `.env` assignments in file order. Put `POSTGRES_PASSWORD` before any value that references it, including `DATABASE_URL`, and keep secret-bearing env files mode `0600`. Use a long random password. `.env.sandbox` and `.env.production` currently target the same Compose database (`db/zttato`), so those profile files are not database-isolated and must not be treated as staging evidence.

For isolated local application rehearsal, generate a separate mode-`0600` Compose env file containing a new random `POSTGRES_PASSWORD`, a newly generated `APP_ENCRYPTION_KEY`, `APP_ENV=sandbox`, `APP_BASE_URL=http://localhost:8001`, `APP_ALLOWED_HOSTS=localhost,127.0.0.1`, `MEDIA_DIR=/srv/media`, and `ZTTATO_ENV_FILE` pointing to that same file. Do not copy values from `.env.production` or `.env.sandbox`. Start with `docker compose -p zttato-sandbox --env-file /path/to/isolated.env -f compose.yaml -f compose.sandbox.yaml up -d --build`. This uses a separate project-scoped PostgreSQL volume, media volume, database/user, app port 8001 and loopback-only database port 55432; the overlay forces TikTok and zWallet credentials off. Stop and remove only that isolated project with the matching files and `docker compose ... down -v`. This is local infrastructure rehearsal, not TikTok Sandbox evidence; real OAuth requires a separately registered non-production callback and account.

The publish worker polls durable jobs every `PUBLISH_WORKER_INTERVAL_SECONDS` (default 2 seconds), claims one row with PostgreSQL `FOR UPDATE SKIP LOCKED`, and never replays a claimed operation automatically. Queued caption/options are Fernet-encrypted and cleared after provider init or a preflight failure; only the HMAC request fingerprint and minimal consent version/timestamp remain for idempotency/audit. Alert on oldest queue age; verify the private metrics scrape and receiver independently.

Photo Post requires `TIKTOK_VERIFIED_MEDIA_URL_PREFIXES` to contain only HTTPS domain or URL prefixes that TikTok has verified for this app. Configure comma-separated prefixes ending in `/`. The application rejects photo URLs outside those prefixes and keeps Photo Post unavailable when the list is empty. TikTok still validates public accessibility, redirects, image format, dimensions and size upstream.

## Schema migrations
Run `docker compose run --rm migrate` before the first production app startup. The migration service receives only `APP_ENV` and the database connection string; it does not need the TikTok client secret or Fernet key. See `docs/MIGRATIONS.md` for the existing-database compatibility gate.

## Backup
Back up PostgreSQL with a consistent pg_dump and back up the token encryption key in a separate protected secret store. Media is operational/transient data and must follow an explicit retention policy.

## Host media storage
The configured Compose target maps `/mnt/zeaz-data/zttato-media` on `core` to `/srv/media` and runs the app as UID/GID `1000:1000` to match the CIFS mount owner. The bind mount fails closed when the external path is unavailable; the old `media_data` named volume remains available for rollback.

The current CIFS mount reports `file_mode=0770` and `dir_mode=0770`. The application requests mode `0600` for uploaded files, but the CIFS client still presents files as `0770`; do not rely on POSIX mode bits alone. The target directory DACL was verified as protected from inheritance and grants access only to the mount identity, Administrators and SYSTEM. Recheck it after server or mount changes. The current local group `1000` contains only `cvsz`.

For a media cutover in either direction, pause app writes, copy all media files without deleting the source, compare file counts and SHA-256 hashes, then recreate the app container. Before rolling back from the bind mount, sync new uploads back to `media_data` first. Do not use `docker compose down -v` during rollback or cutover. See the [2026-09-27 cutover evidence](evidence/2026-09-27-media-storage-cutover.md).

## Data retention and deletion

ค่าเริ่มต้นที่ตั้งในแอป (`.env.example`) ใช้ช่วงเวลาเหล่านี้ โดย cleanup ทำงานเมื่อแอปเริ่มและทำซ้ำทุกชั่วโมง:

- OAuth state ถูกลบหลังหมดอายุ 15 นาที; รอบ cleanup อาจทำให้ข้อมูลหมดอายุค้างอยู่ได้ไม่เกินหนึ่งรอบ
- browser session มีอายุ 24 ชั่วโมงนับจากออก cookie; เมื่อหมดอายุจะลบ session, linked-account row และ access/refresh token ciphertext พร้อม OAuth requests, publish jobs และ media ของ session หากไม่มี publish job ที่ยังรอ reconciliation ในช่วง retention 90 วัน; session ที่มีงานค้างจะถูกเลื่อนการลบจนกว่างานจบหรือพ้นช่วง retention
- media และ photo-set metadata ที่ยังอยู่กับ session ลบเมื่อครบ 30 วัน; job ที่ยังทำงานหรือรอ reconciliation จะกันไม่ให้ลบ media ที่อ้างถึง
- terminal publish-job records เก็บ 90 วันจากการเปลี่ยนสถานะล่าสุด; งานที่ยังไม่ terminal และ `RECONCILIATION_REQUIRED` จะไม่ถูกลบตามอายุ record
- quota counters เก็บไม่เกิน 2 วัน
- งาน `INITIATING` ที่ค้างเกิน 15 นาทีถูกเปลี่ยนเป็น `INITIATION_UNCERTAIN`; งาน `TRANSFER_PENDING` ที่มี provider ID และค้างเกิน 15 นาทีถูกเปลี่ยนเป็น `RECONCILIATION_REQUIRED`; cleanup ไม่เริ่ม external operation ซ้ำ
- คำขอลบข้อมูลผู้ใช้ลบ local media ก่อนลบ metadata; หากลบไฟล์ไม่ได้ endpoint ตอบ 503 และเก็บ metadata ไว้ให้ retry

การลบ token ในฐานข้อมูลเป็นการลบ ciphertext ภายในระบบนี้ ไม่ใช่หลักฐานว่า TikTok ได้ revoke token สำเร็จทุกกรณี การลบ local upload จะจำกัดอยู่ใน `MEDIA_DIR`; path ที่อยู่นอก root จะไม่ถูกแตะต้อง การลบไฟล์ที่ล้มเหลวต้องมี alert และ retry ผ่านรอบถัดไป

## Restore
Restore into an isolated environment first, start the exact known-good image, verify /health/ready, decrypt a controlled test token record if applicable, and prove that production data was not overwritten. Capture timestamped evidence.

## Rollback
Keep immutable image digests/releases. Roll back application image first when schema-compatible. Database rollback requires a tested migration plan; never reverse a destructive migration by assumption.

## Incident priorities
1. Stop unauthorized publishing and revoke exposed credentials.
2. Preserve logs/evidence without recording secret values.
3. Isolate affected sessions/tokens.
4. Restore known-good runtime/data.
5. Verify TikTok authorization and publishing status before retrying jobs.
