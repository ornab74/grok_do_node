#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

[[ ${EUID} -eq 0 ]] || { echo "Run as root: sudo ./repair_proxy_acl_v8_3.sh" >&2; exit 1; }

SRC_DIR="$(pwd -P)"
APP_DIR="/srv/grok-secure-node"

OLD='acl approved_domains dstdomain grok.com .grok.com x.ai .x.ai accounts.x.ai challenges.cloudflare.com'
NEW='acl approved_domains dstdomain grok.com x.ai challenges.cloudflare.com'

patch_conf() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  python3 - "$file" "$OLD" "$NEW" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
old, new = sys.argv[2], sys.argv[3]
s = p.read_text()
if new in s:
    print(f"{p}: already patched")
elif old in s:
    p.write_text(s.replace(old, new))
    print(f"{p}: patched")
else:
    raise SystemExit(f"{p}: expected ACL line not found; refusing blind rewrite")
PY
}

echo "[V8.3] patching checkout Squid ACL"
patch_conf "$SRC_DIR/docker/squid-grok.conf"

echo "[V8.3] patching installed Squid ACL if present"
patch_conf "$APP_DIR/docker/squid-grok.conf"

if [[ -f "$SRC_DIR/SHA256SUMS" && -f "$SRC_DIR/docker/squid-grok.conf" ]]; then
  echo "[V8.3] updating checkout SHA256SUMS"
  python3 - "$SRC_DIR" <<'PY'
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
print(f"SHA256SUMS: replaced {target} with {h}")
PY

  (cd "$SRC_DIR" && sha256sum --check --strict SHA256SUMS >/dev/null) || {
    echo "V8.3: checkout manifest still has unrelated problems." >&2
    echo "Run: sha256sum --check --strict SHA256SUMS" >&2
    exit 1
  }
  echo "[V8.3] checkout manifest verifies"
fi

if [[ -x "$SRC_DIR/install.sh" ]]; then
  echo "[V8.3] rerunning installer"
  exec "$SRC_DIR/install.sh"
fi

echo "V8.3 patched the ACL. install.sh was not found in $SRC_DIR."
