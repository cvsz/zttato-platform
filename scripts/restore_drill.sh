#!/usr/bin/env bash
# Restore rehearsal only. This script never connects to an unspecified database.

set -Eeuo pipefail
umask 077

die() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

[[ "${ZTTATO_DRILL_CONFIRM:-}" == "ISOLATED_STAGING_ONLY" ]] || die "Set ZTTATO_DRILL_CONFIRM=ISOLATED_STAGING_ONLY"
[[ "${PGHOST:-}" == "127.0.0.1" ]] || die "PGHOST must be 127.0.0.1"
[[ "${PGPORT:-}" =~ ^[0-9]{1,5}$ ]] && (( PGPORT >= 1 && PGPORT <= 65535 )) || die "Set a valid isolated staging PGPORT"
[[ -n "${PGUSER:-}" && -n "${PGPASSWORD:-}" && -n "${ZTTATO_DRILL_ADMIN_DB:-}" ]] || die "Set isolated staging PGUSER, PGPASSWORD and ZTTATO_DRILL_ADMIN_DB"
[[ "$PGPASSWORD" =~ ^[A-Fa-f0-9]{32,}$ ]] || die "Use a hex-only synthetic staging password of at least 32 characters"
for binary in psql pg_dump pg_restore createdb dropdb alembic python3; do
  command -v "$binary" >/dev/null || die "$binary is required"
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUFFIX="$(date -u +%Y%m%d%H%M%S)_$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
SOURCE_DB="zttato_drill_src_${SUFFIX}"
RESTORE_DB="zttato_drill_dst_${SUFFIX}"
EXPECTED_MIGRATION_HEAD="20260927_02"
BACKUP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/zttato-backup.XXXXXX")"
ESCROW_DIR="$(mktemp -d "${TMPDIR:-/tmp}/zttato-key-escrow.XXXXXX")"
chmod 700 "$BACKUP_DIR" "$ESCROW_DIR"
BACKUP_FILE="$BACKUP_DIR/staging.dump"
KEY_FILE="$ESCROW_DIR/fernet.key"
EXPECTED_FILE="$ESCROW_DIR/expected.json"
PGPASS_FILE="$(mktemp "${TMPDIR:-/tmp}/zttato-pgpass.XXXXXX")"
chmod 600 "$PGPASS_FILE"
DRILL_PASSWORD="$PGPASSWORD"
printf '%s:%s:*:%s:%s\n' "$PGHOST" "$PGPORT" "$PGUSER" "$DRILL_PASSWORD" > "$PGPASS_FILE"
export PGPASSFILE="$PGPASS_FILE" PGDATABASE="$ZTTATO_DRILL_ADMIN_DB" PGSSLMODE=disable
export ZTTATO_DRILL_PASSWORD_INTERNAL="$DRILL_PASSWORD"
unset PGPASSWORD DRILL_PASSWORD

cleanup() {
  set +e
  dropdb --if-exists --maintenance-db="$ZTTATO_DRILL_ADMIN_DB" "$SOURCE_DB" >/dev/null 2>&1
  dropdb --if-exists --maintenance-db="$ZTTATO_DRILL_ADMIN_DB" "$RESTORE_DB" >/dev/null 2>&1
  if command -v shred >/dev/null; then
    shred -u "$KEY_FILE" "$EXPECTED_FILE" 2>/dev/null
  fi
  rm -rf -- "$BACKUP_DIR" "$ESCROW_DIR" "$PGPASS_FILE"
}
trap cleanup EXIT HUP INT TERM

make_database_url() {
  DATABASE_NAME_INTERNAL="$1" python3 - <<'PY'
import os
from urllib.parse import quote
user = quote(os.environ["PGUSER"], safe="")
password = quote(os.environ["ZTTATO_DRILL_PASSWORD_INTERNAL"], safe="")
print(f"postgresql+psycopg://{user}:{password}@127.0.0.1:{os.environ['PGPORT']}/{os.environ['DATABASE_NAME_INTERNAL']}")
PY
}

SERVER_VERSION="$(psql -XAt -v ON_ERROR_STOP=1 -c "SELECT current_setting('server_version_num')")" || die "Staging PostgreSQL is unreachable"
[[ "$SERVER_VERSION" =~ ^17[0-9]{4}$ ]] || die "Restore drill requires PostgreSQL major version 17"

