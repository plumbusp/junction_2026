#!/usr/bin/env bash
# Roll Spot the Spin on the VPS back to an earlier image.
#
# DRY RUN BY DEFAULT: without --confirm it only prints the plan.
# Run by a human from WSL, after the user said "yes" to the rollback in the
# conversation. Agents never run this script.
#
# Without --to, the target is the sha in /opt/spotthespin/app/PREVIOUS, written
# by deploy.sh. The image spotthespin-app:<sha> and releases/<sha> must still
# exist on the VPS; nothing is rebuilt. The SQLite volume is kept as is.
#
# Usage: bash deploy/rollback.sh [--to <sha>] [--confirm]
# Env:   DEPLOY_SSH_TARGET, DEPLOY_APP_DIR, DEPLOY_SSH_OPTS (see deploy.sh)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
. "$REPO_ROOT/deploy/lib.sh"

usage() {
  sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

TO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --confirm) CONFIRM=1 ;;
    --to) [ $# -ge 2 ] || die "--to needs a value"; TO="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
  shift
done

case "$TO" in
  "") ;;
  *[!0-9a-f]*) die "--to must be a short git sha (hex), got '$TO'" ;;
esac

banner "rollback"

if [ -n "$TO" ]; then
  RESOLVE="TAG='$TO'"
else
  RESOLVE="TAG=\$(cat '$APP_DIR/PREVIOUS' 2>/dev/null || true)
[ -n \"\$TAG\" ] || { echo 'no $APP_DIR/PREVIOUS; pass --to <sha>'; exit 1; }"
fi

remote "switch the app container to the earlier image" "
$RESOLVE
echo \"rolling back to \$TAG\"
docker image inspect \"spotthespin-app:\$TAG\" >/dev/null || { echo \"image spotthespin-app:\$TAG not found\"; exit 1; }
REL='$APP_DIR/releases/'\"\$TAG\"
test -f \"\$REL/docker-compose.yml\" || { echo \"missing \$REL/docker-compose.yml\"; exit 1; }
CUR=\$(cat '$APP_DIR/CURRENT' 2>/dev/null || true)
APP_TAG=\"\$TAG\" docker compose --env-file '$APP_DIR/.env' --project-directory \"\$REL\" -f \"\$REL/docker-compose.yml\" up -d --no-build
[ -z \"\$CUR\" ] || echo \"\$CUR\" > '$APP_DIR/PREVIOUS'
echo \"\$TAG\" > '$APP_DIR/CURRENT'
docker ps --filter name=spotthespin-app-1"

echo
echo "Next (read-only): bash .claude/skills/run-app/scripts/smoke.sh $PUBLIC_URL"
