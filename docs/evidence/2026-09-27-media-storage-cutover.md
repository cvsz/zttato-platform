# หลักฐานย้าย media storage — 2026-09-27

สถานะนี้ยืนยันเฉพาะการย้าย media storage และ ACL ของ path นี้ ไม่ใช่การประกาศว่า repository หรือ TikTok integration พร้อม production ทั้งหมด

## ผลการ cutover

- Compose ผูก `/mnt/zeaz-data/zttato-media` เข้ากับ `/srv/media`; app ทำงานด้วย UID/GID `1000:1000` ตามเจ้าของ CIFS mount
- หยุด app ก่อนคัดลอก และคง `zttato_media_data` ไว้เพื่อ rollback
- ย้ายไฟล์ 3 ไฟล์ รวม 14,087,136 bytes; manifest SHA-256: `af7b210082c49dca2609096da229ae37b3ee65b00e112bf79dc3defb0c9a503b`
- ตรวจฐานข้อมูลหลังเริ่ม app: media rows 3, ไฟล์หาย 0, ขนาดไม่ตรง 0; named volume เดิมยังมีไฟล์ครบ 3 ไฟล์
- สร้าง container ใหม่จาก image เดิม โดยไม่ build image, เปลี่ยน schema หรือเรียก TikTok API

## สิทธิ์ของ share

ก่อนเปลี่ยน ACL โฟลเดอร์รับสิทธิ์สืบทอดจาก `Authenticated Users` และ `Builtin Users` ซึ่งเปิดการอ่าน/เขียนให้ผู้ใช้กว้างเกินความจำเป็น สำรอง ACL เดิมไว้ที่ `/home/cvsz/.local/share/zttato-media-acl-8bh2fqyk/before.txt` โดยไฟล์มี mode `0600` และ parent directory มี mode `0700`.

หลังแก้ DACL เฉพาะโฟลเดอร์ `zttato-media` และปิด inheritance แล้ว DACL อนุญาตเฉพาะ mount identity, Administrators และ SYSTEM; ตรวจ DACL ของไฟล์ทั้ง 3 ไฟล์แล้วได้ ACL ชุดเดียวกัน ไม่มี ACE ของ Authenticated Users, Builtin Users หรือ Everyone. การ query SACL ไม่มีสิทธิ์เพียงพอ แต่ DACL สำหรับ access control อ่านและยืนยันได้

CIFS ยังรายงาน POSIX mode `0770` แม้แอปเรียก `chmod(0600)`; การจำกัดผู้เข้าถึงของ path นี้จึงอาศัย server-side DACL ที่ตรวจไว้ ไม่ได้อาศัย mode ที่รายงานจาก client

## การตรวจหลังเริ่มบริการ

- `docker compose config -q` และ `git diff --check` ผ่าน
- one-shot container ด้วย UID/GID เดียวกับ app เขียน/อ่าน/chmod ใน bind mount ได้ และเชื่อม PostgreSQL ด้วย `SELECT 1` ได้
- app container อยู่สถานะ `running`, health `healthy`, ใช้ bind mount ที่กำหนด และ `/health/ready` ตอบ HTTP 200
- query ฐานข้อมูลยืนยันไฟล์ media ทั้ง 3 รายการมีอยู่และขนาดตรงกัน
- log 5 นาทีหลังเริ่มไม่มี traceback หรือ error line
- ไม่ได้รัน test suite ใน cutover นี้; ไม่ได้เผยแพร่โพสต์หรือเรียก TikTok API

## RPO/RTO และ rollback

- RPO ของ media ที่ commit ก่อนหยุด app: 0 รายการสูญหาย; ตรวจ manifest ต้นทางและปลายทางตรงกันก่อนเริ่ม app ใหม่
- เวลาหยุดถึง healthy ที่แน่นอนไม่ได้เก็บไว้ จึงระบุ RTO เป็น `NOT_MEASURED`; บริการกลับมา healthy เมื่อ `StartedAt` เป็น `2026-09-27T12:20:00Z`
- หาก rollback หลังมี upload ใหม่ ให้หยุด app, sync ไฟล์จาก bind mount กลับ `zttato_media_data`, ตรวจ count/hash แล้วจึงคืนค่า Compose เดิม อย่าลบ named volume