python3 - <<'PY' > "$KEY_FILE"
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode("ascii"))
PY
chmod 600 "$KEY_FILE"
python3 - <<'PY' > "$EXPECTED_FILE"
import json
import secrets
print(json.dumps({"access": secrets.token_urlsafe(36), "refresh": secrets.token_urlsafe(36)}))
PY
chmod 600 "$EXPECTED_FILE"

createdb --maintenance-db="$ZTTATO_DRILL_ADMIN_DB" "$SOURCE_DB" >/dev/null
createdb --maintenance-db="$ZTTATO_DRILL_ADMIN_DB" "$RESTORE_DB" >/dev/null

export ZTTATO_DRILL_KEY_FILE="$KEY_FILE" ZTTATO_DRILL_EXPECTED_FILE="$EXPECTED_FILE"
export ZTTATO_DRILL_SOURCE_DB_INTERNAL="$SOURCE_DB"
export DATABASE_URL="$(make_database_url "$SOURCE_DB")"
(cd "$ROOT" && alembic upgrade head >/dev/null) || die "Versioned migration failed"

python3 - <<'PY'
import json
import os
import time
import uuid
from pathlib import Path

from app.db import BrowserSession, LinkedAccount, MediaAsset, OAuthRequest, PublishJob, make_session_factory
from app.security import TokenCipher, digest

key = Path(os.environ["ZTTATO_DRILL_KEY_FILE"]).read_text().strip()
tokens = json.loads(Path(os.environ["ZTTATO_DRILL_EXPECTED_FILE"]).read_text())
cipher = TokenCipher(key)
engine, factory = make_session_factory(os.environ["DATABASE_URL"], bootstrap=False)
now = int(time.time())
session_id = digest("isolated-synthetic-session-" + str(uuid.uuid4()))
media_id = str(uuid.uuid4())
with factory() as session:
    session.add(BrowserSession(id=session_id, csrf_hash=digest("synthetic-csrf"), expires_at=now + 3600, created_at=now))
    session.add(OAuthRequest(state_hash=digest("synthetic-oauth-state"), session_id=session_id, expires_at=now + 900))
    session.add(
        LinkedAccount(
            session_id=session_id,
            open_id="synthetic-account-only",
            scopes="user.info.basic,video.upload,video.publish",
            access_cipher=cipher.encrypt(tokens["access"]),
            refresh_cipher=cipher.encrypt(tokens["refresh"]),
            access_expires_at=now + 3600,
            refresh_expires_at=now + 86400,
        )
    )
    session.add(
        MediaAsset(
            id=media_id,
            session_id=session_id,
            filename="synthetic.mp4",
            size=32,
            path="/synthetic/sentinel.mp4",
            duration_ms=1000,
            created_at=now,
        )
    )
    session.flush()
    session.add(
        PublishJob(
            id=str(uuid.uuid4()),
            session_id=session_id,
            idempotency_key="synthetic-restore-drill-idempotency",
            media_id=media_id,
            mode="draft",
            publish_id="synthetic-publish-id",
            status="RECONCILIATION_REQUIRED",
            checked_at=0,
            created_at=now,
        )
    )
    session.commit()
engine.dispose()
PY

BACKUP_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
pg_dump --no-owner --no-privileges --format=custom --file="$BACKUP_FILE" --dbname="$SOURCE_DB" >/dev/null || die "pg_dump failed"
BACKUP_COMPLETED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BACKUP_SIZE="$(stat -c '%s' "$BACKUP_FILE")"
BACKUP_SHA256="$(sha256sum "$BACKUP_FILE" | cut -d' ' -f1)"
RESTORE_STARTED_EPOCH="$(date +%s)"
RESTORE_STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$RESTORE_DB" "$BACKUP_FILE" >/dev/null || die "pg_restore failed"

export BACKUP_PATH_INTERNAL="$BACKUP_FILE"
export ZTTATO_DRILL_RESTORE_DB_INTERNAL="$RESTORE_DB"
export ZTTATO_DRILL_RESTORE_URL="$(make_database_url "$RESTORE_DB")"
python3 - <<'PY'
import json
import os
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, func, select

from app.db import BrowserSession, LinkedAccount, MediaAsset, OAuthRequest, PublishJob, MIGRATION_HEAD, make_session_factory
from app.security import TokenCipher

