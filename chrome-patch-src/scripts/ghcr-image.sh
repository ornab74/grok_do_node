#!/usr/bin/env bash
set +x
set -Eeuo pipefail

readonly GITHUB_USER="ornab74"
readonly LOCAL_IMAGE="${LOCAL_IMAGE:-secure-scraper-chromium:151.0.7922.169-patched}"
readonly REMOTE_IMAGE="${REMOTE_IMAGE:-ghcr.io/ornab74/chrome-patch-chromium}"
readonly IMAGE_TAG="${IMAGE_TAG:-151.0.7922.169-patched}"
readonly REMOTE_TAG="${REMOTE_IMAGE}:${IMAGE_TAG}"
readonly PINNED_DIGEST="sha256:f212beff4487370a6c026a1834f779657e76c7091c6ae42b4d2cee8cfe42d2f3"
readonly LOCK_FILE="${LOCK_FILE:-${HOME}/chrome-patch-image.lock}"

AUTH_DIR=""
PUSH_LOG=""

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  scripts/ghcr-image.sh push
  scripts/ghcr-image.sh verify
  scripts/ghcr-image.sh restore [sha256:OCI_MANIFEST_DIGEST]

Run as the non-root account that owns the rootless Docker daemon. The script
prompts for a GitHub PAT without echoing it and uses an ephemeral Docker auth
directory. Push requires a classic PAT with write:packages. Restore requires
read:packages when the package is private.
USAGE
}

configure_rootless_docker() {
  (( EUID != 0 )) || die "run as the rootless-Docker account, not root"

  local uid security_options
  uid="$(id -u)"
  if [[ -z "${XDG_RUNTIME_DIR:-}" && -d "/run/user/${uid}" ]]; then
    export XDG_RUNTIME_DIR="/run/user/${uid}"
  fi
  if [[ -z "${DOCKER_HOST:-}" && -S "${XDG_RUNTIME_DIR:-/nonexistent}/docker.sock" ]]; then
    export DOCKER_HOST="unix://${XDG_RUNTIME_DIR}/docker.sock"
  fi

  command -v docker >/dev/null 2>&1 || die "docker is not installed"
  security_options="$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null)" ||
    die "cannot reach the rootless Docker daemon"
  grep -qi rootless <<<"$security_options" || die "Docker daemon is not rootless"
}

