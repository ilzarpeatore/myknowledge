#!/usr/bin/env bash
# Back up Knowledge OS: database (pg_dump custom format) + stored originals, except the
# files the user uploaded (they keep their own copy; uploading the same file again puts
# it back). Those are listed in originals-excluded.tsv. KOS_BACKUP_ORIGINALS=all keeps them.
#   Docker (default):  ./scripts/backup.sh [backup_root]
#   Local mode:        KOS_BACKUP_MODE=local KOS_DATABASE_URL=postgresql://... \
#                      KOS_STORAGE_ROOT=/path ./scripts/backup.sh [backup_root]
# Creates <backup_root>/kos-<timestamp>/ with db.dump, storage.tar.gz,
# originals-excluded.tsv, SHA256SUMS, info.txt
# and keeps the newest KOS_BACKUP_KEEP backups (default 14). .env is NOT copied (secrets).
set -euo pipefail

ROOT="${1:-./backups}"
KEEP="${KOS_BACKUP_KEEP:-14}"
MODE="${KOS_BACKUP_MODE:-docker}"
ORIGINALS="${KOS_BACKUP_ORIGINALS:-own}"   # own: leave the user's uploads out | all
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ROOT"
ROOT="$(cd "$ROOT" && pwd)"          # absolute, before changing directory
DEST="$ROOT/kos-$STAMP"
mkdir "$DEST"
cd "$(dirname "$0")/.."              # project root: docker compose runs here

echo "Backing up to $DEST (mode: $MODE, originals: $ORIGINALS)"
EXCL="$DEST/originals-excluded.tsv"   # key, bytes, file name, title
if [ "$MODE" = "docker" ]; then
  docker compose exec -T db pg_dump -U kos -d kos -Fc >"$DEST/db.dump"
  if [ "$ORIGINALS" = all ]; then : >"$EXCL"; else docker compose exec -T api kos backup-excludes >"$EXCL"; fi
  # Read the storage volume through a throwaway container, read-only.
  # As root: the app user cannot write to the host backup directory.
  # tmp/ holds unfinished uploads and downloads: never worth keeping.
  docker compose run --rm --no-deps -T -u root -v "$DEST:/backup" --entrypoint sh api \
    -c 'cut -f1 /backup/originals-excluded.tsv >/tmp/excl && echo ./tmp >>/tmp/excl &&
        tar -C /data/storage -X /tmp/excl -czf /backup/storage.tar.gz .'
else
  : "${KOS_DATABASE_URL:?set KOS_DATABASE_URL (postgresql://user:pass@host/db)}"
  : "${KOS_STORAGE_ROOT:?set KOS_STORAGE_ROOT}"
  pg_dump -Fc -d "${KOS_DATABASE_URL/postgresql+psycopg/postgresql}" >"$DEST/db.dump"
  if [ "$ORIGINALS" = all ]; then : >"$EXCL"; else kos backup-excludes >"$EXCL"; fi
  patterns="$(mktemp)"
  { cut -f1 "$EXCL"; echo ./tmp; } >"$patterns"
  tar -C "$KOS_STORAGE_ROOT" -X "$patterns" -czf "$DEST/storage.tar.gz" .
  rm -f "$patterns"
fi

# A dump that pg_restore cannot list is not a backup.
if [ "$MODE" = "docker" ]; then
  docker compose exec -T db pg_restore --list <"$DEST/db.dump" >/dev/null
else
  pg_restore --list "$DEST/db.dump" >/dev/null
fi
tar -tzf "$DEST/storage.tar.gz" >/dev/null

(cd "$DEST" && sha256sum db.dump storage.tar.gz originals-excluded.tsv >SHA256SUMS)
LEFT_N="$(wc -l <"$EXCL" | tr -d ' ')"
LEFT_MB="$(awk -F'\t' '{s += $2} END {printf "%.0f", s / 1e6}' "$EXCL")"
{
  echo "created: $(date -Is)"
  echo "mode: $MODE"
  echo "git: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "db.dump: $(du -h "$DEST/db.dump" | cut -f1)"
  echo "storage.tar.gz: $(du -h "$DEST/storage.tar.gz" | cut -f1)"
  echo "originals left out: $LEFT_N ($LEFT_MB MB, your own uploads; see originals-excluded.tsv)"
} >"$DEST/info.txt"
echo "OK: $(tr '\n' ' ' <"$DEST/info.txt")"

# Tell the app a verified backup exists (shown in Sistema; `kos monitor` alerts if it gets old).
SIZE="$(du -sh "$DEST" | cut -f1)"
if [ "$MODE" = docker ]; then
  docker compose exec -T api kos record-event backup_ok "size=$SIZE" "path=$DEST" >/dev/null || true
else
  kos record-event backup_ok "size=$SIZE" "path=$DEST" >/dev/null || true
fi

# Retention: keep the newest $KEEP backups.
ls -1dt "$ROOT"/kos-* 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
  echo "removing old backup $old"
  rm -rf -- "$old"
done