expected = json.loads(Path(os.environ["ZTTATO_DRILL_EXPECTED_FILE"]).read_text())
key = Path(os.environ["ZTTATO_DRILL_KEY_FILE"]).read_bytes().strip()
cipher = TokenCipher(key.decode("ascii"))
engine, factory = make_session_factory(os.environ["ZTTATO_DRILL_RESTORE_URL"], bootstrap=False)
with engine.connect() as connection:
    revision = MigrationContext.configure(connection).get_current_revision()
assert revision == MIGRATION_HEAD
with factory() as session:
    rows = {
        "sessions": session.scalar(select(func.count()).select_from(BrowserSession)),
        "oauth": session.scalar(select(func.count()).select_from(OAuthRequest)),
        "accounts": session.scalar(select(func.count()).select_from(LinkedAccount)),
        "media": session.scalar(select(func.count()).select_from(MediaAsset)),
        "jobs": session.scalar(select(func.count()).select_from(PublishJob)),
    }
    account = session.scalar(select(LinkedAccount))
    assert account is not None
    assert cipher.decrypt(account.access_cipher) == expected["access"]
    assert cipher.decrypt(account.refresh_cipher) == expected["refresh"]
    assert rows == {"sessions": 1, "oauth": 1, "accounts": 1, "media": 1, "jobs": 1}
assert key not in Path(os.environ["BACKUP_PATH_INTERNAL"]).read_bytes()
engine.dispose()
print("restore_content=PASS")
print("fernet_decrypt=PASS")
print(f"migration_revision={revision}")
print("row_counts=sessions:1,oauth:1,accounts:1,media:1,jobs:1")
PY

RESTORE_COMPLETED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RESTORE_COMPLETED_EPOCH="$(date +%s)"
RTO_SECONDS="$((RESTORE_COMPLETED_EPOCH - RESTORE_STARTED_EPOCH))"
SOURCE_COMMIT="$(git -C "$ROOT" rev-parse HEAD)"

cat <<EOF
restore_drill=PASS
environment=isolated_synthetic_staging
postgres_major=17
timestamp_utc=$RESTORE_COMPLETED
backup_started_utc=$BACKUP_STARTED
backup_completed_utc=$BACKUP_COMPLETED
restore_started_utc=$RESTORE_STARTED
restore_completed_utc=$RESTORE_COMPLETED
synthetic_rpo_seconds=0
measured_restore_rto_seconds=$RTO_SECONDS
backup_size_bytes=$BACKUP_SIZE
backup_sha256=$BACKUP_SHA256
migration_revision=$EXPECTED_MIGRATION_HEAD
source_commit=$SOURCE_COMMIT
secret_values=redacted
production_data_or_credentials=not_used
EOF

if [[ -n "${ZTTATO_DRILL_REPORT:-}" ]]; then
  REPORT_DIR="$(dirname "$ZTTATO_DRILL_REPORT")"
  mkdir -p "$REPORT_DIR"
  cat > "$ZTTATO_DRILL_REPORT" <<EOF
# Isolated PostgreSQL 17 restore rehearsal

- Result: PASS
- Environment: isolated synthetic staging database on loopback
- Completed UTC: $RESTORE_COMPLETED
- Backup start UTC: $BACKUP_STARTED
- Backup complete UTC: $BACKUP_COMPLETED
- Restore start UTC: $RESTORE_STARTED
- Restore complete UTC: $RESTORE_COMPLETED
- Synthetic RPO: 0 seconds (no writes after the controlled snapshot)
- Measured restore RTO: $RTO_SECONDS seconds (pg_restore and decrypt verification)
- PostgreSQL major: 17
- Backup size: $BACKUP_SIZE bytes
- Backup SHA-256: $BACKUP_SHA256
- Migration revision: $EXPECTED_MIGRATION_HEAD
- Synthetic authorization, session, OAuth, media and job rows: 1 each
- Separately stored drill Fernet key: used for successful access and refresh token decryption; key deleted after drill
- Credentials, plaintext test tokens, OAuth state and account identifiers: redacted and deleted after drill
- Production database, TikTok credentials and production encryption key: not used
EOF
  chmod 600 "$ZTTATO_DRILL_REPORT"
fi
