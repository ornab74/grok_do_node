#!/usr/bin/env bash
set -euo pipefail

# Defensive hardening for a Debian Crostini container.
# This script does NOT patch ChromeOS host components (cicerone/crosvm/Termina).
# It reduces the blast radius inside the Debian guest and verifies important boundaries.

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  exec sudo --preserve-env=PATH bash "$0" "$@"
fi

export DEBIAN_FRONTEND=noninteractive

log() { printf '[patch] %s\n' "$*"; }
warn() { printf '[patch][WARN] %s\n' "$*" >&2; }

log "Updating Debian security packages"
apt-get update
apt-get -y full-upgrade
apt-get install -y --no-install-recommends \
  apparmor apparmor-utils auditd ca-certificates curl file gnupg \
  libseccomp2 openssl unattended-upgrades needrestart

log "Enabling automatic security upgrades"
cat >/etc/apt/apt.conf.d/52-crostini-security <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Automatic-Reboot "false";
EOF

log "Applying conservative kernel/network hardening available inside the guest"
cat >/etc/sysctl.d/99-crostini-hardening.conf <<'EOF'
# Do not accept ICMP redirects / source routing.
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0

# Reduce accidental information leakage and ptrace surface.
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.yama.ptrace_scope = 1

# Harden symlink/hardlink/FIFO handling where supported.
fs.protected_symlinks = 1
fs.protected_hardlinks = 1
fs.protected_fifos = 2
fs.protected_regular = 2
EOF
sysctl --system || warn "Some sysctls are controlled by the Crostini kernel and could not be changed."

log "Checking AppArmor"
if [[ -r /sys/module/apparmor/parameters/enabled ]] && grep -qi '^Y' /sys/module/apparmor/parameters/enabled; then
  systemctl enable --now apparmor 2>/dev/null || true
  aa-status || true
else
  warn "AppArmor is not enabled by the guest kernel. This cannot be repaired purely from inside the Debian container."
fi

log "Checking for unexpected privileged listeners"
ss -lntup || true

log "Checking common high-risk local integration surfaces"
for p in /var/run/docker.sock /run/docker.sock /dev/kvm /dev/fuse /dev/dri; do
  if [[ -e "$p" ]]; then
    ls -ld "$p"
  fi
done

if [[ -S /var/run/docker.sock || -S /run/docker.sock ]]; then
  warn "Docker socket is present. Do not mount it into browser/Selenium workloads."
fi

log "Creating Selenium/Chromium sandbox launcher"
install -d -m 0755 /usr/local/libexec
cat >/usr/local/libexec/run-selenium-hardened <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

# Refuse common Chromium sandbox bypass flags.
for arg in "$@"; do
  case "$arg" in
    --no-sandbox|--disable-setuid-sandbox|--disable-seccomp-filter-sandbox|--disable-web-security|--remote-debugging-address=0.0.0.0*)
      echo "refusing unsafe Chromium flag: $arg" >&2
      exit 64
      ;;
  esac
done

# Restrict core dumps and inherited environment surprises.
ulimit -c 0
umask 077
unset LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH NODE_OPTIONS

exec "$@"
EOF
chmod 0755 /usr/local/libexec/run-selenium-hardened

log "Writing a boundary audit helper"
cat >/usr/local/sbin/crostini-boundary-audit <<'EOF'
#!/usr/bin/env bash
set -u
printf '=== kernel ===\n'
uname -a
printf '\n=== Debian ===\n'
cat /etc/os-release
printf '\n=== AppArmor ===\n'
if [[ -r /sys/module/apparmor/parameters/enabled ]]; then
  cat /sys/module/apparmor/parameters/enabled
else
  echo unavailable
fi
printf '\n=== seccomp status of this process ===\n'
grep -E '^(Seccomp|NoNewPrivs):' /proc/self/status || true
printf '\n=== mounts exported from ChromeOS ===\n'
findmnt -R /mnt/chromeos 2>/dev/null || true
printf '\n=== listeners ===\n'
ss -lntup 2>/dev/null || true
printf '\n=== sensitive device/socket exposure ===\n'
for p in /var/run/docker.sock /run/docker.sock /dev/kvm /dev/fuse /dev/dri; do
  [[ -e "$p" ]] && ls -ld "$p"
done
EOF
chmod 0755 /usr/local/sbin/crostini-boundary-audit

log "Upgrade complete. Running audit."
/usr/local/sbin/crostini-boundary-audit

cat <<'EOF'

Guest-side hardening complete.

IMPORTANT BOUNDARY:
  This script cannot patch ChromeOS host services such as cicerone, concierge,
  patchpanel, Termina, or crosvm. Those components are updated through ChromeOS.
  If ChromeOS itself is out of date, update/reboot ChromeOS before trusting the
  Crostini VM as a security boundary.

For Selenium/Chromium, run commands through:
  /usr/local/libexec/run-selenium-hardened <your command ...>
EOF
