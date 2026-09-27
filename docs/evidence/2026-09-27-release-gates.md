# หลักฐาน Release Gates — 2026-09-27

## ขอบเขตและคำตัดสิน

ผลในเอกสารนี้มาจาก PostgreSQL 17 staging แบบแยกวงและข้อมูลสังเคราะห์ เว้นแต่ระบุเป็นอย่างอื่น ไม่มี production database, volume, TikTok token หรือ encryption key ถูกใช้ในการซ้อม

**สถานะโดยรวม: NOT_TESTED สำหรับ production readiness.** ผ่านการซ้อม migration, restore, cleanup, rollback และ local readiness soak บางส่วนแล้ว แต่ยังไม่มีหลักฐานจาก TikTok Sandbox จริง, ระบบ alert receiver จริง, production key escrow/rotation, legal review หรือ public-load soak

## หลักฐานที่ผ่าน

| Gate | สถานะ | หลักฐานและขอบเขต |
| --- | --- | --- |
| Versioned migration | PASS — isolated staging | อัปเกรด schema จาก `20260924_01` เป็น `20260927_02`; downgrade/re-upgrade และ `alembic check` ผ่าน; synthetic session/account/job เดิมยังอยู่ และ `updated_at` backfill จาก `created_at` ถูกต้อง ไม่มีการใช้ `alembic stamp` หรือแก้ production DB |
| PostgreSQL backup/restore | PASS — isolated PostgreSQL 17 | [PG17 restore drill](2026-09-27-pg17-restore-drill.md): backup 04:31:46–04:31:47 UTC, restore 04:31:47–04:31:49 UTC, synthetic RPO 0 วินาที, measured RTO 2 วินาที; แถว synthetic session/OAuth/account/media/job อย่างละ 1 และ decrypt access/refresh token สำเร็จด้วย Fernet key ที่เก็บแยกแล้วลบทิ้ง |
| Image/schema rollback | PASS — isolated staging | 2026-09-27 04:48:17–04:48:23 UTC; known-good image `sha256:499938cc484581d4a351bf3f6ad0935d1f9c6874096f43ecdf0b475c54e4a6e5`; ก่อน migration `/health/ready` = HTTP 200, หลัง upgrade schema image เดิมตอบ 503 ตาม fail-closed guard, หลัง restore backup ก่อน migration กลับมา HTTP 200; restore จนพร้อมใช้ 6.5 วินาที; backup SHA-256 `3770e0bf44fce0f20dad6d47ad8d7fc27e01f92608511b83c23fe7d7798d35ed` |
| Retention/deletion | PASS — isolated synthetic runtime | Scheduler ลบข้อมูลสังเคราะห์ที่หมดอายุ ได้แก่ browser session พร้อม ciphertext/job/media/uploads ที่เชื่อมโยง, OAuth record, media, terminal job และ quota bucket; path guard และการแยก failure ตอน unlink ทดสอบด้วย automated tests |
| Request/upload quotas | PASS — local tests + PostgreSQL staging | ตรวจ quota ต่อ session, publish/creator-info, จำนวน uploads ต่อวันและ bytes ต่อวัน; HTTP 429/`Retry-After` และ PostgreSQL upsert มี regression coverage |
| Media/caption/provider validation | PASS — local tests | MP4 duration parser, creator-info duration guard, caption UTF-16 limit 2,200 code units และการแปลง HTTP/provider rate-limit error ครอบคลุมใน tests; เป็นผลจาก parser/test double ไม่ใช่ TikTok upstream |
| Metrics access | PASS — local staging smoke | `/metrics` ตอบ 404 หากไม่มี bearer token และ 200 เมื่อส่ง token ถูกต้อง; การส่ง alert ไปยัง receiver จริงยังไม่ทดสอบ |
| Alert rule logic | PASS — synthetic Prometheus unit tests | `promtool 2.37.0 check rules` พบ 8 rules และ `promtool test rules` ผ่านการยิง synthetic threshold ครบทั้ง 8 alert; Prometheus scrape, Alertmanager routing และ receiver จริงยังไม่ทดสอบ |
| Readiness soak | PASS — local 5 นาที | PostgreSQL-backed `/health/ready` เท่านั้น; 300 วินาที, concurrency 8, 32,645 requests, 108.82 req/s, HTTP 200 ทั้งหมด, p50 63.21 ms, p95 141.1 ms, p99 224.98 ms |
| Automated checks | PASS — local | Python 66 passed; Node 5 passed; Ruff check/format check, `bash -n scripts/restore_drill.sh` และ `git diff --check` ผ่าน |

