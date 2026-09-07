#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SOURCE_DIR="$SCRIPT_DIR/chrome-patch-src"
readonly SERVICE_USER="grokbrowser"
readonly SERVICE_HOME="/home/${SERVICE_USER}"
readonly APP_DIR="/srv/grok-secure-node"
readonly IMMUTABLE_IMAGE="ghcr.io/ornab74/chrome-patch-chromium@sha256:f212beff4487370a6c026a1834f779657e76c7091c6ae42b4d2cee8cfe42d2f3"
readonly REFRESHED_IMAGE="chrome-patch-refreshed:151.0.7922.169"
readonly VAULT_IMAGE="grok-chrome-patch:151.0.7922.169-vault"
readonly RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
readonly RESTORED_BASE="chrome-patch-restored-base:${RUN_ID}"
readonly BACKUP_DIR="/var/backups/grok-vault"
readonly MIN_CPUS=2
readonly MIN_MEMORY_MIB=3500
readonly MIN_DISK_MIB=10000

SERVICE_UID=""
SERVICE_GID=""
RUNTIME_DIR=""
ROOTLESS_DOCKER_HOST=""
PHASE="startup"

log(){ printf '\n[GROK SECURE NODE] %s\n' "$*"; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
warn(){ printf 'WARNING: %s\n' "$*" >&2; }

on_error(){
  local rc=$?
  printf '\nINSTALL FAILED: phase=%s rc=%s line=%s command=%s\n' \
    "$PHASE" "$rc" "${BASH_LINENO[0]:-?}" "${BASH_COMMAND:-?}" >&2
  if [[ -n "${ROOTLESS_DOCKER_HOST:-}" && -n "${SERVICE_UID:-}" ]]; then
    printf '%s\n' '-- rootless Docker security --' >&2
    rootless_docker info --format '{{json .SecurityOptions}}' >&2 2>/dev/null || true
  fi
  printf '%s\n' '-- recent AppArmor/seccomp/userns messages --' >&2
  journalctl -k --no-pager -n 180 2>/dev/null | grep -Ei 'apparmor|seccomp|userns|denied|audit' | tail -60 >&2 || true
  exit "$rc"
}
trap on_error ERR

require_root(){ [[ ${EUID} -eq 0 ]] || die 'run this installer as root (sudo ./install.sh)'; }
require_amd64(){
  [[ "$(uname -s)" == Linux ]] || die 'Linux is required'
  [[ "$(uname -m)" == x86_64 ]] || die 'linux/amd64 is required by the pinned Chromium image'
}

hardware_preflight(){
  PHASE="hardware-preflight"
  local cpus memory disk
  cpus="$(nproc)"
  memory="$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)"
  disk="$(df -Pm / | awk 'NR==2 {print $4}')"
  printf 'Detected: CPUs=%s RAM=%s MiB free-disk=%s MiB\n' "$cpus" "$memory" "$disk"
  (( cpus >= MIN_CPUS )) || die "need at least ${MIN_CPUS} vCPUs"
  (( memory >= MIN_MEMORY_MIB )) || die "need at least ${MIN_MEMORY_MIB} MiB RAM"
  (( disk >= MIN_DISK_MIB )) || die "need at least ${MIN_DISK_MIB} MiB free disk"
}

restore_canonical_dockerignore_if_missing(){
  local f="$SCRIPT_DIR/.dockerignore" expected actual
  [[ -e "$f" ]] && return 0

  # Some Git-based transports can accidentally omit this root dotfile while
  # leaving SHA256SUMS intact. Recreate only the one canonical build-control
  # file, then verify it against the manifest before trusting the bundle.
  expected="$(awk '$2 == "./.dockerignore" {print $1}' "$SCRIPT_DIR/SHA256SUMS")"
  [[ "$expected" == "9e98387b577bed5d55ca41bc334eef7c2bdb0368bf91a8890fd476e00152745e" ]] \
    || die 'manifest does not contain the expected canonical .dockerignore hash'

  cat >"$f" <<'EOF_DOCKERIGNORE'
chrome-patch-src
refresh-context
security
README.md
install.sh
SHA256SUMS
*.zip
*.tar.gz
EOF_DOCKERIGNORE
  chmod 600 "$f"
  actual="$(sha256sum "$f" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || die 'failed to reconstruct canonical .dockerignore'
  printf 'Restored missing ./.dockerignore and verified sha256=%s\n' "$actual"
}

scrub_transient_python_bytecode(){
  # Python bytecode is host/interpreter-specific transient state. It must never
  # participate in the immutable source manifest or influence imports during
  # installation.
  find "$SCRIPT_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
  find "$SCRIPT_DIR" -depth -type d -name '__pycache__' -empty -delete 2>/dev/null || true
}

reject_transient_manifest_entries(){
  if grep -Eq '(^|/)(__pycache__/|[^/]+\.py[co]([[:space:]]|$))' "$SCRIPT_DIR/SHA256SUMS"; then
    die 'bundle SHA256SUMS illegally contains transient Python bytecode/cache entries'
  fi
}

verify_bundle(){
  PHASE="bundle-integrity"
  [[ -f "$SCRIPT_DIR/SHA256SUMS" ]] || die 'bundle SHA256SUMS is missing'
  scrub_transient_python_bytecode
  reject_transient_manifest_entries
  restore_canonical_dockerignore_if_missing
  (cd "$SCRIPT_DIR" && sha256sum --check --strict SHA256SUMS)
  [[ -f "$SOURCE_DIR/SHA256SUMS" ]] || die 'bundled chrome-patch source snapshot is missing'
}

verify_source_file(){
  local rel="$1" expected actual
  expected="$(awk -v p="./${rel}" '$2 == p {print $1}' "$SOURCE_DIR/SHA256SUMS")"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || die "source manifest has no trusted entry for ${rel}"
  actual="$(sha256sum "$SOURCE_DIR/$rel" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || die "chrome-patch source integrity failure: ${rel}"
}

verify_security_source(){
  PHASE="security-source-validation"
  local rel
  for rel in \
    requirements.txt \
    common/__init__.py common/browser.py common/sandbox_probe.py common/security_gate.py \
    docker/Dockerfile.runtime-refresh docker/entrypoint.py docker/chromium-policy.json \
    docker/chromium-seccomp.json docker/chrome-patch-browser.apparmor \
    scripts/verify-runtime-profiles.py compose.yaml; do
    verify_source_file "$rel"
  done
  python3 "$SOURCE_DIR/scripts/verify-runtime-profiles.py"
}

load_os_release(){
  [[ -r /etc/os-release ]] || die '/etc/os-release is unavailable'
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) DIST="$ID" ;;
    *) die "automatic bootstrap supports Ubuntu/Debian only; detected ${ID:-unknown}" ;;
  esac
  CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  [[ -n "$CODENAME" ]] || die 'could not determine OS codename'
  [[ "$(dpkg --print-architecture)" == amd64 ]] || die 'amd64 packages are required'
}

