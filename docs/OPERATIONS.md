# Operations

## Runtime
Run behind an HTTPS reverse proxy/Cloudflare tunnel. The app listens on loopback in Compose by default. Never expose PostgreSQL publicly.

## Secrets
Store TIKTOK_CLIENT_SECRET, APP_ENCRYPTION_KEY and POSTGRES_PASSWORD outside Git. Rotate credentials immediately if exposed in screenshots or logs. Token values and TikTok upload URLs must never be logged.

## Schema migrations
Run `docker compose run --rm migrate` before the first production app startup. See `docs/MIGRATIONS.md` for the existing-database compatibility gate.

## Backup
Back up PostgreSQL with a consistent pg_dump and back up the token encryption key in a separate protected secret store. Media is operational/transient data and must follow an explicit retention policy.

## Data retention and deletion

ค่าเริ่มต้นที่ตั้งในแอป (`.env.example`) ใช้ช่วงเวลาเหล่านี้ โดย cleanup ทำงานเมื่อแอปเริ่มและทำซ้ำทุกชั่วโมง:

- OAuth state ถูกลบหลังหมดอายุ 15 นาที; รอบ cleanup อาจทำให้ข้อมูลหมดอายุค้างอยู่ได้ไม่เกินหนึ่งรอบ
- browser session มีอายุ 24 ชั่วโมงนับจากออก cookie; เมื่อหมดอายุจะลบ session, linked-account row และ access/refresh token ciphertext พร้อม OAuth requests, publish jobs และ media ของ session
- media และ photo-set metadata ที่ยังอยู่กับ session ลบเมื่อครบ 30 วัน; job ที่ยังทำงานหรือรอ reconciliation จะกันไม่ให้ลบ media ที่อ้างถึง
- terminal publish-job records เก็บ 90 วันจากการเปลี่ยนสถานะล่าสุด; งานที่ยังไม่ terminal และ `RECONCILIATION_REQUIRED` จะไม่ถูกลบตามอายุ record
- quota counters เก็บไม่เกิน 2 วัน
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
