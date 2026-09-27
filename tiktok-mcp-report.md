# รายงานตรวจ TikTok Sandbox, Content Posting API และ Business MCP

**วันที่ตรวจ:** 2026-09-27 UTC  
**Repository:** zttato-platform  
**สภาพแวดล้อมที่ใช้:** local tests บน SQLite in-memory; อ่าน `.env.sandbox` แบบไม่แสดงค่า secret; ตรวจภาพ OAuth ที่ผู้ใช้ให้; Codex MCP catalog เป็น metadata snapshot เดิม  
**คำตัดสิน:** PARTIAL / NOT READY — ภาพแสดง TikTok Developer Sandbox consent และหน้าบัญชีที่เชื่อมแล้ว แต่ไม่มี upstream Content Posting API test ในรอบนี้; Photo Post ปิดอยู่เพราะ Sandbox ไม่มี URL prefix ที่ TikTok ยืนยัน; Ads MCP ยังไม่มีการยืนยัน Sandbox advertiser

## 1. สรุปสำหรับผู้ดูแล

ตรวจเอกสาร TikTok API for Business v1.3 และ TikTok for Developers Content Posting API, ตรวจภาพ Sandbox OAuth ที่ผู้ใช้ให้, ตรวจ `.env.sandbox` โดยไม่พิมพ์ค่า secret, ตรวจ source code และรัน automated tests ด้วย APP_ENV=test และ SQLite in-memory แยกจาก runtime ที่ตั้ง APP_ENV=production

ผลที่ยืนยันได้:

- เอกสารทางการมีหน้า Sandbox accounts สำหรับทดสอบโดยไม่กระทบ production และมีเอกสาร TikTok for Business MCP แยกต่างหาก
- MCP metadata discovery รอบก่อนหน้ารายงาน server **tiktok-ads** enabled, OAuth, endpoint `https://business-api.tiktok.com/open_mcp/tt-ads-mcp-flat`
- MCP catalog snapshot รอบก่อนหน้ารายงาน server **tiktok_ads** และ 380 tool definitions; ไม่ได้ enumerate ใหม่ในรอบนี้
- Screenshot evidence แสดงชื่อแอป Sandbox, หน้า consent ที่ขอ `user.info.basic`, `video.publish`, `video.upload` และ dashboard ที่แสดงบัญชีเชื่อมพร้อม scopes เหล่านั้น
- `.env.sandbox` ระบุ `APP_ENV=sandbox` แต่ยังไม่มี `TIKTOK_VERIFIED_MEDIA_URL_PREFIXES`; ไม่แสดงหรือแก้ค่า credential ใด
- Latest local test suite ผ่าน Python 86 tests และ frontend 10 tests; ชุด TikTok transfer tests ผ่าน 24 tests ในรอบตรวจเดิม; lint ผ่านใน follow-up ล่าสุด
- Photo Post UI/API flow ถูกต่อเข้าด้วยกันใน worktree และมี mock tests; ยังไม่ใช่ผลทดสอบ TikTok Sandbox
- Source ของ zTTato ยังไม่มี TikTok Ads MCP client, route, tool registry หรือ advertiser model

สิ่งที่ยังยืนยันไม่ได้:

- OAuth connection ปัจจุบันชี้ไป Sandbox advertiser หรือ production advertiser
- Sandbox รองรับ tool/API ทุกตัวใน MCP catalog หรือมีข้อจำกัดราย feature อย่างไร
- ผลการเรียก TikTok API จริงสำหรับทุก feature
- สิทธิ์และ feature availability ของ advertiser ใดในบัญชี
- target user ในภาพถูกเพิ่มไว้ในรายชื่อ Sandbox target users ใน Developer Portal หรือไม่
- TikTok Creator Info, transfer, draft inbox, Direct Post, upstream status และผลของ API ที่เคยตอบ 502
- TikTok Developer URL property ที่ยืนยันแล้วสำหรับดึงภาพ Photo Post

ไม่มีการเรียก advertiser/account data, campaign, audience, report, payment, spend, budget หรือ transaction tool ไม่มีการเปลี่ยนงบหรือเงิน และไม่มีการโพสต์คลิป

## 2. ขอบเขตความปลอดภัยและนโยบายเงิน

ข้อกำหนดจากเจ้าของระบบที่ใช้กับรายงานและขั้นตอนทดสอบ:

1. ทดสอบเฉพาะ Sandbox ที่พิสูจน์ได้ว่าไม่ใช่ production
2. ห้ามแก้ budget, bid, spend, pacing, payment, billing, balance, invoice, credit line หรือ transaction
3. ห้ามเรียกเครื่องมือทางการเงินที่ให้รายละเอียดมากกว่ายอดรวมที่อนุญาต
4. อนุญาตให้ดูได้เฉพาะยอดรวมเมื่อมีการร้องขอโดยตรงและนิยาม metric, ช่วงเวลา, currency และขอบเขตบัญชีชัดเจน
5. ในรอบนี้ไม่ได้อ่านยอดเงินแม้แต่ยอดรวม
6. ห้ามให้ LLM หรือ user confirmation ข้ามข้อห้ามด้านบน

การตรวจ schema ของ MCP เป็น metadata discovery เท่านั้น ไม่ใช่การเรียก Business API feature บน advertiser

## 3. ยืนยัน Sandbox และ MCP connection

