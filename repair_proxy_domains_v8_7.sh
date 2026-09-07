#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

[[ ${EUID} -eq 0 ]] || { echo "Run as root: sudo ./repair_proxy_domains_v8_7.sh" >&2; exit 1; }

ROOT="$(pwd -P)"
APP_DIR="/srv/grok-secure-node"
SERVICE_USER="grokbrowser"

NEW='acl approved_domains dstdomain .grok.com .x.ai challenges.cloudflare.com'

patch_conf() {
  local file="$1"
  [[ -f "$file" ]] || return 0

  python3 - "$file" "$NEW" <<'PY'
from pathlib import Path
import re, sys

p = Path(sys.argv[1])
new = sys.argv[2]
s = p.read_text()

pattern = re.compile(r'^acl approved_domains dstdomain .+$', re.MULTILINE)
m = pattern.search(s)
if not m:
    raise SystemExit(f"{p}: approved_domains ACL not found; refusing blind edit")

old = m.group(0)
if old == new:
    print(f"{p}: already correct")
else:
    s = s[:m.start()] + new + s[m.end():]
    p.write_text(s)
    print(f"{p}:")
    print(f"  old: {old}")
    print(f"  new: {new}")
PY
}

echo "[V8.7] Correcting Squid whole-domain matching"
patch_conf "$ROOT/docker/squid-grok.conf"
patch_conf "$APP_DIR/docker/squid-grok.conf"

# Update checkout checksum record if present.
if [[ -f "$ROOT/SHA256SUMS" && -f "$ROOT/docker/squid-grok.conf" ]]; then
  python3 - "$ROOT" <<'PY'
from pathlib import Path
import hashlib, re, sys

root = Path(sys.argv[1])
manifest = root / "SHA256SUMS"
target = "./docker/squid-grok.conf"
lines = manifest.read_text(encoding="utf-8").splitlines()
out = []

for line in lines:
    stripped = line.strip()
    if stripped.endswith(target):
        prefix = stripped[:-len(target)].strip().rstrip("*").strip()
        if re.fullmatch(r"[0-9A-Fa-f]{64}", prefix):
            continue
    out.append(line)

h = hashlib.sha256((root / "docker/squid-grok.conf").read_bytes()).hexdigest()
out.append(f"{h}  {target}")
manifest.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"SHA256SUMS: {target} -> {h}")
PY

  find "$ROOT" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
  find "$ROOT" -depth -type d -name '__pycache__' -empty -delete 2>/dev/null || true
  (cd "$ROOT" && sha256sum --check --strict SHA256SUMS >/dev/null)
fi

# Install a compact blocked-host diagnostic command.
cat >/usr/local/bin/grok-denied <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ ${EUID} -eq 0 ]] || { echo 'run as root: sudo grok-denied' >&2; exit 1; }

SERVICE_USER=grokbrowser
UID_N="$(id -u "$SERVICE_USER")"
RUNTIME_DIR="/run/user/$UID_N"

runuser -u "$SERVICE_USER" -- env \
  HOME=/home/grokbrowser USER="$SERVICE_USER" LOGNAME="$SERVICE_USER" \
  XDG_RUNTIME_DIR="$RUNTIME_DIR" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNTIME_DIR/bus" \
  DOCKER_HOST="unix://$RUNTIME_DIR/docker.sock" \
  PATH="/usr/local/bin:/usr/bin:/bin:/home/grokbrowser/bin" \
  bash -lc '
    cd /srv/grok-secure-node
    docker compose -f compose.yaml logs --no-color --tail=2000 egress-proxy 2>/dev/null \
      | grep "TCP_DENIED/" \
      | sed -nE "s#.* (CONNECT|GET|POST) (https?://)?([^ /:]+)(:[0-9]+)?/?.*#\3#p" \
      | sort \
      | uniq -c \
      | sort -nr
  '
EOF
chmod 0755 /usr/local/bin/grok-denied

# If installer exists, use normal install path to rebuild/parse proxy and
# reinstall all wrappers. APT lock retry support in V8.5+ makes this safe.
if [[ -x "$ROOT/install.sh" ]]; then
  echo "[V8.7] Re-running installer to rebuild and validate proxy"
  exec "$ROOT/install.sh"
fi

echo "V8.7 ACL patched. No install.sh found in $ROOT."