verify_local_image() {
  local image_ref="$1"
  docker image inspect "$image_ref" >/dev/null 2>&1 || die "image not found: ${image_ref}"

  local image_id
  image_id="$(docker image inspect --format '{{.Id}}' "$image_ref")"
  [[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || die "invalid local image ID"

  docker run --rm \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --entrypoint /bin/sh \
    "$image_ref" -ec '
      /opt/chromium/chrome --version
      /opt/chromium/chromedriver --version
      cd /opt/chromium
      sha256sum --check --strict manifest.sha256 >/dev/null
    '

  printf 'Local image verified: %s (%s)\n' "$image_ref" "$image_id"
}

make_auth_dir() {
  local base="${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}"
  AUTH_DIR="$(mktemp -d "${base}/chrome-patch-ghcr.XXXXXX")"
  chmod 0700 "$AUTH_DIR"
}

cleanup() {
  if [[ -n "${PUSH_LOG:-}" ]]; then
    rm -f -- "$PUSH_LOG"
  fi
  if [[ -n "${AUTH_DIR:-}" && -d "${AUTH_DIR}" ]]; then
    docker --config "$AUTH_DIR" logout ghcr.io >/dev/null 2>&1 || true
    rm -rf -- "$AUTH_DIR"
  fi
  unset GHCR_TOKEN 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

login_ghcr() {
  local requested_scope="$1"
  if [[ -t 0 ]]; then
    printf 'GitHub PAT (classic, %s; input hidden): ' "$requested_scope" >&2
    IFS= read -r -s GHCR_TOKEN
    printf '\n' >&2
  elif [[ -r /dev/tty ]]; then
    printf 'GitHub PAT (classic, %s; input hidden): ' "$requested_scope" >/dev/tty
    IFS= read -r -s GHCR_TOKEN </dev/tty
    printf '\n' >/dev/tty
  else
    die "an interactive terminal is required for the token prompt"
  fi
  [[ -n "$GHCR_TOKEN" ]] || die "empty token"

  printf '%s' "$GHCR_TOKEN" |
    docker --config "$AUTH_DIR" login ghcr.io \
      --username "$GITHUB_USER" --password-stdin >/dev/null
  unset GHCR_TOKEN
}

write_lock() {
  local digest="$1"
  local image_id="$2"
  local temp_lock

  umask 077
  mkdir -p -- "$(dirname -- "$LOCK_FILE")"
  temp_lock="$(mktemp "${LOCK_FILE}.XXXXXX")"
  {
    printf 'GHCR_IMAGE=%s\n' "$REMOTE_IMAGE"
    printf 'GHCR_TAG=%s\n' "$IMAGE_TAG"
    printf 'GHCR_DIGEST=%s\n' "$digest"
    printf 'LOCAL_IMAGE=%s\n' "$LOCAL_IMAGE"
    printf 'LOCAL_IMAGE_ID=%s\n' "$image_id"
    printf 'PUSHED_AT_UTC=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$temp_lock"
  mv -- "$temp_lock" "$LOCK_FILE"
  chmod 0600 "$LOCK_FILE"
}

push_image() {
  verify_local_image "$LOCAL_IMAGE"
  make_auth_dir
  login_ghcr write:packages

  local image_id digest
  image_id="$(docker image inspect --format '{{.Id}}' "$LOCAL_IMAGE")"
  PUSH_LOG="$(mktemp "${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/chrome-patch-push.XXXXXX")"

  docker tag "$LOCAL_IMAGE" "$REMOTE_TAG"
  docker --config "$AUTH_DIR" push "$REMOTE_TAG" 2>&1 | tee "$PUSH_LOG"

  digest="$(sed -nE 's/^.*digest: (sha256:[0-9a-f]{64}).*$/\1/p' "$PUSH_LOG" | tail -n 1)"
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || die "push completed without a valid OCI digest"

  docker --config "$AUTH_DIR" pull "${REMOTE_IMAGE}@${digest}" >/dev/null
  write_lock "$digest" "$image_id"
  rm -f -- "$PUSH_LOG"
  PUSH_LOG=""

  printf '\nBACKUP COMPLETE\n'
  printf 'Immutable image: %s@%s\n' "$REMOTE_IMAGE" "$digest"
  printf 'Lock file: %s\n' "$LOCK_FILE"
  if [[ "$digest" == "$PINNED_DIGEST" ]]; then
    printf 'Repository-pinned digest confirmed.\n'
  else
    printf 'WARNING: digest differs from chromium/runtime-image.lock.json.\n' >&2
    printf 'Run the full audit before reviewing a lock-file update.\n' >&2
  fi
}

restore_image() {
  local digest="${1:-}"
  if [[ -z "$digest" ]]; then
    digest="$PINNED_DIGEST"
  fi
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] ||
    die "provide a sha256 digest or use the repository-pinned digest"

  make_auth_dir
  login_ghcr read:packages

  local immutable_ref="${REMOTE_IMAGE}@${digest}"
  docker --config "$AUTH_DIR" pull "$immutable_ref"
  docker image inspect "$immutable_ref" >/dev/null 2>&1 || die "pulled digest is unavailable locally"
  docker tag "$immutable_ref" "$LOCAL_IMAGE"
  verify_local_image "$LOCAL_IMAGE"

  printf '\nRESTORE COMPLETE\n'
  printf 'Restored %s from %s\n' "$LOCAL_IMAGE" "$immutable_ref"
  printf 'Refresh the repository application layer before running its audit.\n'
  printf 'The installer performs this automatically in --restore-image mode.\n'
}

main() {
  ulimit -c 0 || true

  case "${1:-}" in
    -h|--help|help)
      usage
      exit 0
      ;;
  esac

  configure_rootless_docker

  case "${1:-}" in
    push)
      [[ $# -eq 1 ]] || { usage >&2; exit 2; }
      push_image
      ;;
    verify)
      [[ $# -eq 1 ]] || { usage >&2; exit 2; }
      verify_local_image "$LOCAL_IMAGE"
      ;;
    restore)
      [[ $# -le 2 ]] || { usage >&2; exit 2; }
      restore_image "${2:-}"
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
