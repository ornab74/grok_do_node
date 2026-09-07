#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

# Chrome Patch host bootstrap + source-build runner.
#
# Root invocation is the supported DigitalOcean path. It performs a hardware
# preflight, installs Docker from Docker's signed apt repository when needed,
# creates a dedicated non-login account, starts rootless Docker for that user,
# checks out the requested remote ref under /srv, verifies SHA256SUMS, and then
# runs this same script as the unprivileged account.

readonly PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly ORIGINAL_ARGS=("$@")
readonly RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
readonly SERVICE_USER="chromebuild"
readonly SERVICE_HOME="/home/${SERVICE_USER}"
readonly REPO_URL="https://github.com/ornab74/chrome-patch.git"
readonly APP_DIR="/srv/chrome-patch"
readonly STATE_DIR="/var/lib/chrome-patch"
readonly HOST_LOG_DIR="/var/log/chrome-patch"
readonly HOST_OUTPUT_ROOT="${STATE_DIR}/results"

readonly MIN_BUILD_CPUS=16
readonly MIN_BUILD_MEMORY_MIB=30000
readonly MIN_BUILD_DISK_MIB=180000
readonly MIN_RUNTIME_CPUS=2
readonly MIN_RUNTIME_MEMORY_MIB=3500
readonly MIN_RUNTIME_DISK_MIB=10000
readonly SMALL_BUILD_CPUS=4
readonly SMALL_BUILD_MEMORY_MIB=7000

MODE="full"
ACTIVE_LOG_FILE=""

log() {
  printf '\n[CHROME-PATCH] %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'WARNING: %s\n' "$*" >&2
}

usage() {
  cat <<'EOF'
Usage: ./install.sh [OPTION]

Bootstrap the host, build the pinned Chromium image, audit it, run Assignment 8
and the capstone, and save their output. On Ubuntu/Debian, run the full mode as
root; the script creates and switches to a dedicated rootless-Docker account.

Options:
  --preflight-only  Verify hashes and report build-node CPU/RAM/disk only.
  --local-only      Verify hashes and local policy tests; do not use Docker.
  --build-only      Bootstrap and build Chromium; do not run containers.
  --skip-build      Test an image already built from this exact source.
  --restore-image   Pull the pinned GHCR image, audit it, and run both projects.
  -h, --help        Show this help.

Environment:
  BUILD_JOBS=N      Chromium build parallelism (1-64; default: 8).
  SMALL_BUILDER=1   Allow a 4-vCPU/7-GiB build profile; use BUILD_JOBS=1 through 5.
  REF=branch        Remote branch to deploy; defaults to the caller's branch.
EOF
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

on_error() {
  local status=$?
  printf '\nFAILED: rc=%s line=%s command=%s\n' \
    "$status" "${BASH_LINENO[0]:-?}" "${BASH_COMMAND:-?}" >&2
  if [[ -n "$ACTIVE_LOG_FILE" ]]; then
    printf 'Log: %s\n' "$ACTIVE_LOG_FILE" >&2
  fi
  exit "$status"
}

capture() {
  local destination="$1"
  shift
  "$@" 2>&1 | tee "$destination"
}

parse_arguments() {
  while (($#)); do
    case "$1" in
      --preflight-only) MODE="preflight-only" ;;
      --local-only) MODE="local-only" ;;
      --build-only) MODE="build-only" ;;
      --skip-build) MODE="skip-build" ;;
      --restore-image) MODE="restore-image" ;;
      -h|--help) usage; exit 0 ;;
      *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
  done
}

