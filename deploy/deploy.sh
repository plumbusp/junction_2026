#!/usr/bin/env bash
# Deploy a committed git ref of Spot the Spin to the Hostinger VPS.
#
# DRY RUN BY DEFAULT: without --confirm it only prints the plan.
# Run by a human from WSL in the repo root, after the user said "yes" to this
# deployment in the conversation. Agents never run this script.
#
# Only committed files are shipped (git archive), so the untracked .env and
# secrets/ cannot leave the machine. The image is built on the VPS and tagged
# with the commit sha, which is what rollback.sh switches back to.
#
# Usage: bash deploy/deploy.sh [--ref <git-ref>] [--skip-tests] [--confirm]
# Env:   DEPLOY_SSH_TARGET (default deploy@srv2160832.hstgr.cloud)
#        DEPLOY_APP_DIR    (default /opt/spotthespin/app)
#        DEPLOY_SSH_OPTS   (extra ssh options, e.g. "-i ~/.ssh/id_ed25519")
#        UV                (path to uv, default "uv")
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deploy/lib.sh
. "$REPO_ROOT/deploy/lib.sh"

usage() {
  sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

REF=HEAD
SKIP_TESTS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --confirm) CONFIRM=1 ;;
    --ref) [ $# -ge 2 ] || die "--ref needs a value"; REF="$2"; shift ;;
    --skip-tests) SKIP_TESTS=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
  shift
done

banner "deploy"

SHA="$(git -C "$REPO_ROOT" rev-parse --short=12 "${REF}^{commit}" 2>/dev/null || true)"
if [ -z "$SHA" ]; then
  if [ "$CONFIRM" -eq 1 ]; then die "cannot resolve git ref '$REF'"; fi
  SHA="unresolved"
  echo "   (warning: git ref '$REF' not resolved; showing the plan with a placeholder)"
fi

if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null || true)" ]; then
  echo "   (note: the working tree has uncommitted changes; they are NOT deployed, only $REF is)"
fi

RELEASE_DIR="$APP_DIR/releases/$SHA"
COMPOSE="docker compose --env-file '$APP_DIR/.env' --project-directory '$RELEASE_DIR'"
COMPOSE="$COMPOSE -f '$RELEASE_DIR/docker-compose.yml'"

# 1. Local gates --------------------------------------------------------------
if [ "$SKIP_TESTS" -eq 0 ]; then
  step "run the test suite (must be green)" \
    bash -c "cd '$REPO_ROOT' && '${UV:-uv}' run pytest -q"
fi

check_archive() {
  local leaked
  leaked="$(git -C "$REPO_ROOT" archive --format=tar "$SHA" | tar -t \
    | grep -E '(^|/)(\.env(\..*)?|secrets/.*|.*\.pem|.*\.key|id_(rsa|ed25519).*)$' \
    | grep -v -E '(^|/)\.env\.example$' || true)"
  [ -z "$leaked" ] || die "refusing to ship, secret-looking files are committed: $leaked"
  echo "   ok: no .env, secrets/, keys in the archive"
}
step "check that the commit contains no secret files" check_archive

# 2. Server preconditions -------------------------------------------------------
remote "check the production env file exists with mode 600" "
test -f '$APP_DIR/.env' || { echo 'missing $APP_DIR/.env (create it by hand, chmod 600)'; exit 1; }
[ \"\$(stat -c %a '$APP_DIR/.env')\" = 600 ] || { echo '$APP_DIR/.env must be chmod 600'; exit 1; }
echo 'ok: env file present, mode 600'"

# 3. Ship the source ----------------------------------------------------------
upload() {
  git -C "$REPO_ROOT" archive --format=tar "$SHA" \
    | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" \
      "mkdir -p '$RELEASE_DIR' && tar -x -C '$RELEASE_DIR'"
}
step "upload commit $SHA to $RELEASE_DIR (git archive, committed files only)" upload

# 4. Build, switch, record ------------------------------------------------------
remote "build image spotthespin-app:$SHA" "
cd '$RELEASE_DIR'
APP_TAG='$SHA' $COMPOSE build"

remote "start the new container (Traefik picks it up by labels)" "
cd '$RELEASE_DIR'
if [ -f '$APP_DIR/CURRENT' ] && [ \"\$(cat '$APP_DIR/CURRENT')\" != '$SHA' ]; then
  cp '$APP_DIR/CURRENT' '$APP_DIR/PREVIOUS'
fi
APP_TAG='$SHA' $COMPOSE up -d --no-build --remove-orphans
echo '$SHA' > '$APP_DIR/CURRENT'
$COMPOSE ps"

remote "wait for the container health check (up to 60 s)" "
for i in \$(seq 1 30); do
  s=\$(docker inspect -f '{{.State.Health.Status}}' spotthespin-app-1 2>/dev/null || echo unknown)
  [ \"\$s\" = healthy ] && { echo 'ok: healthy'; exit 0; }
  sleep 2
done
echo \"container not healthy (status: \$s); see: docker logs --tail 100 spotthespin-app-1\"
exit 1"

echo
echo "Next (read-only): bash .claude/skills/run-app/scripts/smoke.sh $PUBLIC_URL"
echo "If the public URL has no route: see docs/DEPLOY.md, section 'Traefik shows no route'."
