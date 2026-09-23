#!/usr/bin/env bash
# The gate for this repository, run locally and by .github/workflows/build.yml. The lint
# half holds the scripts, the workflows, the flake and the documents to their rules; the
# behaviour half runs the service's own suite against a stand-in for Telethon, so nothing
# here reaches the network or a Telegram account

set -euo pipefail

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd)

usage() {
  cat <<'EOF'
check.sh — the gate for the telegram skill

Usage: check.sh [all | lint | behaviour]

  all         both halves, in order (the default)
  lint        the vendored checkers, the workflows, the flake and the documents
  behaviour   the service's suite, against a stand-in for Telethon

Exit codes:
  0  everything holds
  1  a check found something
  2  usage, or a tool the gate needs is missing
EOF
}

fail() {
  printf 'check: %s\n' "$*" >&2
  exit 1
}

die() {
  printf 'check: %s\n' "$*" >&2
  exit 2
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "needs $1 — nix develop -c is where the pinned ones live"
}

cmd_lint() {
  cd "$ROOT"
  for tool in shellcheck shfmt actionlint nixfmt statix deadnix jq python; do need "$tool"; done

  echo "== the vendored copies are byte-equal to their source"
  ./vendor-sync.sh check

  echo "== the scripts parse and lint"
  ./check-sh.sh tests/check.sh

  echo "== the workflows are valid, and their tools come from the lock rather than a registry"
  actionlint
  ./check-pins.sh

  echo "== the Nix here is formatted, and says nothing it does not mean"
  nixfmt --check flake.nix nix/package.nix
  statix check
  deadnix --fail

  echo "== the python parses"
  python -m compileall -q tg_agentd tests

  echo "== no paragraph in the docs is hard-wrapped or ends on a full stop"
  ./check-prose.sh

  echo "== SKILL.md loads, every reference is reachable, and every link resolves"
  ./check-skill.sh -n telegram

  echo "== the changelog obeys the versioning skill's rules"
  ./check-changelog.sh
}

cmd_behaviour() {
  cd "$ROOT"
  need python
  echo "== the service's suite, with no account and no network"
  python -m pytest tests -q
}

cmd_all() {
  cmd_lint
  cmd_behaviour
  printf '\ncheck: everything holds\n'
}

cmd=${1:-all}
case $cmd in
  all) cmd_all ;;
  lint) cmd_lint ;;
  behaviour) cmd_behaviour ;;
  -h | --help | help) usage ;;
  *)
    usage >&2
    exit 2
    ;;
esac