### แหล่งข้อมูลทางการ

- [TikTok API for Business Sandbox accounts](https://business-api.tiktok.com/portal/docs/sandbox-accounts/v1.3) ระบุใน metadata ของหน้าเอกสารว่า Sandbox ใช้ทดสอบ integration โดยไม่กระทบ production
- [TikTok API for Business guide](https://business-api.tiktok.com/portal/docs/about-the-guide/v1.3) จัดหัวข้อ Sandbox accounts และ TikTok for Business MCP Server ไว้แยกกันใน guide
- [TikTok for Business MCP overview](https://ads.tiktok.com/resources/help/article/about-tiktok-for-business-mcp-server?lang=en-GB) ระบุ full-disclosure server โหลดเครื่องมือประมาณ 400 รายการ และ progressive server โหลดเครื่องมือหลักประมาณ 40 รายการก่อนค้นหาเพิ่ม
- หน้ารายละเอียด Sandbox เป็น client-rendered; การตรวจแบบไม่ login ยืนยันชื่อหน้าและคำอธิบาย แต่ไม่ได้ยืนยันรายการ API หรือ tool ที่ sandbox รองรับทั้งหมด

### หลักฐาน connection ที่ตรวจ

| รายการ | ผล |
|---|---|
| Codex MCP metadata (prior snapshot) | tiktok-ads แสดงสถานะ enabled |
| Authentication mode | OAuth ใน snapshot ก่อนหน้า |
| Endpoint | `https://business-api.tiktok.com/open_mcp/tt-ads-mcp-flat` ตาม snapshot ก่อนหน้า |
| MCP tool catalog metadata | Codex CLI เคยรายงาน tiktok_ads catalog 380 tools; ไม่ได้ enumerate ซ้ำใน Sandbox pass นี้ |
| Sandbox marker ใน endpoint/config ที่แสดง | ไม่พบใน snapshot ก่อนหน้า |
| Advertiser/Sandbox account identity | ไม่ได้อ่าน |
| Business operation | ไม่ได้เรียก |
| Money data | ไม่ได้อ่าน |

จำนวน 380 เป็น snapshot จาก MCP discovery ก่อนหน้า ไม่ใช่การ enumerate รอบนี้ ไม่ใช่หลักฐานว่าเครื่องมือทั้งหมดเปิดใช้กับ advertiser จริง และไม่ใช่หลักฐานว่า connection อยู่ใน Business Sandbox เอกสารภาพรวมระบุจำนวนโดยประมาณ จึงอาจต่างกันตาม catalog revision และการเปิดเผยเครื่องมือ

**Sandbox binding status: BLOCKED / UNVERIFIED.** Endpoint ที่ลงทะเบียนเป็น official MCP endpoint แต่ไม่ได้ระบุ Sandbox advertiser ในชื่อหรือ URL การล็อกอิน OAuth เพียงอย่างเดียวพิสูจน์ไม่ได้ว่า advertiser ที่ถูกผูกเป็น Sandbox ดังนั้นห้ามใช้ connection นี้ทำ business feature tests จนกว่าจะยืนยัน Sandbox binding ได้

## 4. ผลการทดสอบ local

### คำสั่งที่รัน

- `APP_ENV=test make test`
- `APP_ENV=test make test-tiktok`
- `APP_ENV=test make lint`
- `APP_ENV=test make i18n-check`
- `python3 -m compileall -q app tests`
- `node --check web/dashboard.js && node --check tests/dashboard.test.mjs`

### ผล

| Suite | ผล | ขอบเขต |
|---|---:|---|
| TikTok transfer regression | PASS — 24 passed | MockTransport และไฟล์ fixture ในเครื่อง |
| Python backend suite | PASS — 85 passed | SQLite in-memory; provider transport ใช้ mocks |
| Frontend Node suite | PASS — 9 passed | local DOM/fetch fixtures รวม Photo Post draft/direct และ consent guards |
| Chrome browser smoke | PASS — disconnected test dashboard | Local `APP_ENV=test` server; `/dashboard`, static assets, English locale และ `/api/session` ตอบ 200; ไม่กด OAuth และไม่เชื่อม TikTok |
| รวม full suite | PASS — 94 tests | 85 backend + 9 frontend |
| Ruff lint/format | PASS | `ruff check` และ `ruff format --check` |
| i18n parity | PASS — 4 passed | locale structure/key checks |
| Compile/syntax | PASS | Python `compileall`; Node syntax checks |
| Warnings | 1 warning | Starlette แจ้ง deprecation เรื่อง httpx TestClient |

การรัน `pytest` ตรงครั้งแรกรับ `DATABASE_URL` จาก host และหยุดตอน collection เพราะ DSN มี option `schema` ที่ driver ในเครื่องไม่รู้จัก; การทดสอบซ้ำผ่าน Makefile พร้อม `APP_ENV=test` บังคับ SQLite in-memory และผ่านครบ โดยไม่เชื่อมฐานข้อมูลเดิม โค้ด test ใช้ `httpx.MockTransport` สำหรับ TikTok และ zWallet; ไม่มี TikTok Sandbox HTTP request เกิดขึ้น ผลนี้พิสูจน์ local behavior เท่านั้น

### Feature behavior ที่ชุด local tests ครอบคลุม

- วางแผน upload chunk ตามขนาดไฟล์และขอบเขตที่ TikTok กำหนด
- ปฏิเสธขนาดไฟล์ผิดเงื่อนไขและ upload URL ที่ไม่อยู่ใน allowlist
- ตรวจ streaming chunk, Content-Range และการตอบรับ HTTP 206/201
- หยุดเมื่อ acknowledgement ผิดรูปแบบและต้อง reconcile ก่อน retry
- จำกัด User Info request ให้ขอเฉพาะ basic profile fields
- ทดสอบ provider error, idempotency, quota, consent/validation, OAuth/session และ media behavior ผ่าน mock/local paths

พฤติกรรม live ได้แก่ TikTok rate limits, creator permissions, upload URL region, real draft inbox, Direct Post และ publishing status ยังต้องมีหลักฐาน Sandbox จริง

## 5. ความสามารถ TikTok ใน zTTato source ปัจจุบัน

รายการนี้อิง source/worktree ที่ตรวจ ไม่ได้ยืนยันว่าถูก deploy หรือผ่าน TikTok Sandbox

| ความสามารถ | สถานะจาก source | หลักฐาน/ข้อจำกัด |
|---|---|---|
| TikTok Login Kit OAuth | IMPLEMENTED | callback/state flow; token lifecycle อยู่ฝั่ง server |
| Basic profile | IMPLEMENTED | user.info.basic; ขอ open_id, display_name, avatar_url |
| Creator info | IMPLEMENTED | ใช้ตรวจ privacy, interaction controls และ duration ก่อน Direct Post |
| MP4 upload to workspace | IMPLEMENTED | web upload มีเพดาน 64 MiB ในปัจจุบัน |
| Draft video | IMPLEMENTED | ส่งเข้า TikTok inbox ผ่าน video.upload |
| Direct Post video | IMPLEMENTED | consent แยกชัดเจน; ใช้ video.publish; unaudited app จำกัด visibility ตาม TikTok |
| Photo Post | LOCAL IMPLEMENTATION UPDATED; UPSTREAM BLOCKED | ต่อ dashboard กับ `/api/media/photo` และ `media_type=photo`; ตรวจ HTTPS/JPEG/WebP/35 URLs/cover/verified prefix; fail-closed เมื่อไม่มี verified prefix; ไม่มี live Sandbox evidence |
| Caption validation | IMPLEMENTED | ตรวจ UTF-16 code units ตาม limit ใน code |
| Creator-duration validation | IMPLEMENTED | ใช้ creator-info ล่าสุดก่อนเริ่ม video publish |
| Publishing status | IMPLEMENTED | เก็บ job และ query status เมื่อมี provider publish ID |
| Idempotency | IMPLEMENTED | unique session/idempotency key ลด duplicate init |
| PULL_FROM_URL | LOCAL PHOTO FLOW; UPSTREAM BLOCKED | Photo source ใช้ PULL_FROM_URL; URL ต้องอยู่ใต้ TikTok verified property; API pull, redirect/accessibility, image size/dimension และ status ยังไม่ทดสอบจริง |
| Ads MCP | NOT IMPLEMENTED IN ZTTATO | ไม่มี MCP client, route, schema registry, advertiser model หรือ Ads dashboard |
| Ads reporting | NOT IMPLEMENTED IN ZTTATO | ไม่มี report UI หรือ adapter |
| TikTok Ads campaign/audience/finance control | NOT IMPLEMENTED IN ZTTATO | ไม่ควรเพิ่มโดยผูกเข้ากับ creator publishing |

OAuth scopes ที่เอกสาร zTTato ระบุว่าใช้อยู่คือ user.info.basic, video.upload, video.publish ส่วน user.info.profile, user.info.stats, video.list และ Share Kit ไม่ได้ร้องขอใน web app ปัจจุบัน

## 6. TikTok Business API/MCP feature inventory

รายการต่อไปนี้คือ feature families ที่ปรากฏใน TikTok API for Business guide และ MCP documentation/catalog ที่ค้นได้ ไม่ใช่ผลทดสอบการทำงานของ sandbox และไม่ยืนยันว่า 380 tools ทุกตัวเปิดให้ connection นี้

| Feature family | ตัวอย่างความสามารถในเอกสาร | ประเภท/ความเสี่ยง | ผลการตรวจรอบนี้ |
|---|---|---|---|
| Tool discovery และ skill management | MCP connection, tool discovery, skill upload/publish ตามเอกสาร MCP/API guide | Metadata/read; skill publishing มี side effect | ตรวจ catalog count เท่านั้น; ไม่เรียก tool |
| Authentication/terms | Developer app authorization, access/refresh token, terms check/confirm | Sensitive authorization | เอกสารตรวจ; ไม่เปลี่ยน authorization |
| Advertiser accounts | ดูบัญชีและสิทธิ์ที่ authorize | Read แต่เปิดเผย identity/account metadata | ไม่อ่าน advertiser list |
| Campaign management | Campaign CRUD, status, targeting, objective และ optimization types | Write; อาจมี budget/spend impact | ไม่เรียกทุกกรณี |
| Ad groups | อ่าน/สร้าง/แก้ไข/ปิดใช้งาน ad group, placement และ targeting | Write; budget/bid settings อาจอยู่ใน payload | ไม่เรียก |
| Ads and creatives | สร้าง/แก้ไข/ปิด ads, identity/Spark Ads, previews, image/video/music/playable assets | Write; publish/delivery impact | ไม่เรียก |
| Smart+/Search/Shopping/GMV Max/Reach & Frequency | Campaign variants และ ad formats | Write; อาจเริ่ม delivery หรือใช้เงิน | ไม่เรียก |
| Reporting | Sync/async reports, status/download, metric/dimension customization | Read; ข้อมูลอาจมี spend/budget/cost | ไม่อ่าน report; ยอดเงินยังไม่ถูกร้องขอ |
| Diagnostics and review | Ad review, rejection appeal, diagnosis, recommendations, estimates | Read บางส่วน; appeal/change อาจเป็น write | ไม่เรียก |
| Automated rules and split tests | Create/update/enable/bind rules; split-test configuration | Write; automated rules อาจปรับ campaign/budget | ไม่เรียก |
| Audience management | Custom/customer-file/rule/lookalike/saved audiences, share/refresh/delete/apply | Sensitive personal/audience data; write | ไม่เรียก |
| Business Center members/partners/assets | Assign/unassign assets, users, partners, advertiser account links | Organization-wide write | ไม่เรียก |
| Catalog, product feed, TikTok Shop | Catalog/product/product-set/store management and shopping ads | Write; commerce changes | ไม่เรียก |
| Finance and payment | Balances, budget allocations, cost/transaction records, payment portfolios, payment processing, credit lines | Money-changing or detailed financial data | Deny; ไม่มีการเรียกหรืออ่าน |
| Ad measurement and events | Pixel, web/app/offline/CRM events, attribution and conversions | Data transfer and privacy/legal risk; write | ไม่เรียก |
| Lead generation | Lead forms, lead reads/export, CRM postbacks | Personal data and data-transfer risk | ไม่เรียก |
| Messaging | Business direct messages, welcome/auto messages, suggested prompts | User communication; write/send side effect | ไม่เรียก |
| Organic Accounts API | Profile/post insights, comments moderation, video/photo publishing, ad authorization | Separate product and permission set; posting/moderation writes | ไม่เรียก Ads MCP tool |
| Mentions, Discovery, TikTok One | Mention discovery, creator/campaign linking, creator insights and authorization | Separate API products, consent and policy boundaries | ไม่เรียก |
| Webhooks/subscriptions | Ad/account/comment/posting/mention event subscriptions and delivery | External callback and persistent side effect | ไม่สร้าง subscription |

TikTok for Business MCP เป็นทางเข้า agent สำหรับเครื่องมือ Ads โดยตรง ส่วน TikTok Login Kit/Content Posting API ของ zTTato เป็น creator publishing integration คนละ OAuth/product boundary ห้ามรวม token หรือ authorization path เข้าด้วยกัน

## 7. Coverage matrix: อะไรผ่านและอะไรยังไม่ผ่าน

| Gate | สถานะ | เหตุผล |
|---|---|---|
| อ่านเอกสาร Sandbox v1.3 | PASS — documentation discovery | พบหน้า Sandbox accounts และคำอธิบายว่าทดสอบได้โดยไม่กระทบ production |
| ระบุ MCP endpoint ที่ตั้งค่า | PASS — configuration metadata | พบ tt-ads-mcp-flat พร้อม OAuth |
| Enumerate tool catalog metadata | PARTIAL PASS | MCP runtime แจ้ง 380 tools; ไม่มีการเรียก business method; การจัดหมวดครบราย tool ยังไม่เสร็จ |
| ยืนยัน Sandbox advertiser binding | BLOCKED_EXTERNAL | ไม่มีหลักฐานใน endpoint/config/session metadata ที่ยืนยัน account mode |
| Login และ account discovery บน Sandbox | NOT RUN | ไม่อ่านข้อมูลบัญชีเพื่อหลีกเลี่ยงการเรียกผิด environment |
| Sandbox campaign/report tests | NOT RUN | ยังไม่มี verified Sandbox target; report อาจมี monetary detail |
| Sandbox audience/creative/catalog/message tests | NOT RUN | ไม่มี verified target และมี data/side-effect risks |
| Sandbox finance/payment tests | FORBIDDEN | อยู่นอกขอบเขตเงินที่ผู้ใช้อนุญาต |
| zTTato local test suite | PASS | Python 85 + frontend 9; mocks/fixtures only |
| Sandbox OAuth consent and return | PASS — screenshot evidence | app name indicates Sandbox; dashboard displays connected profile and granted scopes; no tokens were inspected |
| Sandbox target-user portal membership | NOT VERIFIED | ไม่มีภาพ/ผลตรวจจาก Developer Portal รายชื่อ Target users |
| Creator info and real video/photo transfer | NOT TESTED | ไม่มี request ไป TikTok ในรอบนี้; screenshot checkbox ยังไม่ consent |
| Sandbox verified photo URL property | BLOCKED_EXTERNAL | `TIKTOK_VERIFIED_MEDIA_URL_PREFIXES` ว่างใน `.env.sandbox` |
| TikTok upstream E2E | NOT TESTED | local mocks ไม่ใช่ upstream evidence |

## 8. ข้อสรุปเกี่ยวกับคำขอ “ตรวจ all feature”

ทำได้แล้วในรอบนี้:

- ตรวจ official Business API Sandbox documentation entry และแยกจาก TikTok for Developers app sandbox
- ยืนยันด้วยภาพว่าหน้า TikTok consent ระบุแอป Sandbox และกลับมาที่ dashboard ซึ่งแสดง connected account/scopes
- ตรวจค่า environment ของ `.env.sandbox` แบบไม่แสดง credential และพบว่า photo URL property ยังไม่ตั้ง
- อ้างอิง endpoint/OAuth metadata และ 380 MCP tool definitions จาก MCP discovery รอบก่อนหน้า; ไม่ได้เรียก Business API operation หรือ enumerate ใหม่ในรอบนี้
- ทำ capability-family inventory จาก official TikTok docs
- แก้ source/UI ที่เคยแสดง i18n key ดิบและ Photo Post ซึ่งยังไม่มี event flow; เพิ่ม validation และ fail-closed URL property gate
- รันทดสอบ local code ทั้ง backend และ frontend โดยทุก provider transport ใช้ mocks; lint, locale parity และ syntax ผ่าน

ยังทำไม่ได้อย่างปลอดภัย:

- Functional test ของทุก MCP tool บน TikTok Sandbox เพราะยังยืนยัน Sandbox advertiser binding ไม่ได้
- ทดสอบการเงินหรือ budget writes เพราะถูกห้ามโดยขอบเขตงาน
- ยืนยันว่า sandbox รองรับทุก API/MCP tool; หน้า TikTok Sandbox body render ด้วย JavaScript และรายการ supported endpoints ยังไม่ได้หลักฐานครบ
- อ้างว่าผล catalog inventory เท่ากับ production readiness หรือ TikTok approval
- ส่ง content เพื่อสร้าง draft หรือ post: ภาพหลัง OAuth ยังไม่เลือก consent และ config ที่ใช้กับ runtime ปัจจุบันเป็น production; ไม่มีการเรียก TikTok API

## 9. ขั้นตอนถัดไปที่ต้องผ่านก่อน functional Sandbox tests

1. สร้างหรือเลือก Sandbox account/advertiser ใน TikTok API for Business portal ตามเอกสารทางการ และยืนยันชื่อ/ID ในช่องทางที่ผู้ใช้ควบคุม โดยไม่ส่ง secrets ในแชต
2. จัด OAuth connection แยกที่ผูกกับ Sandbox advertiser นั้นอย่างชัดเจน; อย่าใช้ connection ชื่อ tiktok-ads ที่มีอยู่จนกว่าจะยืนยัน environment ได้
3. เริ่มจาก MCP metadata/tool list เท่านั้น แล้วบันทึกชื่อ, input schema, read/write classification, data class, และ side effects ของ tools ทั้ง 380 รายการ
4. สร้าง test allowlist สำหรับ sandbox read/validation operations; ปิด default ทุก write, campaign, budget, bid, payment, transaction และ finance detail
5. ทดสอบ feature ที่อนุญาตบน Sandbox ทีละหมวด ใช้ synthetic test assets/accounts, เก็บ request IDs/status โดยไม่เก็บ tokens หรือ monetary detail
6. ตรวจผล sandbox หลังแต่ละ operation และลบเฉพาะ test objects ที่สร้างไว้เมื่อเป็น operation ที่ผู้ใช้อนุญาตและไม่กระทบเงิน
7. แนบ timestamped evidence และใช้ gate labels PASS, FAIL, BLOCKED_EXTERNAL, NOT_TESTED หรือ NOT_APPLICABLE

หากบัญชี Sandbox ไม่มีสิทธิ์แยกจาก live advertiser หรือ MCP ไม่เปิด routing/identity ที่ยืนยันได้ ให้คงสถานะ BLOCKED_EXTERNAL และใช้ mock/local tests ต่อไป

## 10. ข้อจำกัดและความน่าเชื่อถือของหลักฐาน

- จำนวน 380 tools เป็น tool-catalog metadata ที่รายงานโดย Codex MCP session ไม่ได้ยืนยัน feature availability หรือ entitlement
- MCP catalog audit แบบละเอียดราย tool ถูกหยุดก่อนสรุปครบ จึงไม่ควรถือว่า inventory ในตารางนี้เป็นรายการครบทุก function name/schema
- Official MCP overview ระบุจำนวนโดยประมาณ จึงไม่จำเป็นต้องเท่ากับจำนวน tool definitions ใน session
- TikTok source pages บางส่วนเป็น JavaScript-rendered; metadata/official search index ช่วยระบุหัวข้อ แต่ไม่ใช้แทนการตรวจ API response จริง
- Full local tests ไม่ใช่ TikTok Sandbox proof; ไม่มี TikTok OAuth/API business request เกิดขึ้นจากชุดทดสอบ
- Repository มี pre-existing user changes บน branch main. รายงานนี้เป็นไฟล์ใหม่เท่านั้น; ไม่แก้หรือ stage การเปลี่ยนแปลงเดิม

## 11. Sources

- TikTok API for Business, [About the Guide v1.3](https://business-api.tiktok.com/portal/docs/about-the-guide/v1.3)
- TikTok API for Business, [Sandbox accounts v1.3](https://business-api.tiktok.com/portal/docs/sandbox-accounts/v1.3)
- TikTok API for Business, [TikTok for Business MCP Server guide](https://business-api.tiktok.com/portal/docs/tiktok-ads-mcp-server/v1.3)
- TikTok API for Business, [Available tools in TikTok for Business MCP Server](https://business-api.tiktok.com/portal/docs/available-tools-in-tiktok-for-business-mcp-server/v1.3)
- TikTok for Business, [MCP server overview](https://ads.tiktok.com/resources/help/article/about-tiktok-for-business-mcp-server?lang=en-GB)
- TikTok for Developers, [Add a Sandbox](https://developers.tiktok.com/docs/en/add-a-sandbox)
- Repository evidence: README.md, docs/PRODUCTION_GATES.md, docs/TIKTOK_REVIEW.md, docs/TIKTOK_MEDIA_TRANSFER.md, docs/evidence/2026-09-27-release-gates.md, app/tiktok.py, app/main.py, tests/test_tiktok_transfer.py

## 12. TikTok Developer Sandbox: ภาพหลักฐานและ flow ของ Content Posting API

ส่วนนี้เป็นหลักฐานคนละผลิตภัณฑ์กับ TikTok API for Business/Ads MCP ข้อความ “Sandbox” ในภาพหมายถึงแอป TikTok for Developers ที่ใช้ Login Kit/Content Posting API ไม่ได้ยืนยันว่า OAuth connection `tiktok-ads` ใช้ Business Sandbox advertiser

### 12.1 Screenshot evidence ที่ได้รับ

| ไฟล์ | สิ่งที่เห็น | ขอบเขตของหลักฐาน |
|---|---|---|
| [`before-login.png`](docs/evidence/before-login.png) | หน้าเริ่มต้น zTTato Creator และปุ่มต่อกับ TikTok | ยืนยันหน้าแอปก่อน OAuth เท่านั้น |
| [`after-login.png`](docs/evidence/after-login.png) | TikTok consent page ระบุชื่อแอป Sandbox และแสดง scopes `user.info.basic`, `video.publish`, `video.upload` | ยืนยันหน้าขอ consent และ scopes ที่แสดง ไม่ใช่หลักฐานว่า content ถูกส่ง |
| [`after-accept.png`](docs/evidence/after-accept.png) | dashboard กลับมาหลัง authorization; แสดง connected profile และ granted scopes ตามข้างต้น | เป็น visual evidence ว่า OAuth return และ basic profile/scopes แสดงใน UI; ไม่ยืนยัน creator-info response, token persistence/decryption หรือ API post |

ไม่ได้คัดลอกชื่อ/รูปผู้ใช้จากภาพลงรายงานเพื่อลดข้อมูลส่วนบุคคล ภาพ dashboard เป็นสถานะก่อนแก้ UI: แสดง raw keys เช่น `dashboard.video.drop_title`, `dashboard.video.direct_help`, `dashboard.review.direct`; patch ปัจจุบันเพิ่มข้อความเหล่านี้ครบใน 6 locales และตรวจ parity แล้ว Chrome smoke บน local disconnected dashboard แสดงหน้าและ English locale ได้ แต่ไม่ได้จำลองบัญชี connected จึงยังไม่มี visual recheck ของ form ใน connected dashboard

ภาพหลัง authorization แสดงว่าไม่มี media พร้อมส่งและ checkbox consent ยังไม่เลือก จึงไม่เริ่ม upload, draft หรือ Direct Post ในรอบนี้ ไม่ใช้ภาพเป็นหลักฐานว่า TikTok สร้างโพสต์แล้ว

### 12.2 Sandbox และ runtime config

| Gate | ผล | เหตุผล |
|---|---|---|
| TikTok Developer Sandbox app label | PASS — screenshot | consent UI ระบุชื่อแอป Sandbox |
| OAuth return ไป zTTato dashboard | PASS — screenshot | dashboard ระบุ connected profile และ granted scopes |
| Target user อยู่ใน Sandbox target-user list | NOT VERIFIED | ไม่มีหลักฐานจาก Developer Portal; การตรวจรายการต้องทำใน portal ที่ผู้ใช้ควบคุม |
| `.env.sandbox` mode | PASS — redacted metadata | `APP_ENV=sandbox`; ไม่แสดงค่าหรือคัดลอก client credentials |
| `.env.sandbox` TikTok URL property | BLOCKED_EXTERNAL | `TIKTOK_VERIFIED_MEDIA_URL_PREFIXES` ไม่ได้ตั้งค่า; Photo Post ถูกปิดแบบ fail-closed |
| `.env.production` | UNCHANGED | ไม่เปลี่ยน environment หรือ credentials ในงาน Sandbox นี้ |
| Container API calls | NOT RUN | runtime ที่ตรวจมี `APP_ENV=production`; ใช้เฉพาะ local test process ที่บังคับ `APP_ENV=test` และ SQLite |

TikTok ระบุว่า Developer Sandbox เป็น environment แบบจำกัด และไม่มี Content Posting API สำหรับ public videos หรือ Data Portability API; บัญชีที่จะลอง Sandbox ต้องถูกเพิ่มเป็น target user ก่อน authorize ดังนั้น public Direct Post ไม่อยู่ในขอบเขต Sandbox และการเห็นชื่อ Sandbox บน consent screen ไม่แทนหลักฐาน target-user list จาก Portal ([Add a Sandbox](https://developers.tiktok.com/docs/en/add-a-sandbox)).

### 12.3 Source และ UX audit ที่ทำใน worktree

ภาพ dashboard baseline แสดง Photo Post panel แต่ปุ่มเตรียมภาพยังไม่มี handler เชื่อมกับ API และมี translation key แสดงดิบ การแก้ไข local worktree รอบนี้:

- เชื่อม `Prepare photo set` กับ `/api/media/photo`; เก็บ URL/cover แบบ tenant/session-owned media record และให้ publish payload ระบุ `media_type=photo`.
- ปิด Photo Post เมื่อไม่มี TikTok-verified URL prefix; backend ตรวจ HTTPS, domain, path prefix, extension JPEG/WebP, whitespace, credentials, fragments, จำนวนภาพไม่เกิน 35 และ cover index.
- สร้าง photo Direct Post/UI จาก creator info ล่าสุดและ privacy options ปัจจุบัน; post caption ใส่ `description` ทั้ง `MEDIA_UPLOAD` และ `DIRECT_POST`; ส่ง AI disclosure ที่ระดับ payload; รองรับ paid/own-business disclosure และปิด duet/stitch controls ที่ Photo API ไม่รองรับ.
- ปรับ caption validation ให้ใช้ UTF-16 code units: วิดีโอ 2,200 และ photo 4,000; เพิ่มเพดานวิดีโอที่ developer ส่งผ่าน Content Posting API 600 วินาที พร้อมตรวจ creator cap ล่าสุดก่อน Direct Post.
- อนุญาตการเตรียม Photo Post เมื่อ scope `video.upload` หรือ `video.publish` มีอยู่; mode แต่ละแบบยังบังคับ scope ที่เกี่ยวข้องก่อนส่ง.
- เติม missing video/review i18n keys ครบใน English, Thai, Japanese, Korean, Vietnamese และ Chinese; ปรับ photo disclosure strings ให้เรียก photo post ให้ถูกต้อง.

การตรวจนี้ยืนยัน source-level contract และ mock behavior เท่านั้น TikTok กำหนด Photo Post สูงสุด 35 URLs ที่ publicly accessible และ verify กับ app; Direct Post ต้อง query creator info ล่าสุด; JPEG/WebP สูงสุด 1080p และ 20 MB ต่อภาพ; URL ต้อง HTTPS และไม่ redirect ([Photo API](https://developers.tiktok.com/docs/en/content-posting-api-reference-photo-post), [Media Transfer Guide](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide), [Query Creator Info](https://developers.tiktok.com/docs/en/content-posting-api-reference-query-creator-info)). แอปไม่ได้ดาวน์โหลด remote images เพื่อตรวจขนาด/ความละเอียดเอง จึงต้องพึ่ง TikTok API response สำหรับ validation ที่เหลือ

URL ที่ผู้ใช้แสดงในหน้า Photo Post มี raw whitespace และนามสกุล PNG; source ปัจจุบันปฏิเสธ whitespace และรองรับเฉพาะ JPEG/WebP อีกทั้งแอป mount ไฟล์ `web/` ภายใต้ `/assets`, ไม่ใช่ path root ที่แสดงในตัวอย่าง จึงไม่มีหลักฐานว่า URL นั้นเป็น public media URL ที่เรียกได้ การตรวจนี้ไม่ยิง GET ไปยัง URL ดังกล่าว และไม่เผยแพร่รูปไปยัง TikTok

### 12.4 Verification matrix ของ Content Posting API

| Feature/gate | สถานะ | Evidence |
|---|---|---|
| Login Kit consent screen ใน Sandbox | PASS — screenshot | `after-login.png` |
| OAuth return และ granted scopes ใน dashboard | PASS — screenshot | `after-accept.png` |
| Basic profile display | PASS — screenshot | UI แสดง profile ที่อนุญาต; ไม่เปิดเผย identity ในรายงาน |
| Video workspace upload | PASS — source/local tests | MP4 validation และ file upload tests; ไม่ได้อัปโหลดคลิปไป TikTok |
| Video draft inbox | NOT TESTED | ไม่มี TikTok transfer/status request หรือ consent สำหรับ content |
| Video Direct Post | NOT TESTED | ไม่มี fresh live creator-info call หรือ content consent ในรอบนี้ |
| Photo draft/Direct UI payload | PASS — local mocks | Node flow tests ครอบคลุม draft, direct, consent, captions, disclosure และ publish scope |
| Photo URL prefix enforcement | PASS — local tests | configured-prefix allow/deny, publish-time revalidation และ empty-prefix fail-closed tests |
| Photo URL property in Sandbox | BLOCKED_EXTERNAL | ต้องเพิ่ม/ยืนยัน property ใน TikTok Developer Portal และ config ของ Sandbox |
| TikTok upload/draft/post status | NOT TESTED | status API ไม่ได้เรียก; local tests ใช้ MockTransport |
| Real upstream rate limits / 502 reconciliation | NOT TESTED | ไม่ยิง requests ไป upstream จึงไม่มีหลักฐาน behavior จริง |
| TikTok approval / production readiness | NOT CLAIMED | Sandbox, screenshot และ mock tests ไม่เป็น approval/deployment evidence |

### 12.5 ขั้นตอนถัดไปที่ปลอดภัย

1. ใน TikTok Developer Portal ตรวจว่า authorized target user อยู่ใน Sandbox target-user list; ห้ามส่ง login credentials หรือ token ในแชต.
2. ใน app Sandbox เพิ่ม HTTPS domain/URL prefix ที่ Developer Portal ยืนยันแล้ว และใส่เฉพาะ exact prefix ลง `TIKTOK_VERIFIED_MEDIA_URL_PREFIXES` ใน `.env.sandbox`; อย่าใส่ prefix ที่เดาเองหรือ copy ไป production.
3. ใช้ synthetic non-sensitive JPEG/WebP บน URL ที่ไม่ redirect, เปิดสาธารณะ และคงอยู่ได้ระหว่าง TikTok pull; ทดสอบ preparation/validation ก่อน.
4. หากทดสอบ actual draft/post ให้ผู้ใช้ตรวจ media, caption, account, disclosures และ privacy ทุกครั้ง แล้วให้ explicit consent แยกต่อ content; ใน Sandbox ให้คาดหวัง only-private restrictions และตรวจ status หลังยืนยัน.
5. บันทึก request ID, timestamps, status และ redacted error codes; ไม่เก็บ access tokens, upload URLs, user photos หรือ financial data.
6. ตรวจ Ads MCP แยกจาก Creator API ด้วย Business Sandbox advertiser ที่พิสูจน์ได้; คงทุก tool ที่อาจเขียน campaign/budget/payment/transaction หรืออ่าน finance detail ไว้ปิด.

## 13. ขอบเขตการเปลี่ยนแปลงในรอบนี้

- เปลี่ยนเฉพาะ local source/docs/tests สำหรับ sandbox photo-flow audit; ไม่ stage, commit, push หรือ deploy.
- ไม่แก้ `.env.sandbox` หรือ `.env.production` เพราะยังไม่มี TikTok-confirmed URL property ที่จะตั้งค่า และห้ามเดาค่า config.
- ไม่เรียก TikTok API, Ads MCP business operations, advertiser/campaign/report/finance endpoints; ไม่เปลี่ยน budget หรือเงินจริง.
- Checkout มีการเปลี่ยนแปลงเดิมหลายไฟล์ก่อนงานนี้; รายงานนี้ไม่อ้างว่าทุก dirty file เป็นส่วนของ audit รอบนี้.

## 14. Follow-up: 502 recovery และ PostgreSQL credential (2026-09-27)

- Dashboard จัดการ API error ที่มี `detail` object ได้แล้ว: แสดง job/status ที่ backend บันทึกไว้และเปิดปุ่ม refresh; retry ในหน้าเดิมใช้ idempotency key เดิม. หาก edge proxy ส่ง 502 ที่ไม่มี job metadata ให้คงหน้าเดิมและอย่าสร้างคำขอใหม่ด้วย key ใหม่ก่อนตรวจสถานะ.
- Regression test จำลอง TikTok init ตอบ 502 ก่อนมี `publish_id`: backend บันทึก `INITIATION_UNCERTAIN`, ส่ง `job_id`, คืน job เดิมเมื่อ replay key เดิม และไม่เรียก init ซ้ำ. นี่เป็น MockTransport evidence เท่านั้น ไม่ใช่ผล upstream Sandbox.
- Latest local verification: `APP_ENV=test make test` ผ่าน Python 86 tests และ frontend 10 tests; `APP_ENV=test make lint` ผ่าน. ไม่มี TikTok API หรือ MCP business request ใน follow-up นี้.
- สาเหตุ Compose warning คือ `DATABASE_URL` ถูกประเมินก่อน `POSTGRES_PASSWORD` ในไฟล์ env. หมุน password ของ DB role ที่ใช้งานจริงและปรับลำดับ assignment ใน `.env`, `.env.production`, `.env.sandbox`; ทั้งสามไฟล์ mode `0600`. ไม่บันทึกค่า credential ลงรายงาน.
- Fresh PostgreSQL 17 connection ผ่าน; สร้าง `app` และ `db` containers ใหม่โดยคง PostgreSQL persistent volume และ image เดิม, ยืนยันว่า DB container environment ตรงกับ credential ใหม่, `/health/live` และ `/health/ready` ตอบ 200 เมื่อใช้ allowed Host, และ `docker compose ps` ไม่มี warning เรื่อง `POSTGRES_PASSWORD`.
- ก่อนหมุน credential สร้าง custom-format `pg_dump` เวลา `2026-09-27T11:25:05Z`, 17,567 bytes, mode `0600`; `pg_restore --list` อ่าน archive catalog ผ่าน. Backup path: `/home/cvsz/.local/share/zttato/backups/postgres-zttato-before-password-rotation-20260927T112505Z.dump`. ไม่ได้ restore backup นี้ซ้ำใน follow-up นี้.
- `.env.sandbox` และ `.env.production` ต่างชี้ไป Compose `db/zttato`; ดังนั้น sandbox profile ปัจจุบัน **ไม่ใช่ฐานข้อมูล staging ที่แยกขาด**. ห้ามใช้ผลจาก profile นี้เป็นหลักฐาน staging migration/restore.
- ไม่ทำ migration/stamp, ไม่ rebuild/deploy source changes, ไม่ retry หรือส่ง content ไป TikTok, และไม่แตะ Ads MCP หรือเงิน. Dashboard code fix ยังอยู่ใน worktree และยังไม่อยู่ใน running image.
