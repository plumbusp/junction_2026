# Shared helpers for deploy/*.sh. Sourced, not executed.
#
# Every step goes through `step` (local) or `remote` (on the VPS). Without
# --confirm both only print what they would do, so a dry run never touches the
# server, never opens an SSH connection and never builds anything.

SSH_TARGET="${DEPLOY_SSH_TARGET:-deploy@srv2160832.hstgr.cloud}"
APP_DIR="${DEPLOY_APP_DIR:-/opt/spotthespin/app}"
PUBLIC_URL="${DEPLOY_PUBLIC_URL:-https://srv2160832.hstgr.cloud}"
# Extra ssh options, for example: DEPLOY_SSH_OPTS="-i ~/.ssh/id_ed25519"
read -r -a SSH_OPTS <<< "${DEPLOY_SSH_OPTS:-}"

CONFIRM=0

banner() {
  if [ "$CONFIRM" -eq 1 ]; then
    echo "== $1: EXECUTING against $SSH_TARGET ($APP_DIR)"
  else
    echo "== $1: DRY RUN. Nothing is executed. Re-run with --confirm to apply."
  fi
}

# step "description" cmd args...   (local command)
step() {
  local desc="$1"; shift
  echo "-- [local] $desc"
  printf '   $'; printf ' %q' "$@"; echo
  if [ "$CONFIRM" -eq 1 ]; then "$@"; fi
}

# remote "description" 'shell script run on the VPS'
remote() {
  local desc="$1" script="$2"
  echo "-- [vps]   $desc"
  printf '%s\n' "$script" | sed 's/^/   | /'
  if [ "$CONFIRM" -eq 1 ]; then
    # The script goes over stdin to bash, so it does not depend on the remote
    # login shell's quoting rules.
    printf '%s\n' "$script" | ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'bash -euo pipefail -s'
  fi
}

die() { echo "ERROR: $*" >&2; exit 1; }
