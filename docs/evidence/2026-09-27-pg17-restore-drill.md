# Isolated PostgreSQL 17 restore rehearsal

- Result: PASS
- Environment: isolated synthetic staging database on loopback
- Completed UTC: 2026-09-27T04:31:49Z
- Backup start UTC: 2026-09-27T04:31:46Z
- Backup complete UTC: 2026-09-27T04:31:47Z
- Restore start UTC: 2026-09-27T04:31:47Z
- Restore complete UTC: 2026-09-27T04:31:49Z
- Synthetic RPO: 0 seconds (no writes after the controlled snapshot)
- Measured restore RTO: 2 seconds (pg_restore and decrypt verification)
- PostgreSQL major: 17
- Backup size: 14269 bytes
- Backup SHA-256: 0ff9fbbeed7d0d25fc1ab0d62b97e5587f2a9709094e52151ea2e3eaf25ddfd0
- Migration revision: 20260927_02
- Synthetic authorization, session, OAuth, media and job rows: 1 each
- Separately stored drill Fernet key: used for successful access and refresh token decryption; key deleted after drill
- Credentials, plaintext test tokens, OAuth state and account identifiers: redacted and deleted after drill
- Production database, TikTok credentials and production encryption key: not used
