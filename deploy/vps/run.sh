#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/linuxdo-v2ex-checkin}"
PYTHON_BIN="${PYTHON_BIN:-$INSTALL_DIR/.venv/bin/python}"
PIP_BIN="${PIP_BIN:-$INSTALL_DIR/.venv/bin/pip}"
AUTO_UPDATE="${AUTO_UPDATE:-true}"
AUTO_INSTALL_DEPS="${AUTO_INSTALL_DEPS:-true}"
AUTO_UPDATE_STRICT="${AUTO_UPDATE_STRICT:-true}"
AUTO_UPDATE_REMOTE="${AUTO_UPDATE_REMOTE:-origin}"
XVFB_SCREEN="1920x1080x24"

log() {
  printf '[run.sh] %s\n' "$*"
}

notify_message() {
  local title="$1"
  local message="$2"
  local python_bin="$PYTHON_BIN"

  if [[ ! -x "$python_bin" ]]; then
    python_bin="$(command -v python3 || true)"
  fi

  if [[ -z "$python_bin" ]]; then
    log "No Python interpreter available for failure notification."
    return 0
  fi

  INSTALL_DIR="$INSTALL_DIR" NOTIFY_TITLE="$title" NOTIFY_MESSAGE="$message" "$python_bin" - <<'PY' || true
import os
import sys

install_dir = os.environ.get("INSTALL_DIR", "")
if install_dir and install_dir not in sys.path:
    sys.path.insert(0, install_dir)

title = os.environ.get("NOTIFY_TITLE", "VPS Auto Update")
message = os.environ.get("NOTIFY_MESSAGE", "")

try:
    from notify import NotificationManager
    NotificationManager().send_all(title, message)
except Exception as exc:
    print(f"[run.sh] Failed to send notification: {exc}", file=sys.stderr)
PY
}

strict_abort() {
  local reason="$1"
  log "$reason"
  notify_message "VPS Auto Update Failed" "$reason"
  return 1
}

as_bool() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

should_use_xvfb() {
  [[ -z "${DISPLAY:-}" ]]
}

git_head_short() {
  git -C "$INSTALL_DIR" rev-parse --short "$1" 2>/dev/null || true
}

auto_update_repo() {
  if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    log "Git repository not found in $INSTALL_DIR, skipping auto update."
    return 0
  fi

  local old_head
  local new_head
  local current_branch
  local upstream_ref
  local remote_name
  local remote_branch
  local remote_head
  local old_head_short
  local remote_head_short
  local new_head_short

  old_head="$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)"
  old_head_short="$(git_head_short HEAD)"
  current_branch="$(git -C "$INSTALL_DIR" branch --show-current 2>/dev/null || true)"
  upstream_ref="$(git -C "$INSTALL_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"

  log "Auto updating repository..."
  log "Current branch: ${current_branch:-detached}"
  log "Current HEAD: ${old_head_short:-unknown}"

  if [[ -n "$upstream_ref" && "$upstream_ref" == */* ]]; then
    remote_name="${upstream_ref%%/*}"
    remote_branch="${upstream_ref#*/}"
  else
    remote_name="$AUTO_UPDATE_REMOTE"
    remote_branch="${current_branch:-main}"
    upstream_ref="$remote_name/$remote_branch"
  fi

  if ! git -C "$INSTALL_DIR" fetch --prune "$remote_name" "$remote_branch"; then
    log "git fetch failed for $remote_name/$remote_branch."
    if as_bool "$AUTO_UPDATE_STRICT"; then
      strict_abort "AUTO_UPDATE_STRICT enabled: git fetch failed for $remote_name/$remote_branch, so this run was stopped to avoid executing stale code."
    fi
    log "AUTO_UPDATE_STRICT disabled, continuing with the currently checked out local code."
    return 0
  fi

  remote_head="$(git -C "$INSTALL_DIR" rev-parse "$upstream_ref" 2>/dev/null || true)"
  remote_head_short="$(git_head_short "$upstream_ref")"
  log "Remote HEAD: ${remote_head_short:-unknown} ($upstream_ref)"

  if [[ -z "$old_head" || -z "$remote_head" ]]; then
    log "Unable to resolve local or remote Git revision."
    if as_bool "$AUTO_UPDATE_STRICT"; then
      strict_abort "AUTO_UPDATE_STRICT enabled: unable to resolve local or remote Git revision, so this run was stopped."
    fi
    log "AUTO_UPDATE_STRICT disabled, continuing with local code."
    return 0
  fi

  if [[ "$old_head" == "$remote_head" ]]; then
    log "Repository already up to date at ${old_head_short:-unknown}."
    return 0
  fi

  if ! git -C "$INSTALL_DIR" merge --ff-only "$upstream_ref"; then
    log "Fast-forward update failed: ${old_head_short:-unknown} -> ${remote_head_short:-unknown}."
    if as_bool "$AUTO_UPDATE_STRICT"; then
      strict_abort "AUTO_UPDATE_STRICT enabled: fast-forward update failed (${old_head_short:-unknown} -> ${remote_head_short:-unknown}), so this run was stopped to avoid executing stale code."
    fi
    log "AUTO_UPDATE_STRICT disabled, continuing with the currently checked out local code."
    return 0
  fi

  new_head="$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)"
  new_head_short="$(git_head_short HEAD)"
  log "Repository updated: ${old_head_short:-unknown} -> ${new_head_short:-unknown}"

  if ! as_bool "$AUTO_INSTALL_DEPS"; then
    log "AUTO_INSTALL_DEPS disabled, skipping dependency refresh."
    return 0
  fi

  if git -C "$INSTALL_DIR" diff --name-only "$old_head" "$new_head" -- requirements.txt | grep -q .; then
    log "requirements.txt changed, refreshing Python dependencies..."
    if ! "$PIP_BIN" install -r "$INSTALL_DIR/requirements.txt"; then
      if as_bool "$AUTO_UPDATE_STRICT"; then
        strict_abort "AUTO_UPDATE_STRICT enabled: dependency refresh failed after updating to ${new_head_short:-unknown}, so this run was stopped."
      fi
      log "Dependency refresh failed, continuing because AUTO_UPDATE_STRICT is disabled."
    fi
  else
    log "requirements.txt unchanged, skipping dependency refresh."
  fi
}

run_update_phase() {
  if as_bool "$AUTO_UPDATE"; then
    auto_update_repo
  else
    log "AUTO_UPDATE disabled, skipping git pull."
  fi
}

run_main_phase() {
  log "Running main.py at commit $(git_head_short HEAD)"

  # Cleanup orphaned Chrome processes from any previously crashed run.
  pkill -9 -f 'chrome.*DrissionPage' 2>/dev/null || true
  pkill -9 -f 'chrome.*remote-debugging-port' 2>/dev/null || true

  if should_use_xvfb; then
    if ! command -v xvfb-run >/dev/null 2>&1; then
      log "DISPLAY is empty and xvfb-run is required because LinuxDo uses a headed browser."
      return 1
    fi
    log "DISPLAY is empty, starting main.py with xvfb-run."
    exec xvfb-run -a --server-args="-screen 0 ${XVFB_SCREEN}" \
      "$PYTHON_BIN" "$INSTALL_DIR/main.py"
  fi

  exec "$PYTHON_BIN" "$INSTALL_DIR/main.py"
}

main() {
  cd "$INSTALL_DIR"

  case "${1:-}" in
    --update-only)
      run_update_phase
      ;;
    --run-only)
      run_main_phase
      ;;
    "")
      run_update_phase
      run_main_phase
      ;;
    *)
      log "Unknown argument: $1"
      return 2
      ;;
  esac
}

main "$@"
