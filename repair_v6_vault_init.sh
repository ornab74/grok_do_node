#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
[[ ${EUID} -eq 0 ]] || { echo 'run as root: sudo ./repair_v6_vault_init.sh' >&2; exit 1; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
[[ -f compose.yaml && -f SHA256SUMS && -x install.sh ]] || { echo 'run this from the grok node source directory' >&2; exit 1; }
python3 - <<'PY'
from pathlib import Path
p=Path('compose.yaml')
s=p.read_text()
old='''    cap_add: [CHOWN, FOWNER]\n    security_opt:\n      - no-new-privileges:true\n    volumes:\n      - grok-vault:/vault\n    command: ["/bin/sh", "-ec", "mkdir -p /vault/profiles; chown -R 10001:10001 /vault; chmod 0700 /vault /vault/profiles"]\n'''
new='''    # Rootless container root is mapped and intentionally has almost no\n    # capabilities. DAC_OVERRIDE is needed only by this network-less,\n    # short-lived init helper so it can repair/traverse a pre-existing vault\n    # whose top directory is already mode 0700 and owned by UID 10001.\n    cap_add: [CHOWN, FOWNER, DAC_OVERRIDE]\n    security_opt:\n      - no-new-privileges:true\n    volumes:\n      - grok-vault:/vault\n    # Never chmod the parent first: after chowning it to 10001, container UID 0\n    # would otherwise lose traversal without DAC_OVERRIDE. Touch directories\n    # only; existing encrypted vault files remain owned by the TUI user.\n    command: ["/bin/sh", "-ec", "mkdir -p /vault/profiles; chown 10001:10001 /vault /vault/profiles; chmod 0700 /vault/profiles; chmod 0700 /vault"]\n'''
if new in s:
    print('compose.yaml already has the V7 vault-init fix')
elif old in s:
    p.write_text(s.replace(old,new))
    print('patched compose.yaml')
else:
    raise SystemExit('refusing to patch: expected V6 vault-init stanza was not found')
PY
# SHA256SUMS is the bundle manifest, not a signature. This updater changes only
# the compose.yaml entry after applying the exact patch above.
newhash="$(sha256sum compose.yaml | awk '{print $1}')"
python3 - "$newhash" <<'PY'
from pathlib import Path
import sys
h=sys.argv[1]
p=Path('SHA256SUMS')
lines=p.read_text().splitlines()
out=[]
found=False
for line in lines:
    if line.endswith('  ./compose.yaml'):
        out.append(f'{h}  ./compose.yaml')
        found=True
    else:
        out.append(line)
if not found:
    raise SystemExit('manifest entry ./compose.yaml not found')
p.write_text('\n'.join(out)+'\n')
PY
sha256sum --check --strict SHA256SUMS >/dev/null
echo 'V7 vault-init patch verified. Re-running installer...'
exec ./install.sh
