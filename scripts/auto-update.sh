#!/usr/bin/env bash
# Update Knowledge OS when the branch the VPS follows has new commits.
# Used by both automatic options (DEPLOYMENT.md §6b):
#   - cron on the VPS, every 30 minutes:   */30 * * * * /ruta/knowledge-os/scripts/auto-update.sh
#   - GitHub Actions over SSH, with a key restricted to this script (forced command).
# Steps: lock -> fetch -> (optional) require green CI -> backup -> fast-forward ->
# build -> start -> check health. If the new version does not come up healthy, it goes
# back to the previous commit and alerts. Database migrations are not undone: the
# alert names the backup to restore if that is needed.
#
# Settings (environment, or a line in .env is NOT read here on purpose):
#   KOS_DEPLOY_BRANCH        branch to follow (default: the one checked out)
#   KOS_DEPLOY_BACKUP_ROOT   where backup.sh writes (default: ./backups)
#   KOS_DEPLOY_REQUIRE_CI    1 = deploy only commits whose GitHub CI passed (needs
#                            KOS_DEPLOY_GITHUB_TOKEN: fine-grained, read-only Actions)
#   KOS_DEPLOY_GITHUB_REPO   owner/name (default: from the origin URL)
#   KOS_DEPLOY_HEALTH_WAIT   seconds to wait for a healthy API (default: 240)
# Optional settings file, private to the user (chmod 600): $HOME/.config/kos-deploy.env
# (or KOS_DEPLOY_ENV), for the token above; cron does not need it in the crontab.
# Flags: --force     rebuild and restart even if there is nothing new.
#        --from-ssh  called through the restricted SSH key of GitHub Actions: the only
#                    request honoured is "force" (anything else is ignored).
set -euo pipefail

cd "$(dirname "$0")/.."
FORCE=0
case "${1:-}" in
  --force) FORCE=1 ;;
  --from-ssh) [ "${SSH_ORIGINAL_COMMAND:-}" = "force" ] && FORCE=1 ;;
esac
ENV_FILE="${KOS_DEPLOY_ENV:-$HOME/.config/kos-deploy.env}"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi
BRANCH="${KOS_DEPLOY_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
BACKUP_ROOT="${KOS_DEPLOY_BACKUP_ROOT:-./backups}"
WAIT="${KOS_DEPLOY_HEALTH_WAIT:-240}"
FAILED_FILE=".auto-update-failed"   # a version that did not come up is not retried

log() { echo "$(date -Is) $*"; }

# Alerts go through the app (KOS_ALERT_URL: ntfy or Slack); never fail because of them.
notify() {
  docker compose exec -T api kos notify "$1" "$2" >/dev/null 2>&1 \
    || docker compose run --rm --no-deps -T api kos notify "$1" "$2" >/dev/null 2>&1 \
    || true
}
record() {
  docker compose exec -T api kos record-event "$@" >/dev/null 2>&1 || true
}

# One run at a time (cron and GitHub may coincide).
exec 9>"${KOS_DEPLOY_LOCK:-/tmp/kos-auto-update.lock}"
if ! flock -n 9; then
  log "another update is running; nothing to do"
  exit 0
fi

if [ "$BRANCH" = "HEAD" ]; then
  log "detached HEAD: set KOS_DEPLOY_BRANCH"
  exit 1
fi
if [ "$(git rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
  log "checked out $(git rev-parse --abbrev-ref HEAD), expected $BRANCH: not touching it"
  notify "Knowledge OS: actualización parada" "El VPS no está en la rama $BRANCH. No se ha cambiado nada."
  exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  log "local changes to tracked files: not updating"
  notify "Knowledge OS: actualización parada" "Hay cambios sin guardar en el código del VPS. No se ha cambiado nada."
  exit 1
fi

git fetch --quiet origin "$BRANCH"
OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse FETCH_HEAD)"
if [ "$OLD" = "$NEW" ] && [ "$FORCE" = 0 ]; then
  log "up to date at ${OLD:0:7}"
  exit 0