docker_stack_available(){
  command -v docker >/dev/null 2>&1 &&
  command -v dockerd-rootless-setuptool.sh >/dev/null 2>&1 &&
  docker compose version >/dev/null 2>&1
}

install_host_packages(){
  PHASE="host-packages"
  load_os_release
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends \
    ca-certificates curl uidmap dbus-user-session slirp4netns fuse-overlayfs \
    iproute2 procps apparmor apparmor-utils acl systemd python3 gnupg

  if docker_stack_available; then
    log 'Docker Engine/Compose/rootless extras already available'
    return
  fi

  local conflicts=() pkg
  for pkg in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    if dpkg-query -W -f='${db:Status-Abbrev}' "$pkg" 2>/dev/null | grep -q '^ii'; then
      conflicts+=("$pkg")
    fi
  done
  if ((${#conflicts[@]})); then
    die "conflicting Docker packages are installed: ${conflicts[*]}. Remove/review them before rerunning."
  fi

  install -m 0755 -d /etc/apt/keyrings
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --retry 5 \
    "https://download.docker.com/linux/${DIST}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod 0644 /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/grok-secure-node-docker.sources <<APT
Types: deb
URIs: https://download.docker.com/linux/${DIST}
Suites: ${CODENAME}
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
APT
  apt-get update
  apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-ce-rootless-extras \
    docker-buildx-plugin docker-compose-plugin
  docker_stack_available || die 'Docker rootless stack installation did not complete'
}

ensure_subid_range(){
  local file="$1" flag="$2" start
  if ! grep -q "^${SERVICE_USER}:" "$file" 2>/dev/null; then
    start="$(awk -F: '{end=$2+$3; if(end>max) max=end} END{print (max<100000?100000:max)}' "$file" 2>/dev/null || echo 100000)"
    usermod "$flag" "$start-$((start+65535))" "$SERVICE_USER"
  fi
}

run_as_service(){
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" USER="$SERVICE_USER" LOGNAME="$SERVICE_USER" \
    XDG_RUNTIME_DIR="${RUNTIME_DIR:-/run/user/$(id -u "$SERVICE_USER")}" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=${RUNTIME_DIR:-/run/user/$(id -u "$SERVICE_USER")}/bus" \
    DOCKER_HOST="${ROOTLESS_DOCKER_HOST:-unix:///run/user/$(id -u "$SERVICE_USER")/docker.sock}" \
    PATH="/usr/local/bin:/usr/bin:/bin:${SERVICE_HOME}/bin" "$@"
}

rootless_docker(){ run_as_service docker "$@"; }

install_rootlesskit_apparmor_profile(){
  local profile=/etc/apparmor.d/rootlesskit
  local legacy=/etc/apparmor.d/usr.bin.rootlesskit
  local backup=/var/backups/grok-rootlesskit-apparmor
  local expected_legacy
  install -d -m 0755 /etc/apparmor.d/local
  expected_legacy="$(mktemp)"
  cat > "$expected_legacy" <<'AA'
abi <abi/4.0>,
include <tunables/global>
/usr/bin/rootlesskit flags=(unconfined) {
  userns,
  include if exists <local/usr.bin.rootlesskit>
}
AA
  if [[ -f "$legacy" ]]; then
    if cmp -s "$legacy" "$expected_legacy"; then
      install -d -m 0700 "$backup"
      apparmor_parser -R "$legacy" >/dev/null 2>&1 || true
      mv "$legacy" "$backup/usr.bin.rootlesskit.${RUN_ID}"
    else
      rm -f "$expected_legacy"
      die "unknown AppArmor policy exists at $legacy; refusing to overwrite it"
    fi
  fi
  rm -f "$expected_legacy"

  if [[ ! -f "$profile" ]]; then
    cat > "$profile" <<'AA'
abi <abi/4.0>,
include <tunables/global>
profile rootlesskit /usr/bin/rootlesskit flags=(unconfined) {
  include if exists <local/rootlesskit>
}
AA
  fi
  grep -Fq 'include if exists <local/rootlesskit>' "$profile" || die "$profile lacks the supported local include"
  cat > /etc/apparmor.d/local/rootlesskit <<'AA'
# Rootless Docker's outer user namespace. Global userns restriction stays ON.
userns,
AA
  chmod 0644 "$profile" /etc/apparmor.d/local/rootlesskit
  apparmor_parser -r "$profile" || die 'failed to load RootlessKit AppArmor profile'
}

configure_rootless_docker(){
  PHASE="rootless-docker"
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$SERVICE_USER"
    passwd -l "$SERVICE_USER" >/dev/null 2>&1 || true
  else
    usermod --home "$SERVICE_HOME" --shell /bin/bash "$SERVICE_USER"
  fi
  local group
  for group in sudo wheel docker; do
    getent group "$group" >/dev/null 2>&1 && gpasswd -d "$SERVICE_USER" "$group" >/dev/null 2>&1 || true
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
  [[ -d "$RUNTIME_DIR" ]] || die 'service-user runtime directory was not created'

  if [[ -e /proc/sys/kernel/apparmor_restrict_unprivileged_userns ]]; then
    cat > /etc/sysctl.d/90-grok-secure-userns.conf <<'SYSCTL'
kernel.apparmor_restrict_unprivileged_userns=1
SYSCTL
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
  rootless_docker info >/dev/null 2>&1 || die 'rootless Docker did not become ready'
  local security
  security="$(rootless_docker info --format '{{json .SecurityOptions}}')"
  grep -qi rootless <<<"$security" || die 'Docker daemon is not rootless'
  grep -qi seccomp <<<"$security" || die 'Docker seccomp support is unavailable'
}

install_application_files(){
  PHASE="application-files"
  install -d -m 0755 -o root -g root "$APP_DIR" "$APP_DIR/docker" "$APP_DIR/security" "$APP_DIR/refresh-context/common" "$APP_DIR/refresh-context/docker"

  install -m 0644 "$SCRIPT_DIR/grok_vault_tui.py" "$APP_DIR/grok_vault_tui.py"
  install -m 0644 "$SCRIPT_DIR/Dockerfile.vault" "$APP_DIR/Dockerfile.vault"
  install -m 0644 "$SCRIPT_DIR/compose.yaml" "$APP_DIR/compose.yaml"
  install -m 0644 "$SCRIPT_DIR/.dockerignore" "$APP_DIR/.dockerignore"
  install -m 0755 "$SCRIPT_DIR/run-grok.sh" "$APP_DIR/run-grok.sh"
  install -m 0644 "$SCRIPT_DIR/docker/Dockerfile.proxy" "$APP_DIR/docker/Dockerfile.proxy"
  install -m 0644 "$SCRIPT_DIR/docker/squid-grok.conf" "$APP_DIR/docker/squid-grok.conf"

  # Minimal application refresh context. Only security/browser files actually
  # required for the Grok client are copied into the restored runtime image.
  rm -rf "$APP_DIR/refresh-context"
  install -d -m 0755 "$APP_DIR/refresh-context/common" "$APP_DIR/refresh-context/docker"
  install -m 0644 "$SOURCE_DIR/requirements.txt" "$APP_DIR/refresh-context/requirements.txt"
  for f in __init__.py browser.py sandbox_probe.py security_gate.py; do
    install -m 0644 "$SOURCE_DIR/common/$f" "$APP_DIR/refresh-context/common/$f"
  done
  install -m 0644 "$SOURCE_DIR/docker/Dockerfile.runtime-refresh" "$APP_DIR/refresh-context/docker/Dockerfile.runtime-refresh"
  install -m 0644 "$SOURCE_DIR/docker/entrypoint.py" "$APP_DIR/refresh-context/docker/entrypoint.py"
  install -m 0644 "$SOURCE_DIR/docker/chromium-policy.json" "$APP_DIR/refresh-context/docker/chromium-policy.json"

  install -m 0644 "$SOURCE_DIR/docker/chromium-seccomp.json" "$APP_DIR/security/chromium-seccomp.json"
  install -m 0644 "$SOURCE_DIR/docker/chrome-patch-browser.apparmor" "$APP_DIR/security/chrome-patch-browser.apparmor"
  install -m 0644 "$SOURCE_DIR/docker/chrome-patch-browser.apparmor" /etc/apparmor.d/chrome-patch-browser
  apparmor_parser -r /etc/apparmor.d/chrome-patch-browser
  if [[ -r /sys/kernel/security/apparmor/profiles ]]; then
    grep -q '^chrome-patch-browser ' /sys/kernel/security/apparmor/profiles || die 'chrome-patch-browser AppArmor profile did not load'
  else
    aa-status 2>/dev/null | grep -q 'chrome-patch-browser' || die 'chrome-patch-browser AppArmor profile did not load'
  fi

  chown -R root:root "$APP_DIR"
  find "$APP_DIR" -type d -exec chmod 0755 {} +
  find "$APP_DIR" -type f -exec chmod go-w {} +
  chmod 0755 "$APP_DIR/run-grok.sh"
}

pull_and_refresh(){
  PHASE="runtime-image"
  log 'Pulling immutable public Chromium image (no GHCR credentials required)'
  rootless_docker pull "$IMMUTABLE_IMAGE"

  log 'Verifying packaged Chromium manifest before application refresh'
  run_as_service docker run --rm --entrypoint /bin/sh "$IMMUTABLE_IMAGE" -ec \
    'cd /opt/chromium && sha256sum --check --strict manifest.sha256'

  rootless_docker tag "$IMMUTABLE_IMAGE" "$RESTORED_BASE"
  log 'Refreshing current browser/security application layer without recompiling Chromium'
  run_as_service env DOCKER_BUILDKIT=1 docker build \
    --pull=false \
    --file "$APP_DIR/refresh-context/docker/Dockerfile.runtime-refresh" \
    --build-arg "BASE_IMAGE=$RESTORED_BASE" \
    --tag "$REFRESHED_IMAGE" \
    "$APP_DIR/refresh-context"

  log 'Building Grok vault TUI layer'
  run_as_service env DOCKER_BUILDKIT=1 docker build \
    --pull=true \
    --file "$APP_DIR/Dockerfile.vault" \
    --build-arg "BASE_IMAGE=$REFRESHED_IMAGE" \
    --tag "$VAULT_IMAGE" \
    "$APP_DIR"
  rootless_docker image rm "$RESTORED_BASE" >/dev/null 2>&1 || true

  local expected actual
  expected="$(sha256sum "$SOURCE_DIR/common/sandbox_probe.py" | awk '{print $1}')"
  actual="$(rootless_docker run --rm --entrypoint sha256sum "$VAULT_IMAGE" /app/common/sandbox_probe.py | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || die 'derived image contains the wrong sandbox probe'
  printf 'sandbox_probe_sha256=%s\n' "$actual"
}

build_and_audit(){
  PHASE="compose-audit"
  run_as_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml config --quiet"
  run_as_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml build egress-proxy"
  log 'Squid policy parse test under the runtime service'
  run_as_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml run --rm --no-deps --entrypoint /usr/sbin/squid egress-proxy -k parse -f /etc/squid/squid.conf"
  run_as_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml run --rm vault-init >/dev/null"

  log 'Nested user-namespace preflight'
  run_as_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml run --rm --entrypoint /bin/sh browser-audit -ec '/usr/bin/unshare --user --map-root-user /bin/true; echo nested-user-namespace=passed'"

  log 'Chromium chrome://sandbox self-test'
  run_as_service bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml run --rm browser-audit"
}

install_wrappers(){
  PHASE="wrappers"
  install -d -m 0700 -o root -g root "$BACKUP_DIR"

  cat > /usr/local/bin/grok-tui <<WRAP
#!/usr/bin/env bash
set -Eeuo pipefail
[[ \${EUID} -eq 0 ]] || { echo 'run as root: sudo grok-tui' >&2; exit 1; }
ulimit -c 0 || true
exec runuser -u $SERVICE_USER -- env HOME=$SERVICE_HOME USER=$SERVICE_USER LOGNAME=$SERVICE_USER XDG_RUNTIME_DIR=$RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$RUNTIME_DIR/bus DOCKER_HOST=$ROOTLESS_DOCKER_HOST PATH=/usr/local/bin:/usr/bin:/bin:$SERVICE_HOME/bin $APP_DIR/run-grok.sh
WRAP

  cat > /usr/local/bin/grok-audit <<WRAP
#!/usr/bin/env bash
set -Eeuo pipefail
[[ \${EUID} -eq 0 ]] || { echo 'run as root: sudo grok-audit' >&2; exit 1; }
exec runuser -u $SERVICE_USER -- env HOME=$SERVICE_HOME USER=$SERVICE_USER LOGNAME=$SERVICE_USER XDG_RUNTIME_DIR=$RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$RUNTIME_DIR/bus DOCKER_HOST=$ROOTLESS_DOCKER_HOST PATH=/usr/local/bin:/usr/bin:/bin:$SERVICE_HOME/bin bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml run --rm browser-audit"
WRAP

  cat > /usr/local/bin/grok-status <<WRAP
#!/usr/bin/env bash
set -Eeuo pipefail
[[ \${EUID} -eq 0 ]] || { echo 'run as root: sudo grok-status' >&2; exit 1; }
exec runuser -u $SERVICE_USER -- env HOME=$SERVICE_HOME USER=$SERVICE_USER LOGNAME=$SERVICE_USER XDG_RUNTIME_DIR=$RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$RUNTIME_DIR/bus DOCKER_HOST=$ROOTLESS_DOCKER_HOST PATH=/usr/local/bin:/usr/bin:/bin:$SERVICE_HOME/bin bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml ps && echo && docker info --format '{{json .SecurityOptions}}'"
WRAP

  cat > /usr/local/bin/grok-proxy-logs <<WRAP
#!/usr/bin/env bash
set -Eeuo pipefail
[[ \${EUID} -eq 0 ]] || { echo 'run as root: sudo grok-proxy-logs' >&2; exit 1; }
exec runuser -u $SERVICE_USER -- env HOME=$SERVICE_HOME USER=$SERVICE_USER LOGNAME=$SERVICE_USER XDG_RUNTIME_DIR=$RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$RUNTIME_DIR/bus DOCKER_HOST=$ROOTLESS_DOCKER_HOST PATH=/usr/local/bin:/usr/bin:/bin:$SERVICE_HOME/bin bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml logs --no-color --tail=200 egress-proxy"
WRAP

  cat > /usr/local/bin/grok-stop <<WRAP
#!/usr/bin/env bash
set -Eeuo pipefail
[[ \${EUID} -eq 0 ]] || { echo 'run as root: sudo grok-stop' >&2; exit 1; }
exec runuser -u $SERVICE_USER -- env HOME=$SERVICE_HOME USER=$SERVICE_USER LOGNAME=$SERVICE_USER XDG_RUNTIME_DIR=$RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$RUNTIME_DIR/bus DOCKER_HOST=$ROOTLESS_DOCKER_HOST PATH=/usr/local/bin:/usr/bin:/bin:$SERVICE_HOME/bin bash -lc "cd '$APP_DIR' && docker compose -f compose.yaml down --remove-orphans"
WRAP

  cat > /usr/local/bin/grok-vault-backup <<WRAP
#!/usr/bin/env bash
set -Eeuo pipefail
[[ \${EUID} -eq 0 ]] || { echo 'run as root: sudo grok-vault-backup' >&2; exit 1; }
stamp=\$(date -u +%Y%m%dT%H%M%SZ)
out="$BACKUP_DIR/grok-secure-vault-\${stamp}.tar.gz"
running=\$(runuser -u $SERVICE_USER -- env HOME=$SERVICE_HOME USER=$SERVICE_USER LOGNAME=$SERVICE_USER XDG_RUNTIME_DIR=$RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$RUNTIME_DIR/bus DOCKER_HOST=$ROOTLESS_DOCKER_HOST PATH=/usr/local/bin:/usr/bin:/bin:$SERVICE_HOME/bin docker ps --filter label=com.docker.compose.service=grok-tui --format '{{.ID}}')
[[ -z "\$running" ]] || { echo 'close grok-tui before taking a vault backup' >&2; exit 1; }
runuser -u $SERVICE_USER -- env HOME=$SERVICE_HOME USER=$SERVICE_USER LOGNAME=$SERVICE_USER XDG_RUNTIME_DIR=$RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS=unix:path=$RUNTIME_DIR/bus DOCKER_HOST=$ROOTLESS_DOCKER_HOST PATH=/usr/local/bin:/usr/bin:/bin:$SERVICE_HOME/bin docker run --rm --network none -v grok-secure-vault-v1:/vault:ro debian:bookworm-slim tar -C /vault -czf - . > "\$out"
chmod 0600 "\$out"
echo "Encrypted vault backup: \$out"
sha256sum "\$out"
WRAP

  chmod 0755 /usr/local/bin/grok-tui /usr/local/bin/grok-audit /usr/local/bin/grok-status /usr/local/bin/grok-proxy-logs /usr/local/bin/grok-stop /usr/local/bin/grok-vault-backup
}

main(){
  require_root
  require_amd64
  hardware_preflight
  verify_bundle
  verify_security_source
  install_host_packages
  configure_rootless_docker
  install_application_files
  pull_and_refresh
  build_and_audit
  install_wrappers
  PHASE="complete"
  trap - ERR
  printf '\nINSTALL PASSED\n\n'
  printf '%s\n' \
    'Start:          sudo grok-tui' \
    'Sandbox audit:  sudo grok-audit' \
    'Status:         sudo grok-status' \
    'Proxy logs:     sudo grok-proxy-logs' \
    'Stop proxy:     sudo grok-stop' \
    'Vault backup:   sudo grok-vault-backup'
}

main "$@"
