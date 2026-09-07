#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly REPO_URL="https://github.com/ornab74/scraper-chrome-docker.git"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly APP_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly LOCAL_IMAGE="${LOCAL_IMAGE:-secure-scraper-chromium:151.0.7922.169-patched}"
readonly GHCR_IMAGE="${GHCR_IMAGE:-ghcr.io/ornab74/scraper-chrome-docker:latest}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git is not installed"
command -v docker >/dev/null 2>&1 || die "docker is not installed"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is not installed"
[[ -d "$APP_DIR/.git" ]] || die "run this script from a checked-out scraper-chrome-docker repository"

security_options="$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null)" || die "cannot reach the configured Docker daemon"
grep -qi rootless <<<"$security_options" || die "Docker daemon is not rootless"
grep -qi seccomp <<<"$security_options" || die "Docker seccomp is unavailable"

if [[ -z "${GITHUB_PAT:-}" ]]; then
  printf 'GitHub PAT (repo read; package read if GHCR is needed): ' >&2
  IFS= read -r -s GITHUB_PAT
  printf '\n' >&2
fi
[[ -n "$GITHUB_PAT" ]] || die "GitHub PAT is required for this private repository"

readonly CRED_DIR="$(mktemp -d)"
readonly CRED_SOCKET="$CRED_DIR/git-credential-cache.sock"
cleanup() {
  git -c credential.helper="cache --socket=$CRED_SOCKET" credential-cache exit >/dev/null 2>&1 || true
  rm -rf -- "$CRED_DIR"
  unset GITHUB_PAT
}
trap cleanup EXIT HUP INT TERM

git_with_auth() {
  git -c credential.helper="cache --socket=$CRED_SOCKET" "$@"
}

printf 'protocol=https\nhost=github.com\nusername=x-access-token\npassword=%s\n\n' "$GITHUB_PAT" |
  git -c credential.helper="cache --socket=$CRED_SOCKET" credential approve

git_with_auth ls-remote --exit-code "$REPO_URL" HEAD >/dev/null ||
  die "PAT could not read ornab74/scraper-chrome-docker"

cd "$APP_DIR"
git remote set-url origin "$REPO_URL"
git_with_auth fetch --prune origin main
git checkout -B main origin/main

printf '\nRepository directory: %s\n' "$APP_DIR"
printf 'Repository commit:\n'
git rev-parse HEAD

printf '\nVerifying committed SHA256SUMS:\n'
if ! sha256sum --check --strict SHA256SUMS; then
  printf '\nNOTE: committed SHA256SUMS is stale. Current Assignment 8 hashes:\n' >&2
  sha256sum assignment8/get_books.py assignment8/owasp_top_10.py >&2
  die "SHA256SUMS verification failed; regenerate and commit the manifest before running"
fi

printf '\nAssignment 8 SHA-256 values:\n'
sha256sum assignment8/get_books.py assignment8/owasp_top_10.py

if ! docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1; then
  printf '\nLocal runtime image is missing; authenticating to GHCR...\n'
  printf '%s' "$GITHUB_PAT" | docker login ghcr.io -u ornab74 --password-stdin
  docker pull "$GHCR_IMAGE" || die "could not pull $GHCR_IMAGE"
  docker tag "$GHCR_IMAGE" "$LOCAL_IMAGE"
  docker logout ghcr.io >/dev/null 2>&1 || true
fi

bash ./scripts/refresh-runtime-image.sh

printf '\nContainer security verification:\n'
docker compose run --rm browser-audit

printf '\nRunning live Durham scrape:\n'
docker compose --profile live run --rm assignment8-books-live

printf '\nRunning live OWASP scrape:\n'
docker compose --profile live run --rm assignment8-owasp-live

printf '\nAssignment 8 results:\n'
bash ./scripts/view-results.sh
