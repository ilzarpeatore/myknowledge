#!/usr/bin/env bash
# Restore a backup made by backup.sh. Replaces the current database and stored files.
#   Docker (default):  ./scripts/restore.sh backups/kos-YYYYMMDD-HHMMSS
#   Local mode:        KOS_BACKUP_MODE=local KOS_DATABASE_URL=... KOS_STORAGE_ROOT=... \
#                      ./scripts/restore.sh <backup_dir>
# Asks for confirmation unless KOS_RESTORE_YES=1.
set -euo pipefail

SRC="${1:?usage: restore.sh <backup_dir>}"
MODE="${KOS_BACKUP_MODE:-docker}"
SRC="$(cd "$SRC" && pwd)"
cd "$(dirname "$0")/.."

(cd "$SRC" && sha256sum -c SHA256SUMS) || { echo "checksum mismatch: aborting"; exit 1; }
cat "$SRC/info.txt" 2>/dev/null || true

if [ "${KOS_RESTORE_YES:-}" != "1" ]; then
  read -r -p "This REPLACES the current database and files with $SRC. Type 'restore' to continue: " ans
  [ "$ans" = "restore" ] || { echo "aborted"; exit 1; }
fi

if [ "$MODE" = "docker" ]; then
  docker compose stop api worker web gateway
  docker compose exec -T db dropdb -U kos --if-exists kos
  docker compose exec -T db createdb -U kos kos
  docker compose exec -T db pg_restore -U kos -d kos --no-owner --exit-on-error <"$SRC/db.dump"
  # As root; tar restores the original file owners (the app user).
  docker compose run --rm --no-deps -T -u root -v "$SRC:/backup:ro" --entrypoint sh api \
    -c 'find /data/storage -mindepth 1 -delete && tar -C /data/storage -xzf /backup/storage.tar.gz'
  docker compose up -d   # 'migrate' runs first and applies any newer migrations
else
  : "${KOS_DATABASE_URL:?set KOS_DATABASE_URL}"
  : "${KOS_STORAGE_ROOT:?set KOS_STORAGE_ROOT}"
  url="${KOS_DATABASE_URL/postgresql+psycopg/postgresql}"
  # Extensions (vector, pg_trgm) belong to a superuser: they must already exist in
  # the target database and are left out of the restore list.
  list="$(mktemp)"
  trap 'rm -f "$list"' EXIT
  pg_restore --list "$SRC/db.dump" | grep -vE ' (EXTENSION|COMMENT) .*(vector|pg_trgm)' >"$list"
  pg_restore -d "$url" -L "$list" --clean --if-exists --no-owner --exit-on-error "$SRC/db.dump"
  mkdir -p "$KOS_STORAGE_ROOT"
  find "$KOS_STORAGE_ROOT" -mindepth 1 -delete
  tar -C "$KOS_STORAGE_ROOT" -xzf "$SRC/storage.tar.gz"
fi
echo "Restore complete from $SRC"