fi
if [ "$FORCE" = 0 ] && [ -f "$FAILED_FILE" ] && [ "$(cat "$FAILED_FILE")" = "$NEW" ]; then
  log "${NEW:0:7} failed before: waiting for a newer commit (or run with --force)"
  exit 0
fi
if ! git merge-base --is-ancestor "$OLD" "$NEW"; then
  log "origin/$BRANCH is not a fast-forward of ${OLD:0:7}: not updating"
  notify "Knowledge OS: actualización parada" "La rama $BRANCH en GitHub no continúa la versión instalada (${OLD:0:7}). Hace falta revisarlo a mano."
  exit 1
fi

if [ "${KOS_DEPLOY_REQUIRE_CI:-0}" = 1 ] && [ "$OLD" != "$NEW" ]; then
  : "${KOS_DEPLOY_GITHUB_TOKEN:?KOS_DEPLOY_REQUIRE_CI=1 needs KOS_DEPLOY_GITHUB_TOKEN}"
  REPO="${KOS_DEPLOY_GITHUB_REPO:-$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')}"
  RUNS="$(curl -fsS -H "Authorization: Bearer $KOS_DEPLOY_GITHUB_TOKEN" \
    "https://api.github.com/repos/$REPO/actions/runs?head_sha=$NEW&status=success&per_page=1" || true)"
  if ! printf '%s' "$RUNS" | grep -q '"total_count": *[1-9]'; then
    log "CI has not passed (yet) for ${NEW:0:7}: waiting"
    exit 0
  fi
fi

SUBJECT="$(git log -1 --format=%s "$NEW")"
log "updating ${OLD:0:7} -> ${NEW:0:7} ($SUBJECT)"
BACKUP_LINE="$(./scripts/backup.sh "$BACKUP_ROOT" | grep '^OK:' || true)"
if [ -z "$BACKUP_LINE" ]; then
  log "backup failed: not updating"
  notify "Knowledge OS: actualización parada" "La copia de seguridad previa falló. No se ha cambiado nada."
  exit 1
fi
BACKUP_DIR="$(ls -1dt "$BACKUP_ROOT"/kos-* | head -n 1)"

healthy() {
  local waited=0 api migrate_code
  while [ "$waited" -lt "$WAIT" ]; do
    api="$(docker compose ps --format '{{.Health}}' api 2>/dev/null || true)"
    migrate_code="$(docker compose ps -a --format '{{.State}} {{.ExitCode}}' migrate 2>/dev/null || true)"
    case "$migrate_code" in
      "exited 0") ;;
      exited*) log "migrate failed: $migrate_code"; return 1 ;;
    esac
    if [ "$api" = "healthy" ] && [ "$migrate_code" = "exited 0" ]; then
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  log "api not healthy after ${WAIT}s (api=$api migrate=$migrate_code)"
  return 1
}

deploy() {
  docker compose build --quiet && docker compose up -d --remove-orphans && healthy
}

git merge --ff-only --quiet "$NEW"
if deploy; then
  log "OK at ${NEW:0:7}"
  rm -f "$FAILED_FILE"
  record deploy_ok "commit=${NEW:0:7}" "from=${OLD:0:7}"
  notify "Knowledge OS actualizado" "Versión ${NEW:0:7}: $SUBJECT"
  exit 0
fi

log "new version unhealthy: going back to ${OLD:0:7}"
echo "$NEW" >"$FAILED_FILE"
git reset --hard --quiet "$OLD"
if deploy; then
  record deploy_failed "commit=${NEW:0:7}" "back_to=${OLD:0:7}"
  notify "Knowledge OS: actualización fallida" "La versión ${NEW:0:7} no arrancó bien y se ha vuelto a ${OLD:0:7}, que funciona. Si la nueva aplicó migraciones, puede hacer falta restaurar $BACKUP_DIR (DEPLOYMENT.md §5)."
else
  notify "Knowledge OS CAÍDO" "Ni la versión ${NEW:0:7} ni la anterior ${OLD:0:7} arrancan. Restaura $BACKUP_DIR con scripts/restore.sh (DEPLOYMENT.md §5)."
fi
exit 1