การตรวจ production database แบบอ่านอย่างเดียวพบ Alembic revision `20260924_01` อยู่แล้ว จึงไม่พบ pre-Alembic database ที่ต้องเทียบ schema หรือ stamp; ไม่มี migration หรือ stamp เกิดขึ้นกับ production

### ข้อจำกัด rollback

image เดิมไม่รองรับ schema head ใหม่และตอบ 503 โดยตั้งใจ จึงต้องใช้ migration-compatible release หรือกู้ backup schema เดิมก่อนเปิด image เดิม การซ้อมนี้ยืนยันขั้นตอน restore-to-old-schema แล้วค่อยเปิด image เดิมได้ใน isolated staging เท่านั้น; ไม่มี production rollback/deploy เกิดขึ้น

## ยังไม่ผ่าน / ยังไม่มีหลักฐานภายนอก

| Gate | สถานะ | สิ่งที่ยังขาด |
| --- | --- | --- |
| TikTok Login Kit, Draft และ Direct Post E2E | BLOCKED_EXTERNAL | ยังไม่มี authorized TikTok Sandbox account/recording; ห้ามนับ MockTransport หรือ live website smoke เป็น upstream proof |
| TikTok upstream rate limits และ creator permissions | BLOCKED_EXTERNAL | ต้องทดสอบ 429/`Retry-After`, creator duration/privacy options และ upload/status reconciliation กับ TikTok จริง |
| TikTok production approval/audit | BLOCKED_EXTERNAL | unaudited client ยังมีข้อจำกัด private-only; ห้ามกล่าวว่า approved หรือเปิด public Direct Post |
| Production Fernet key escrow/rotation/recovery | NOT_TESTED | การซ้อมใช้ key สังเคราะห์ที่เก็บแยกและทำลายหลังทดสอบ ไม่ได้พิสูจน์ backup ของ production key |
| Alert receiver, paging และ incident response | NOT_TESTED | ยืนยัน endpoint metrics ภายในเท่านั้น; ยังไม่มีหลักฐานว่าระบบ monitoring ภายนอกรับและส่ง alert ได้ |
| Public workload/load/soak | NOT_TESTED | readiness soak ไม่ได้ครอบคลุม OAuth, uploads, TikTok, concurrent tenants หรือ traffic ผ่าน public edge |
| Legal review และ main branch/CI/security gates | NOT_TESTED | ต้องแนบหลักฐานจากผู้ตรวจและ GitHub checks ปัจจุบันก่อนปิด gate |

## TikTok product และ scope ที่ใช้งานจริง

