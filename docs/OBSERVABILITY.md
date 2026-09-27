# Observability and alerting

## ขอบเขต

แอปเปิด Prometheus text endpoint ที่ `GET /metrics` เมื่อกำหนด `METRICS_BEARER_TOKEN` เท่านั้น คำขอที่ไม่มี Bearer token ที่ถูกต้องได้ 404 ตัวแปรนี้ต้องเป็น secret ที่สุ่มอย่างน้อย 32 bytes, เก็บนอก Git และ scrape ผ่าน network ที่จำกัดสิทธิ์ ห้ามใส่ token, cookie, OAuth state, TikTok identifier หรือ upload URL เป็น label หรือ log field

metric ที่แอปส่งออก:

- `zttato_http_requests_total{method,status}` และ `zttato_http_request_duration_seconds_sum{method,status}` สำหรับแนวโน้ม HTTP และ 5xx/429
- `zttato_oauth_failures_total{status}`, `zttato_video_uploads_total`, `zttato_video_upload_bytes_total` และ `zttato_media_disk_{free,total}_bytes` สำหรับ OAuth, media intake และพื้นที่ว่าง
- `zttato_cleanup_runs_total{result}`, `zttato_cleanup_last_success_timestamp_seconds`, `zttato_cleanup_deleted_rows_total{resource}` และ `zttato_cleanup_file_delete_failures_total` สำหรับงาน retention

metric เป็น process-local; Prometheus ต้อง scrape ทุก app replica แยกกันและรวมด้วย `sum()` ใน rule การตรวจ DB ให้ใช้ probe `GET /health/ready`; probe disk ใช้ metric ที่ exporter ของ host/container ส่งให้ monitoring ระบบปัจจุบันยังไม่ยืนยันว่า Prometheus, alert receiver หรือ exporter ถูกติดตั้ง/route แล้ว

## Alert rules

ไฟล์ [alerts.yml](observability/alerts.yml) กำหนด threshold สำหรับ HTTP 5xx/429, OAuth endpoint failures, retention cleanup, readiness และ disk ที่เก็บ media; [alerts.test.yml](observability/alerts.test.yml) จำลอง metric เพื่อยืนยันว่า alert ทั้ง 8 rules fire ตาม threshold ที่กำหนด ตรวจและรันทดสอบด้วย:

```sh
promtool check rules docs/observability/alerts.yml
promtool test rules docs/observability/alerts.test.yml
```

การทดสอบนี้ยืนยัน syntax และ PromQL alert logic ด้วยข้อมูลสังเคราะห์เท่านั้น ยังไม่ wire rule เข้ากับ Prometheus/Alertmanager จริง และไม่ยืนยันว่า receiver ได้รับ notification ต้องทดสอบ scrape, routing และการส่ง alert จริงก่อนเปิดบริการสาธารณะ

## Operational response

- 5xx/readiness: ตรวจ DB และ migration revision ก่อน restart หรือ rollback
- 429: แยกแอป quota ออกจาก TikTok upstream quota; ใช้ `Retry-After` และห้าม retry post-init ซ้ำก่อน status reconciliation
- cleanup/file deletion: ตรวจ mount และ permissions ของ `MEDIA_DIR`; อย่าลบ metadata ของรายการที่ไฟล์ยังลบไม่ได้
- disk: จำกัด upload ที่ reverse proxy และแอป ควบคุมพื้นที่ volume และทดสอบ alert โดยใช้ staging เท่านั้น
