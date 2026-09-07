#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly LOCAL_IMAGE="${LOCAL_IMAGE:-secure-scraper-chromium:151.0.7922.169-patched}"
readonly BASE_IMAGE="chrome-patch-restored-base:${RUNTIME_REFRESH_ID:-$(date -u +%Y%m%d%H%M%S)}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

(( EUID != 0 )) || die "run as the rootless-Docker account, not root"
command -v docker >/dev/null 2>&1 || die "docker is not installed"
security_options="$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null)" ||
  die "cannot reach the rootless Docker daemon"
grep -qi rootless <<<"$security_options" || die "Docker daemon is not rootless"
docker image inspect "$LOCAL_IMAGE" >/dev/null 2>&1 || die "base image not found: $LOCAL_IMAGE"

cleanup() {
  docker image rm "$BASE_IMAGE" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

docker tag "$LOCAL_IMAGE" "$BASE_IMAGE"
DOCKER_BUILDKIT=1 docker build \
  --pull=false \
  --file "$PROJECT_DIR/docker/Dockerfile.runtime-refresh" \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  --tag "$LOCAL_IMAGE" \
  "$PROJECT_DIR"

printf 'Refreshed application layer without rebuilding Chromium: %s\n' "$LOCAL_IMAGE"
