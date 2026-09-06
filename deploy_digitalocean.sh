#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SERVICE_USER="grokbrowser"
readonly SERVICE_HOME="/home/${SERVICE_USER}"
readonly APP_DIR="/srv/grok-patched-node"
readonly IMAGE="ghcr.io/ornab74/chrome-patch-chromium@sha256:f212beff4487370a6c026a1834f779657e76c7091c6ae42b4d2cee8cfe42d2f3"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"

SERVICE_UID=""
RUNTIME_DIR=""
ROOTLESS_DOCKER_HOST=""
DIST=""
CODENAME=""

log() { printf '\n[GROK-PATCH] %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die "run this deployment script as root"
[[ "$(uname -s)" == Linux ]] || die "Linux required"
[[ "$(uname -m)" == x86_64 ]] || die "the pinned image is linux/amd64 only"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"

load_os_release() {
  . /etc/os-release
  case "${ID:-}" in ubuntu|debian) DIST="$ID" ;; *) die "Ubuntu or Debian required" ;; esac
  CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  [[ -n "$CODENAME" ]] || die "cannot determine distro codename"
  [[ "$(dpkg --print-architecture)" == amd64 ]] || die "amd64 required"
}

preflight() {
  local cpus mem_mib disk_mib
  cpus="$(nproc)"
  mem_mib="$(awk '/MemTotal:/ {print int($2/1024)}' /proc/meminfo)"
  disk_mib="$(df -Pm /var 2>/dev/null | awk 'NR==2 {print $4}')"
  (( cpus >= 2 )) || die "runtime needs at least 2 vCPUs"
  (( mem_mib >= 3500 )) || die "runtime needs at least about 3.5 GiB RAM"
  (( disk_mib >= 8000 )) || die "runtime needs at least 8 GiB free under /var"
}