เว็บปัจจุบันขอเฉพาะ `user.info.basic`, `video.upload`, `video.publish` ตาม UI และ API ที่ implement แล้ว ส่วน `user.info.profile`, `user.info.stats`, `video.list` ยังไม่มี feature ใช้งาน และ Share Kit เป็น mobile sharing product; zTTato ปัจจุบันเป็น web flow ที่ใช้ Content Posting API จึงยังไม่เปิด scopes/products ส่วนเกินใน OAuth request. ตรวจรายการและคำอธิบายกับ [TikTok Scopes Reference](https://developers.tiktok.com/docs/en/tiktok-api-scopes), [Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post), [Query Creator Info](https://developers.tiktok.com/docs/en/content-posting-api-reference-query-creator-info) และ [Share Kit](https://developers.tiktok.com/products/share-kit).

## Runtime configuration boundary

ไฟล์ `.env.sandbox` และ `.env.production` ได้รับค่า quota, retention, cleanup interval และ metrics bearer token สำหรับ configuration ในแต่ละ profile โดยคง credentials/scopes เดิมไว้; permissions ถูกจำกัดเป็น owner-only (`0600`). Compose ปัจจุบันอ่าน `.env` แยกต่างหาก ดังนั้นการแก้ profile files นี้ไม่ได้เปลี่ยนหรือ restart runtime ที่กำลังทำงาน

เวลา `2026-09-27T05:53:46Z` หมุนเฉพาะ `METRICS_BEARER_TOKEN` ใน `.env.sandbox`; ค่าเก่าและค่าใหม่ไม่ถูกบันทึกในหลักฐานนี้ ส่วน encryption key, PostgreSQL/TikTok credentials และ `.env.production` ไม่เปลี่ยน การหมุนนี้ไม่เปลี่ยน runtime เพราะ Compose ไม่ได้อ่าน `.env.sandbox` โดยตรง

## Addendum — implementation verification 2026-09-27 13:21 UTC

ผลส่วนนี้ตรวจจาก dirty local worktree ที่ `HEAD=268d9a77e4f74f86194732024f0e8d740b87e8df`; commit SHA เป็นฐานก่อนการแก้ไขชุดนี้ และไม่ได้แทน source revision ที่ deploy แล้ว

| Check | Result | Evidence |
| --- | --- | --- |
| Python/frontend checks | PASS — local | `make lint`; `make test`: Python 92 passed, Node 10 passed. มี Starlette/httpx deprecation warning หนึ่งรายการ |
| Dependency audit | PASS — local | `make security`: `pip-audit --strict --requirement requirements-dev.txt`; no known vulnerabilities |
| Container build | PASS — isolated candidate | `zttato-sandbox-app` และ `zttato-sandbox-migrate` สร้างจาก worktree โดยไม่เปลี่ยน image/container ที่ให้บริการพอร์ต 8000 |
| Isolated Compose sandbox | PASS — local only | project `zttato-sandbox`; PostgreSQL 17.11, DB/media volume แยก, app `127.0.0.1:8001`, DB `127.0.0.1:55432`; Tiktok และ zWallet credentials ว่าง; callback canary ได้ HTTP 401 และไม่ปรากฏใน container logs |
| Latest migration | PASS — isolated PG17 | schema `20260927_04`, `/health/ready` HTTP 200; production Compose DB ไม่ถูกแก้ |
| Latest backup/restore | PASS — isolated PG17 | [restore drill](2026-09-27-pg17-restore-drill.md): เสร็จ `13:21:22Z`, synthetic RPO 0 วินาที, measured RTO 4 วินาที; session/OAuth/account/media/job อย่างละหนึ่งแถว, decrypt token สำเร็จด้วย key ที่ escrow แยก, HMAC/consent/encrypted request restored |
| Durable publish queue | PASS — synthetic tests | queued job survive application startup/recovery, row claim, same-key replay, failure reconciliation; ไม่เรียก TikTok จริง |
| Metrics/alerts | PASS — local synthetic | Prometheus พบ 9 rules และ `promtool test rules` ผ่าน; queue depth/age metric มี unit coverage |

การตรวจ runtime แบบ read-only หลัง rehearsal พบ Compose app ที่กำลังทำงานยังใช้ image `sha256:499938cc484581d4a351bf3f6ad0935d1f9c6874096f43ecdf0b475c54e4a6e5` และ DB revision `20260924_01`; ไม่มีการ deploy, migrate, restart หรือเปลี่ยนไฟล์ production env ในงานนี้. Sandbox image/schema `20260927_04` ไม่ถูกนำไปแทน runtime. Restore/rollback drill เดิมในตารางด้านบนครอบคลุม schema `20260927_02`; restore ของ head `20260927_04` ผ่านแล้ว แต่ rollback ด้วย known-good image สำหรับ head 04 ยังไม่ได้ทำซ้ำ.

## Remaining release blockers

- `BLOCKED_EXTERNAL`: TikTok authorized Sandbox E2E, live creator-info permissions, actual upstream rate-limit/timeout-after-success reconciliation, and TikTok app audit/production approval.
- `NOT_TESTED`: public edge OAuth query redaction, Alertmanager receiver/paging, production encryption-key escrow/rotation, legal counsel review, and public workload/load soak.
- `NOT_TESTED`: live GitHub CI/security checks for this dirty worktree. Code-scanning alert #4 cannot close until the fix is committed/pushed and a new hosted scan runs.
- `NOT_TESTED`: canonical Affiliate Core and Commerce Sources bounded contexts are not implemented in this Creator-focused application; do not claim the larger platform architecture is complete.
- User boundary held: no ad-budget, payment, zWallet, TikTok publish, credential rotation, production migration, commit/push, or production deployment occurred.
