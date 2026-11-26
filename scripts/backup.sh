#!/usr/bin/env bash
# Back up Knowledge OS: database (pg_dump custom format) + stored originals.
#   Docker (default):  ./scripts/backup.sh [backup_root]
#   Local mode:        KOS_BACKUP_MODE=local KOS_DATABASE_URL=postgresql://... \
#                      KOS_STORAGE_ROOT=/path ./scripts/backup.sh [backup_root]
# Creates <backup_root>/kos-<timestamp>/ with db.dump, storage.tar.gz, SHA256SUMS, info.txt
# and keeps the newest KOS_BACKUP_KEEP backups (default 14). .env is NOT copied (secrets).
set -euo pipefail

ROOT="${1:-./backups}"
KEEP="${KOS_BACKUP_KEEP:-14}"
MODE="${KOS_BACKUP_MODE:-docker}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ROOT"
ROOT="$(cd "$ROOT" && pwd)"          # absolute, before changing directory
DEST="$ROOT/kos-$STAMP"
mkdir "$DEST"
cd "$(dirname "$0")/.."              # project root: docker compose runs here

echo "Backing up to $DEST (mode: $MODE)"
if [ "$MODE" = "docker" ]; then
  docker compose exec -T db pg_dump -U kos -d kos -Fc >"$DEST/db.dump"
  # Read the storage volume through a throwaway container, read-only.
  # As root: the app user cannot write to the host backup directory.
  docker compose run --rm --no-deps -T -u root -v "$DEST:/backup" --entrypoint sh api \
    -c 'tar -C /data/storage -czf /backup/storage.tar.gz .'
else
  : "${KOS_DATABASE_URL:?set KOS_DATABASE_URL (postgresql://user:pass@host/db)}"
  : "${KOS_STORAGE_ROOT:?set KOS_STORAGE_ROOT}"
  pg_dump -Fc -d "${KOS_DATABASE_URL/postgresql+psycopg/postgresql}" >"$DEST/db.dump"
  tar -C "$KOS_STORAGE_ROOT" -czf "$DEST/storage.tar.gz" .
fi

# A dump that pg_restore cannot list is not a backup.
if [ "$MODE" = "docker" ]; then
  docker compose exec -T db pg_restore --list <"$DEST/db.dump" >/dev/null
else
  pg_restore --list "$DEST/db.dump" >/dev/null
fi
tar -tzf "$DEST/storage.tar.gz" >/dev/null

(cd "$DEST" && sha256sum db.dump storage.tar.gz >SHA256SUMS)
{
  echo "created: $(date -Is)"
  echo "mode: $MODE"
  echo "git: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "db.dump: $(du -h "$DEST/db.dump" | cut -f1)"
  echo "storage.tar.gz: $(du -h "$DEST/storage.tar.gz" | cut -f1)"
} >"$DEST/info.txt"
echo "OK: $(tr '\n' ' ' <"$DEST/info.txt")"

# Retention: keep the newest $KEEP backups.
ls -1dt "$ROOT"/kos-* 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
  echo "removing old backup $old"
  rm -rf -- "$old"
done
