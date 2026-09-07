#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly TEMP_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/chrome-patch-sha256.XXXXXX")"

cleanup() {
  rm -f -- "$TEMP_MANIFEST"
}
trap cleanup EXIT

cd -- "$PROJECT_DIR"

LC_ALL=C find . -type f \
  ! -path './.git/*' \
  ! -path './run-logs/*' \
  ! -path './test-output/*' \
  ! -path './__pycache__/*' \
  ! -path '*/__pycache__/*' \
  ! -path './.pytest_cache/*' \
  ! -name '*.pyc' \
  ! -name 'SHA256SUMS' \
  -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum > "$TEMP_MANIFEST"

[[ -s "$TEMP_MANIFEST" ]] || {
  printf 'Refusing to replace SHA256SUMS with an empty manifest.\n' >&2
  exit 1
}

mv -- "$TEMP_MANIFEST" SHA256SUMS
trap - EXIT
printf 'Wrote SHA256SUMS with %s entries.\n' "$(wc -l < SHA256SUMS)"