install_docker_stack() {
  load_os_release
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends \
    ca-certificates curl uidmap dbus-user-session slirp4netns fuse-overlayfs \
    iproute2 procps apparmor apparmor-utils acl systemd

  if command -v docker >/dev/null 2>&1 && \
     command -v dockerd-rootless-setuptool.sh >/dev/null 2>&1 && \
     docker compose version >/dev/null 2>&1; then
    return
  fi

  local conflicts=() p
  for p in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    if dpkg-query -W -f='${db:Status-Abbrev}' "$p" 2>/dev/null | grep -q '^ii'; then conflicts+=("$p"); fi
  done
  ((${#conflicts[@]} == 0)) || die "conflicting Docker packages installed: ${conflicts[*]}"

  install -m 0755 -d /etc/apt/keyrings
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --retry 5 \
    "https://download.docker.com/linux/${DIST}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod 0644 /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/grok-patch-docker.sources <<APT
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
}

ensure_subid_range() {
  local file="$1" flag="$2" start
  if ! grep -q "^${SERVICE_USER}:" "$file" 2>/dev/null; then
    start="$(awk -F: '{end=$2+$3; if(end>max) max=end} END{print (max<100000?100000:max)}' "$file" 2>/dev/null || echo 100000)"
    usermod "$flag" "$start-$((start + 65535))" "$SERVICE_USER"
  fi
}

run_as_service() {
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" USER="$SERVICE_USER" LOGNAME="$SERVICE_USER" \
    XDG_RUNTIME_DIR="$RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=${RUNTIME_DIR}/bus" \
    DOCKER_HOST="$ROOTLESS_DOCKER_HOST" \
    PATH="/usr/local/bin:/usr/bin:/bin:${SERVICE_HOME}/bin" "$@"
}

install_rootlesskit_apparmor_profile() {
  install -d -m 0755 /etc/apparmor.d/local
  if [[ ! -f /etc/apparmor.d/rootlesskit ]]; then
    cat > /etc/apparmor.d/rootlesskit <<'AA'
abi <abi/4.0>,
include <tunables/global>
profile rootlesskit /usr/bin/rootlesskit flags=(unconfined) {
  include if exists <local/rootlesskit>
}
AA
  fi
  grep -Fq 'include if exists <local/rootlesskit>' /etc/apparmor.d/rootlesskit || \
    die "existing /etc/apparmor.d/rootlesskit does not expose the supported local include"
  cat > /etc/apparmor.d/local/rootlesskit <<'AA'
# Permit RootlessKit user namespaces while the global restriction remains on.
userns,
AA
  chmod 0644 /etc/apparmor.d/rootlesskit /etc/apparmor.d/local/rootlesskit
  apparmor_parser -r /etc/apparmor.d/rootlesskit
}

configure_rootless_docker() {
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$SERVICE_USER"
  fi
  local group
  for group in sudo wheel docker; do
    if getent group "$group" >/dev/null 2>&1; then gpasswd -d "$SERVICE_USER" "$group" >/dev/null 2>&1 || true; fi
  done
  ensure_subid_range /etc/subuid --add-subuids
  ensure_subid_range /etc/subgid --add-subgids

  SERVICE_UID="$(id -u "$SERVICE_USER")"
  RUNTIME_DIR="/run/user/${SERVICE_UID}"
  ROOTLESS_DOCKER_HOST="unix://${RUNTIME_DIR}/docker.sock"

  loginctl enable-linger "$SERVICE_USER"
  systemctl start "user-runtime-dir@${SERVICE_UID}.service"
  systemctl start "user@${SERVICE_UID}.service"

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

  for _ in $(seq 1 60); do
    if run_as_service docker info >/dev/null 2>&1; then break; fi
    sleep 1
  done
  run_as_service docker info >/dev/null 2>&1 || die "rootless Docker did not become ready"
  local security
  security="$(run_as_service docker info --format '{{json .SecurityOptions}}')"
  grep -qi rootless <<<"$security" || die "Docker is not rootless"
  grep -qi seccomp <<<"$security" || die "Docker seccomp is unavailable"
}

install_project() {
  log "Installing Grok node files into ${APP_DIR}"
  install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$APP_DIR" "$APP_DIR/docker"
  install -m 0640 -o "$SERVICE_USER" -g "$SERVICE_USER" "$SCRIPT_DIR/compose.grok.yaml" "$APP_DIR/compose.grok.yaml"
  install -m 0640 -o "$SERVICE_USER" -g "$SERVICE_USER" "$SCRIPT_DIR/grok_tui.py" "$APP_DIR/grok_tui.py"
  install -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$SCRIPT_DIR/run-grok.sh" "$APP_DIR/run-grok.sh"
  install -m 0640 -o "$SERVICE_USER" -g "$SERVICE_USER" "$SCRIPT_DIR/docker/Dockerfile.proxy" "$APP_DIR/docker/Dockerfile.proxy"
  install -m 0640 -o "$SERVICE_USER" -g "$SERVICE_USER" "$SCRIPT_DIR/docker/squid-grok.conf" "$APP_DIR/docker/squid-grok.conf"
  install -m 0644 -o root -g root "$SCRIPT_DIR/docker/chromium-seccomp.json" "$APP_DIR/docker/chromium-seccomp.json"
  install -m 0644 -o root -g root "$SCRIPT_DIR/docker/chrome-patch-browser.apparmor" "$APP_DIR/docker/chrome-patch-browser.apparmor"
}

install_browser_apparmor() {
  log "Loading reviewed Chromium AppArmor profile from the supplied ZIP"
  if [[ -f /etc/apparmor.d/chrome-patch-browser ]] && \
     ! cmp -s "$APP_DIR/docker/chrome-patch-browser.apparmor" /etc/apparmor.d/chrome-patch-browser; then
    install -d -m 0700 /var/backups/grok-patch-apparmor
    install -m 0600 /etc/apparmor.d/chrome-patch-browser \
      "/var/backups/grok-patch-apparmor/chrome-patch-browser.${RUN_ID}"
  fi
  install -m 0644 "$APP_DIR/docker/chrome-patch-browser.apparmor" /etc/apparmor.d/chrome-patch-browser
  apparmor_parser -r /etc/apparmor.d/chrome-patch-browser
}

pull_and_verify() {
  log "Pulling public immutable GHCR image"
  run_as_service docker pull "$IMAGE"
  log "Verifying packaged Chromium/ChromeDriver manifest"
  run_as_service docker run --rm \
    --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --entrypoint /bin/sh "$IMAGE" -ec \
    '/opt/chromium/chrome --version; /opt/chromium/chromedriver --version; cd /opt/chromium; sha256sum --check --strict manifest.sha256 >/dev/null; echo manifest=OK'
}

build_proxy_and_audit() {
  log "Building narrow egress proxy and auditing Chromium sandbox"
  run_as_service bash -lc "cd '$APP_DIR' && docker compose -f compose.grok.yaml build egress-proxy"
  run_as_service bash -lc "cd '$APP_DIR' && docker compose -f compose.grok.yaml run --rm browser-audit"
}

install_wrapper() {
  cat > /usr/local/bin/grok-tui <<EOF2
#!/usr/bin/env bash
set -Eeuo pipefail
exec runuser -u ${SERVICE_USER} -- env \\
  HOME=${SERVICE_HOME} USER=${SERVICE_USER} LOGNAME=${SERVICE_USER} \\
  XDG_RUNTIME_DIR=${RUNTIME_DIR} DBUS_SESSION_BUS_ADDRESS=unix:path=${RUNTIME_DIR}/bus \\
  DOCKER_HOST=${ROOTLESS_DOCKER_HOST} PATH=/usr/local/bin:/usr/bin:/bin:${SERVICE_HOME}/bin \\
  ${APP_DIR}/run-grok.sh
EOF2
  chmod 0755 /usr/local/bin/grok-tui
}

main() {
  printf '%s  %s\n' \
    '6a9bef77a628595b204ae68d7e14a57015860cfece49de500830c290005e921e' "$SCRIPT_DIR/docker/chrome-patch-browser.apparmor" \
    '093bf753187a00b5bf180ae7b60a72a421914366f787d3b2bcce4842f3c23e97' "$SCRIPT_DIR/docker/chromium-seccomp.json" | sha256sum -c - >/dev/null || \
    die "security profile files do not match the supplied ZIP"
  preflight
  log "Installing Docker/rootless prerequisites"
  install_docker_stack
  configure_rootless_docker
  install_project
  install_browser_apparmor
  pull_and_verify
  build_proxy_and_audit
  install_wrapper
  printf '\nDEPLOYMENT PASSED\n\n'
  printf 'Run the terminal client with:\n  grok-tui\n\n'
  printf 'The Chromebook only needs an SSH terminal to this node; grok.com executes in the patched container.\n'
}

main "$@"