resolve_ref() {
  local candidate="${REF:-}"
  if [[ -z "$candidate" && "$PROJECT_DIR" == "$APP_DIR" ]]; then
    die "do not run the installer from the managed deployment checkout; run it from your full clone or set REF explicitly"
  fi
  if [[ -z "$candidate" ]] && command -v git >/dev/null 2>&1; then
    candidate="$(git -C "$PROJECT_DIR" branch --show-current 2>/dev/null || true)"
  fi
  candidate="${candidate:-main}"
  [[ "$candidate" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || die "invalid REF: $candidate"
  [[ "$candidate" != *..* && "$candidate" != *//* && "$candidate" != */ ]] ||
    die "unsafe REF: $candidate"
  REF="$candidate"
  export REF
}

verify_repository_manifest() {
  local quiet="${1:-}"
  require_command sha256sum
  [[ -f "$PROJECT_DIR/SHA256SUMS" ]] || die "SHA256SUMS is missing"
  if [[ "$quiet" == "--quiet" ]]; then
    (cd -- "$PROJECT_DIR" && sha256sum --check --strict --quiet SHA256SUMS)
  else
    (cd -- "$PROJECT_DIR" && sha256sum --check --strict SHA256SUMS)
  fi
}

hardware_values() {
  HOST_CPUS="$(nproc)"
  HOST_MEMORY_MIB="$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)"
  local disk_path="${1:-/home}"
  [[ -e "$disk_path" ]] || disk_path="/"
  HOST_DISK_MIB="$(df -Pm "$disk_path" | awk 'NR == 2 {print $4}')"
  [[ "$HOST_CPUS" =~ ^[0-9]+$ ]] || die "could not determine CPU count"
  [[ "$HOST_MEMORY_MIB" =~ ^[0-9]+$ ]] || die "could not determine memory"
  [[ "$HOST_DISK_MIB" =~ ^[0-9]+$ ]] || die "could not determine free disk"
}

preflight_hardware() {
  local profile="$1"
  local min_cpus min_memory min_disk disk_path
  if [[ "$profile" == "build" && "${SMALL_BUILDER:-0}" == "1" ]]; then
    min_cpus=$SMALL_BUILD_CPUS
    min_memory=$SMALL_BUILD_MEMORY_MIB
    min_disk=$MIN_BUILD_DISK_MIB
  elif [[ "$profile" == "build" ]]; then
    min_cpus=$MIN_BUILD_CPUS
    min_memory=$MIN_BUILD_MEMORY_MIB
    min_disk=$MIN_BUILD_DISK_MIB
  else
    min_cpus=$MIN_RUNTIME_CPUS
    min_memory=$MIN_RUNTIME_MEMORY_MIB
    min_disk=$MIN_RUNTIME_DISK_MIB
  fi

  disk_path="$SERVICE_HOME"
  [[ -e "$disk_path" ]] || disk_path="/home"
  hardware_values "$disk_path"
  printf 'Detected: CPUs=%s RAM=%s MiB free-disk=%s MiB (%s)\n' \
    "$HOST_CPUS" "$HOST_MEMORY_MIB" "$HOST_DISK_MIB" "$disk_path"
  printf 'Required: CPUs>=%s RAM>=%s MiB free-disk>=%s MiB\n' \
    "$min_cpus" "$min_memory" "$min_disk"

  local failures=()
  ((HOST_CPUS >= min_cpus)) || failures+=("CPU")
  ((HOST_MEMORY_MIB >= min_memory)) || failures+=("RAM")
  ((HOST_DISK_MIB >= min_disk)) || failures+=("disk")
  if ((${#failures[@]})); then
    printf 'HOST PREFLIGHT FAILED: insufficient %s.\n' "$(IFS=,; echo "${failures[*]}")" >&2
    if [[ "$profile" == "build" ]]; then
      printf 'Use SMALL_BUILDER=1 with BUILD_JOBS=1 through 5 on a 4-vCPU/8-GB-or-larger host, or use the default 16-vCPU/30-GiB profile.\n' >&2
    fi
    exit 2
  fi
  printf 'HOST PREFLIGHT PASSED\n'
}

validate_build_jobs() {
  BUILD_JOBS_VALUE="${BUILD_JOBS:-8}"
  [[ "$BUILD_JOBS_VALUE" =~ ^[0-9]+$ ]] || die "BUILD_JOBS must be an integer from 1 through 64"
  ((BUILD_JOBS_VALUE >= 1 && BUILD_JOBS_VALUE <= 64)) ||
    die "BUILD_JOBS must be from 1 through 64"
  if [[ "${SMALL_BUILDER:-0}" == "1" ]]; then
    ((BUILD_JOBS_VALUE <= 5)) || die "SMALL_BUILDER=1 requires BUILD_JOBS=1 through 5"
  fi
  export BUILD_JOBS="$BUILD_JOBS_VALUE"
  printf 'Effective Chromium build jobs: %s\n' "$BUILD_JOBS_VALUE"
}

assert_environment_safe() {
  local variable
  for variable in \
    CHROME_NO_SANDBOX ALLOW_DRIVER_DOWNLOAD SE_MANAGER_PATH SELENIUM_MANAGER_PATH \
    SELENIUM_REMOTE_URL CHROME_FLAGS CHROMIUM_FLAGS CHROME_OPTS CHROMIUM_OPTS \
    COMPOSE_FILE; do
    [[ -z "${!variable:-}" ]] || die "unsafe override is set: ${variable}"
  done
  if [[ -n "${DOCKER_HOST:-}" && "${DOCKER_HOST}" != unix://* ]]; then
    die "remote or non-Unix DOCKER_HOST values are refused"
  fi
}

setup_log() {
  local directory="$1" prefix="$2"
  mkdir -p -- "$directory"
  ACTIVE_LOG_FILE="${directory}/${prefix}-${RUN_ID}.log"
  touch "$ACTIVE_LOG_FILE"
  chmod 0600 "$ACTIVE_LOG_FILE"
  exec > >(tee -a "$ACTIVE_LOG_FILE") 2>&1
  trap on_error ERR
}

load_os_release() {
  [[ -r /etc/os-release ]] || die "/etc/os-release is unavailable"
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) DIST="$ID" ;;
    *) die "automatic host bootstrap supports Ubuntu or Debian only; detected ${ID:-unknown}" ;;
  esac
  CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  [[ -n "$CODENAME" ]] || die "could not determine the OS codename"
  [[ "$(dpkg --print-architecture)" == "amd64" ]] || die "automatic bootstrap supports amd64 only"
}

docker_stack_available() {
  command -v docker >/dev/null 2>&1 &&
    command -v dockerd-rootless-setuptool.sh >/dev/null 2>&1 &&
    docker compose version >/dev/null 2>&1
}

install_docker_engine() {
  load_os_release
  export DEBIAN_FRONTEND=noninteractive

  log "Installing host prerequisites"
  apt-get update
  apt-get install -y --no-install-recommends \
    ca-certificates curl diffutils git python3 uidmap dbus-user-session slirp4netns \
    fuse-overlayfs iproute2 procps apparmor apparmor-utils acl systemd

  if docker_stack_available; then
    printf 'Docker Engine, Compose v2, and rootless extras are already installed.\n'
    return
  fi

  local conflicts=() package
  for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    if dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null | grep -q '^ii'; then
      conflicts+=("$package")
    fi
  done
  if ((${#conflicts[@]})); then
    die "conflicting Docker packages are installed: ${conflicts[*]}; review and remove them before rerunning"
  fi

  log "Installing Docker from its signed apt repository"
  install -m 0755 -d /etc/apt/keyrings
  local key_tmp
  key_tmp="$(mktemp /etc/apt/keyrings/docker.asc.XXXXXX)"
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --retry 5 \
    "https://download.docker.com/linux/${DIST}/gpg" -o "$key_tmp"
  [[ -s "$key_tmp" ]] || die "Docker repository signing key download was empty"
  chmod 0644 "$key_tmp"
  mv -f -- "$key_tmp" /etc/apt/keyrings/docker.asc

  cat > /etc/apt/sources.list.d/chrome-patch-docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/${DIST}
Suites: ${CODENAME}
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF

  apt-get update
  apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-ce-rootless-extras \
    docker-buildx-plugin docker-compose-plugin
  docker_stack_available || die "Docker rootless stack installation did not complete"
}

ensure_subid_range() {
  local file="$1" flag="$2" start
  if ! grep -q "^${SERVICE_USER}:" "$file" 2>/dev/null; then
    start="$(awk -F: '{end=$2+$3; if(end>max) max=end} END{print (max<100000?100000:max)}' \
      "$file" 2>/dev/null || echo 100000)"
    usermod "$flag" "$start-$((start + 65535))" "$SERVICE_USER"
  fi
}

configure_rootless_docker() {
  log "Creating dedicated rootless-Docker account"
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$SERVICE_USER"
  else
    usermod --home "$SERVICE_HOME" --shell /bin/bash "$SERVICE_USER"
  fi

  local group
  for group in sudo wheel docker; do
    if getent group "$group" >/dev/null 2>&1; then
      gpasswd -d "$SERVICE_USER" "$group" >/dev/null 2>&1 || true
    fi
  done
  ensure_subid_range /etc/subuid --add-subuids
  ensure_subid_range /etc/subgid --add-subgids

  SERVICE_UID="$(id -u "$SERVICE_USER")"
  SERVICE_GID="$(id -g "$SERVICE_USER")"
  RUNTIME_DIR="/run/user/${SERVICE_UID}"
  ROOTLESS_DOCKER_HOST="unix://${RUNTIME_DIR}/docker.sock"

  loginctl enable-linger "$SERVICE_USER"
  systemctl start "user-runtime-dir@${SERVICE_UID}.service"
  systemctl start "user@${SERVICE_UID}.service"
  [[ -d "$RUNTIME_DIR" ]] || die "service-user runtime directory was not created"

  if [[ -e /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]]; then
    sysctl -w kernel.apparmor_restrict_unprivileged_userns=1 >/dev/null
    install_rootlesskit_apparmor_profile
  fi

  if [[ ! -f "$SERVICE_HOME/.config/systemd/user/docker.service" ]]; then
    run_as_service dockerd-rootless-setuptool.sh install
  fi
  run_as_service systemctl --user daemon-reload
  run_as_service systemctl --user enable docker.service
  run_as_service systemctl --user restart docker.service

  local attempt
  for attempt in $(seq 1 60); do
    rootless_docker info >/dev/null 2>&1 && break
    sleep 2
  done
  rootless_docker info >/dev/null 2>&1 || die "rootless Docker did not become ready"
  ROOTLESS_SECURITY="$(rootless_docker info --format '{{json .SecurityOptions}}')"
  grep -qi rootless <<<"$ROOTLESS_SECURITY" || die "Docker is not running rootless"
  grep -qi seccomp <<<"$ROOTLESS_SECURITY" || die "Docker seccomp is unavailable"
}

install_rootlesskit_apparmor_profile() {
  local profile="/etc/apparmor.d/rootlesskit"
  local legacy="/etc/apparmor.d/usr.bin.rootlesskit"
  local backup_dir="/var/backups/chrome-patch-apparmor"
  local expected_legacy

  install -d -m 0755 /etc/apparmor.d/local
  expected_legacy="$(mktemp)"
  cat > "$expected_legacy" <<'EOF'
abi <abi/4.0>,
include <tunables/global>
/usr/bin/rootlesskit flags=(unconfined) {
  userns,
  include if exists <local/usr.bin.rootlesskit>
}
EOF

  # Older revisions of this installer created a second profile attachment.
  # Remove only that byte-for-byte known file, after backing it up. Unknown
  # administrator policy is never overwritten or removed automatically.
  if [[ -f "$legacy" ]]; then
    if cmp -s "$legacy" "$expected_legacy"; then
      install -d -m 0700 "$backup_dir"
      apparmor_parser -R "$legacy" >/dev/null 2>&1 || true
      mv -- "$legacy" "${backup_dir}/usr.bin.rootlesskit.${RUN_ID}"
    else
      rm -f -- "$expected_legacy"
      die "conflicting RootlessKit AppArmor profile at $legacy; review it manually"
    fi
  fi
  rm -f -- "$expected_legacy"

  if [[ ! -f "$profile" ]]; then
    cat > "$profile" <<'EOF'
abi <abi/4.0>,
include <tunables/global>

profile rootlesskit /usr/bin/rootlesskit flags=(unconfined) {
  include if exists <local/rootlesskit>
}
EOF
  fi
  grep -Fq 'include if exists <local/rootlesskit>' "$profile" ||
    die "$profile does not expose the supported local policy include"

  cat > /etc/apparmor.d/local/rootlesskit <<'EOF'
# Required for rootless Docker while the global unprivileged-userns
# restriction remains enabled.
userns,
EOF
  chmod 0644 "$profile" /etc/apparmor.d/local/rootlesskit
  apparmor_parser -r "$profile" ||
    die "failed to load the RootlessKit AppArmor profile"
}

run_as_service() {
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" USER="$SERVICE_USER" LOGNAME="$SERVICE_USER" \
    XDG_RUNTIME_DIR="${RUNTIME_DIR:-/run/user/$(id -u "$SERVICE_USER")}" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=${RUNTIME_DIR:-/run/user/$(id -u "$SERVICE_USER")}/bus" \
    PATH="/usr/local/bin:/usr/bin:/bin:${SERVICE_HOME}/bin" "$@"
}

rootless_docker() {
  run_as_service env DOCKER_HOST="$ROOTLESS_DOCKER_HOST" docker "$@"
}

checkout_immutable_source() {
  log "Checking out immutable source ref ${REF} under /srv"
  [[ ! -L "$APP_DIR" ]] || die "$APP_DIR must not be a symbolic link"
  install -d -m 0755 /srv
  if [[ -d "$APP_DIR/.git" ]]; then
    chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
  elif [[ -e "$APP_DIR" ]]; then
    die "$APP_DIR exists but is not a managed Git checkout"
  else
    install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$APP_DIR"
    run_as_service git clone --no-checkout "$REPO_URL" "$APP_DIR"
  fi

  run_as_service git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  run_as_service git -C "$APP_DIR" fetch --force --prune origin \
    "refs/heads/${REF}:refs/remotes/origin/${REF}"
  TARGET_COMMIT="$(run_as_service git -C "$APP_DIR" rev-parse "refs/remotes/origin/${REF}")"
  [[ "$TARGET_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "could not resolve the remote commit"
  run_as_service git -C "$APP_DIR" checkout --detach --force "$TARGET_COMMIT"
  run_as_service git -C "$APP_DIR" reset --hard "$TARGET_COMMIT"
  run_as_service git -C "$APP_DIR" clean -ffd

  (cd -- "$APP_DIR" && sha256sum --check --strict SHA256SUMS)
  SOURCE_ARCHIVE_SHA256="$(
    run_as_service git -C "$APP_DIR" archive --format=tar "$TARGET_COMMIT" |
      sha256sum | awk '{print $1}'
  )"

  chown -R root:root "$APP_DIR"
  find "$APP_DIR" -xdev -type d -exec chmod 0755 {} +
  find "$APP_DIR" -xdev -type f -exec chmod 0644 {} +
  chmod 0755 \
    "$APP_DIR/install.sh" \
    "$APP_DIR/chromium/build_chromium.sh" \
    "$APP_DIR/chromium/package_runtime.py" \
    "$APP_DIR/chromium/verify_source_fix.py" \
    "$APP_DIR/scripts/generate-sha256.sh" \
    "$APP_DIR/scripts/ghcr-image.sh" \
    "$APP_DIR/scripts/refresh-runtime-image.sh" \
    "$APP_DIR/scripts/verify-runtime-profiles.py" \
    "$APP_DIR/scripts/verify-container.sh" \
    "$APP_DIR/scripts/view-results.sh"
}

install_runtime_security_profiles() {
  log "Installing reviewed Chromium runtime policies"
  python3 "$APP_DIR/scripts/verify-runtime-profiles.py"

  if [[ -f /etc/apparmor.d/chrome-patch-browser ]] &&
     ! cmp -s "$APP_DIR/docker/chrome-patch-browser.apparmor" \
       /etc/apparmor.d/chrome-patch-browser; then
    install -d -m 0700 /var/backups/chrome-patch-apparmor
    install -m 0600 /etc/apparmor.d/chrome-patch-browser \
      "/var/backups/chrome-patch-apparmor/chrome-patch-browser.${RUN_ID}"
  fi
  install -m 0644 -o root -g root \
    "$APP_DIR/docker/chrome-patch-browser.apparmor" \
    /etc/apparmor.d/chrome-patch-browser
  apparmor_parser -r /etc/apparmor.d/chrome-patch-browser ||
    die "failed to load the Chromium AppArmor profile"

  if [[ -r /sys/kernel/security/apparmor/profiles ]]; then
    grep -q '^chrome-patch-browser ' /sys/kernel/security/apparmor/profiles ||
      die "Chromium AppArmor profile did not become active"
  else
    aa-status 2>/dev/null | grep -q 'chrome-patch-browser' ||
      die "Chromium AppArmor profile did not become active"
  fi
}

prepare_service_storage() {
  [[ ! -L "$STATE_DIR" ]] || die "$STATE_DIR must not be a symbolic link"
  [[ ! -L "$HOST_LOG_DIR" ]] || die "$HOST_LOG_DIR must not be a symbolic link"
  install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR"
  install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$HOST_OUTPUT_ROOT"
  install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$HOST_LOG_DIR"
}

install_management_wrappers() {
  cat > /usr/local/bin/chrome-patch-docker <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
exec runuser -u '$SERVICE_USER' -- env \
  HOME='$SERVICE_HOME' USER='$SERVICE_USER' LOGNAME='$SERVICE_USER' \
  XDG_RUNTIME_DIR='$RUNTIME_DIR' \
  DBUS_SESSION_BUS_ADDRESS='unix:path=$RUNTIME_DIR/bus' \
  DOCKER_HOST='$ROOTLESS_DOCKER_HOST' docker "\$@"
EOF
  chmod 0700 /usr/local/bin/chrome-patch-docker

  cat > /usr/local/bin/chrome-patch-compose <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
for argument in "\$@"; do
  if [[ "\$argument" == build ]]; then
    printf 'Refusing an unpinned wrapper build; rerun install.sh from the trusted branch checkout.\n' >&2
    exit 64
  fi
done
exec runuser -u '$SERVICE_USER' -- env \
  HOME='$SERVICE_HOME' USER='$SERVICE_USER' LOGNAME='$SERVICE_USER' \
  XDG_RUNTIME_DIR='$RUNTIME_DIR' \
  DBUS_SESSION_BUS_ADDRESS='unix:path=$RUNTIME_DIR/bus' \
  DOCKER_HOST='$ROOTLESS_DOCKER_HOST' \
  docker compose --project-directory '$APP_DIR' -f '$APP_DIR/compose.yaml' "\$@"
EOF
  chmod 0700 /usr/local/bin/chrome-patch-compose

  cat > /usr/local/bin/chrome-patch-results <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
chrome-patch-compose run --rm assignment8-results
chrome-patch-compose run --rm capstone-results
chrome-patch-compose run --rm capstone-query
EOF
  chmod 0700 /usr/local/bin/chrome-patch-results

  cat > /usr/local/bin/chrome-patch-image <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
exec runuser -u '$SERVICE_USER' -- env \
  HOME='$SERVICE_HOME' USER='$SERVICE_USER' LOGNAME='$SERVICE_USER' \
  XDG_RUNTIME_DIR='$RUNTIME_DIR' \
  DBUS_SESSION_BUS_ADDRESS='unix:path=$RUNTIME_DIR/bus' \
  DOCKER_HOST='$ROOTLESS_DOCKER_HOST' \
  bash '$APP_DIR/scripts/ghcr-image.sh' "\$@"
EOF
  chmod 0700 /usr/local/bin/chrome-patch-image
}

root_bootstrap() {
  local profile="build"
  [[ "$MODE" == "skip-build" || "$MODE" == "restore-image" ]] && profile="runtime"
  if [[ "$MODE" != "local-only" ]]; then
    log "Host hardware preflight"
    preflight_hardware "$profile"
  fi

  setup_log "$HOST_LOG_DIR" bootstrap
  log "Root bootstrap for ref ${REF}"
  if [[ "$MODE" != "local-only" ]]; then
    install_docker_engine
    configure_rootless_docker
  else
    load_os_release
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends git ca-certificates
    if ! id "$SERVICE_USER" >/dev/null 2>&1; then
      useradd --create-home --shell /bin/bash "$SERVICE_USER"
    fi
    SERVICE_UID="$(id -u "$SERVICE_USER")"
    SERVICE_GID="$(id -g "$SERVICE_USER")"
    RUNTIME_DIR="/run/user/${SERVICE_UID}"
  fi

  checkout_immutable_source
  prepare_service_storage
  if [[ "$MODE" != "local-only" ]]; then
    install_runtime_security_profiles
    install_management_wrappers
  fi

  log "Running the validated project as ${SERVICE_USER}"
  local status=0
  set +e
  run_as_service env \
    CHROME_PATCH_BOOTSTRAPPED=1 \
    CHROME_PATCH_LOG_DIR="$HOST_LOG_DIR" \
    CHROME_PATCH_OUTPUT_ROOT="$HOST_OUTPUT_ROOT" \
    DOCKER_HOST="${ROOTLESS_DOCKER_HOST:-}" \
    BUILD_JOBS="$BUILD_JOBS_VALUE" \
    SMALL_BUILDER="${SMALL_BUILDER:-0}" \
    REF="$REF" \
    bash "$APP_DIR/install.sh" "${ORIGINAL_ARGS[@]}"
  status=$?
  set -e

  if [[ $status -eq 0 ]]; then
    usermod --shell /usr/sbin/nologin "$SERVICE_USER"
    passwd -l "$SERVICE_USER" >/dev/null 2>&1 || true
    log "Bootstrap completed at commit ${TARGET_COMMIT}"
    printf 'Source archive SHA-256: %s\n' "$SOURCE_ARCHIVE_SHA256"
    printf 'Root bootstrap log: %s\n' "$ACTIVE_LOG_FILE"
    if [[ "$MODE" != "local-only" ]]; then
      printf 'View containers: chrome-patch-docker ps\n'
      printf 'View results:    chrome-patch-results\n'
      printf 'Manage image:    chrome-patch-image {verify|push|restore}\n'
    fi
  fi
  exit "$status"
}

resolve_image_digest() {
  local image="$1" digest
  docker pull "$image" >/dev/null
  digest="$(docker image inspect "$image" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
  [[ "$digest" == *@sha256:* ]] || die "could not resolve immutable digest for $image"
  printf '%s' "$digest"
}

application_run() {
  ((EUID != 0)) || die "internal error: application phase must not run as root"
  if [[ "${CHROME_PATCH_BOOTSTRAPPED:-0}" == "1" ]]; then
    [[ "$(id -un)" == "$SERVICE_USER" ]] || die "bootstrap account mismatch"
    [[ "$PROJECT_DIR" == "$APP_DIR" ]] || die "bootstrapped source must run from $APP_DIR"
  fi
  cd -- "$PROJECT_DIR"

  local log_dir="${CHROME_PATCH_LOG_DIR:-${PROJECT_DIR}/run-logs}"
  local output_root="${CHROME_PATCH_OUTPUT_ROOT:-${PROJECT_DIR}/test-output}"
  local output_dir="${output_root}/${RUN_ID}"
  mkdir -p -- "$output_dir"
  setup_log "$log_dir" install

  printf 'Chrome Patch secure installer\n'
  printf 'Run ID: %s\nMode: %s\nRef: %s\n' "$RUN_ID" "$MODE" "$REF"
  verify_repository_manifest

  log "Running local security and unit tests"
  PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/chrome-patch-pycache-${RUN_ID}" \
    python3 -m unittest discover -s tests -v

  if [[ "$MODE" == "local-only" ]]; then
    printf '\nLOCAL VALIDATION PASSED\nLog: %s\n' "$ACTIVE_LOG_FILE"
    return
  fi

  require_command docker
  docker compose version
  local docker_endpoint="${DOCKER_HOST:-}"
  [[ "$docker_endpoint" == unix://* ]] || die "Docker must use a local Unix socket"
  printf 'Docker endpoint: %s\n' "$docker_endpoint"
  docker info >/dev/null
  local security_options
  security_options="$(docker info --format '{{json .SecurityOptions}}')"
  grep -qi rootless <<<"$security_options" || die "Docker must be rootless"
  grep -qi seccomp <<<"$security_options" || die "Docker seccomp is unavailable"
  docker compose config --quiet

  if [[ "$MODE" == "restore-image" ]]; then
    log "Restoring the immutable GHCR Chromium image"
    "$PROJECT_DIR/scripts/ghcr-image.sh" restore
    log "Refreshing the current application layer without compiling Chromium"
    "$PROJECT_DIR/scripts/refresh-runtime-image.sh"
    "$PROJECT_DIR/scripts/ghcr-image.sh" verify
    DEBIAN_IMAGE="not-resolved-restored-image"
    PYTHON_IMAGE="not-resolved-restored-image"
  elif [[ "$MODE" == "skip-build" ]]; then
    log "Verifying and refreshing the existing local image"
    "$PROJECT_DIR/scripts/ghcr-image.sh" verify
    "$PROJECT_DIR/scripts/refresh-runtime-image.sh"
    "$PROJECT_DIR/scripts/ghcr-image.sh" verify
    DEBIAN_IMAGE="not-resolved-existing-image"
    PYTHON_IMAGE="not-resolved-existing-image"
  else
    log "Resolving immutable base-image digests"
    DEBIAN_IMAGE="$(resolve_image_digest debian:bookworm-slim)"
    PYTHON_IMAGE="$(resolve_image_digest python:3.13.15-slim-bookworm)"
    export DEBIAN_IMAGE PYTHON_IMAGE
    printf 'Debian base: %s\nPython base: %s\n' "$DEBIAN_IMAGE" "$PYTHON_IMAGE"

    log "Building pinned Chromium and ChromeDriver from source"
    DOCKER_BUILDKIT=1 docker compose build --pull browser-audit
  fi

  if [[ "$MODE" == "build-only" ]]; then
    printf '\nBUILD PASSED\nLog: %s\n' "$ACTIVE_LOG_FILE"
    return
  fi

  log "Auditing sandbox and running both projects"
  capture "$output_dir/namespace-preflight.txt" \
    docker compose run --rm --entrypoint /bin/sh browser-audit -ec \
      '/usr/bin/unshare --user --map-root-user /bin/true; echo nested-user-namespace=passed'
  capture "$output_dir/sandbox-audit.txt" docker compose run --rm browser-audit
  capture "$output_dir/container-tests.txt" docker compose run --rm test
  capture "$output_dir/assignment8-results.txt" docker compose run --rm assignment8-results
  capture "$output_dir/capstone-results.txt" docker compose run --rm capstone-results
  capture "$output_dir/capstone-query.txt" docker compose run --rm capstone-query

  {
    printf 'run_id=%s\nmode=%s\nref=%s\n' "$RUN_ID" "$MODE" "$REF"
    printf 'chromium_version=151.0.7922.169\nselenium_version=4.47.0\n'
    printf 'sandbox_fix_commit=4298968d02fa7a24dccc65b03071af84c5418c38\n'
    printf 'depot_tools_revision=547d7e12fe104305c7de797d5a2b4155914ad362\n'
    printf 'debian_image=%s\npython_image=%s\n' "$DEBIAN_IMAGE" "$PYTHON_IMAGE"
    printf 'docker_security_options=%s\nstatus=passed\n' "$security_options"
  } > "$output_dir/run-summary.txt"

  (
    cd -- "$output_dir"
    sha256sum -- *.txt > SHA256SUMS
  )

  printf '\nFULL VALIDATION PASSED\n'
  printf 'Full log: %s\nTest output: %s\n' "$ACTIVE_LOG_FILE" "$output_dir"
  printf 'Root view command: chrome-patch-results\n'
}

main() {
  parse_arguments "$@"
  resolve_ref
  validate_build_jobs
  assert_environment_safe

  [[ "$(uname -s)" == "Linux" ]] || die "this hardened build supports Linux only"
  [[ "$(uname -m)" == "x86_64" ]] || die "this locked build supports linux-amd64 only"
  require_command nproc
  require_command awk
  require_command df
  require_command find
  require_command sort
  require_command tee
  verify_repository_manifest --quiet

  if [[ "$MODE" == "preflight-only" ]]; then
    preflight_hardware build
    exit 0
  fi

  if ((EUID == 0)); then
    root_bootstrap
  fi

  local profile="build"
  [[ "$MODE" == "skip-build" || "$MODE" == "restore-image" ]] && profile="runtime"
  if [[ "$MODE" != "local-only" ]]; then
    log "Host hardware preflight"
    preflight_hardware "$profile"
  fi
  application_run
}

main "$@"
