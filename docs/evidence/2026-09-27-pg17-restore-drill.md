# Isolated PostgreSQL 17 restore rehearsal

- Result: PASS
- Environment: isolated synthetic staging database on loopback
- Completed UTC: 2026-09-27T13:21:22Z
- Backup start UTC: 2026-09-27T13:21:18Z
- Backup complete UTC: 2026-09-27T13:21:18Z
- Restore start UTC: 2026-09-27T13:21:18Z
- Restore complete UTC: 2026-09-27T13:21:22Z
- Synthetic RPO: 0 seconds (no writes after the controlled snapshot)
- Measured restore RTO: 4 seconds (pg_restore and decrypt verification)
- PostgreSQL major: 17
- Backup size: 14973 bytes
- Backup SHA-256: 105928fc2891c93a26414ba4998ba4cbf063ba0d4d30d7c5de42ad9ea952aa1c
- Migration revision: 20260927_04
- Synthetic authorization, session, OAuth, media and job rows: 1 each
- Separately stored drill Fernet key: used for successful access and refresh token decryption; key deleted after drill
- Credentials, plaintext test tokens, OAuth state and account identifiers: redacted and deleted after drill
- Production database, TikTok credentials and production encryption key: not used
