#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SCRIPT_UNDER_TEST="$REPO_ROOT/deploy/vps/run.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_eq() {
  local actual="$1"
  local expected="$2"
  local message="$3"

  if [[ "$actual" != "$expected" ]]; then
    fail "$message (expected '$expected', got '$actual')"
  fi
}

assert_file_equals() {
  local path="$1"
  local expected="$2"
  local message="$3"
  local content

  content="$(tr -d '\r' < "$path")"
  content="${content%$'\n'}"
  assert_eq "$content" "$expected" "$message"
}

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

origin_repo="$tmpdir/origin.git"
seed_repo="$tmpdir/seed"
server_repo="$tmpdir/server"
update_log="$tmpdir/update.log"

git init --bare "$origin_repo" >/dev/null
git --git-dir="$origin_repo" symbolic-ref HEAD refs/heads/main
git init --initial-branch=main "$seed_repo" >/dev/null
git -C "$seed_repo" config user.name "Codex Test"
git -C "$seed_repo" config user.email "codex@example.com"
git -C "$seed_repo" remote add origin "$origin_repo"

printf 'version-one\n' > "$seed_repo/app.txt"
printf 'requests==2.32.3\n' > "$seed_repo/requirements.txt"
git -C "$seed_repo" add app.txt requirements.txt
git -C "$seed_repo" commit -m "initial commit" >/dev/null
git -C "$seed_repo" push -u origin main >/dev/null

git clone --branch main "$origin_repo" "$server_repo" >/dev/null

printf 'local dirty change\n' > "$server_repo/app.txt"
printf 'temporary note\n' > "$server_repo/notes.txt"

printf 'version-two\n' > "$seed_repo/app.txt"
git -C "$seed_repo" add app.txt
git -C "$seed_repo" commit -m "remote update" >/dev/null
git -C "$seed_repo" push >/dev/null

if ! INSTALL_DIR="$server_repo" AUTO_INSTALL_DEPS=false AUTO_UPDATE_STRICT=true \
  bash "$SCRIPT_UNDER_TEST" --update-only >"$update_log" 2>&1; then
  cat "$update_log" >&2
  fail "auto update should succeed even when the working tree has local changes"
fi

server_head="$(git -C "$server_repo" rev-parse HEAD)"
remote_head="$(git -C "$server_repo" rev-parse origin/main)"
status_output="$(git -C "$server_repo" status --short)"
stash_count="$(git -C "$server_repo" stash list | wc -l | tr -d ' ')"

assert_eq "$server_head" "$remote_head" "server repo should fast-forward to origin/main"
assert_eq "$status_output" "" "server repo should be clean after auto update"
assert_file_equals "$server_repo/app.txt" "version-two" "updated code should match the remote commit"

if [[ "$stash_count" -lt 1 ]]; then
  fail "local changes should be preserved in git stash"
fi

printf 'PASS\n'
