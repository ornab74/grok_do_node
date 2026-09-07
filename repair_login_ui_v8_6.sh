#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

[[ ${EUID} -eq 0 ]] || { echo "Run as root: sudo ./repair_login_ui_v8_6.sh" >&2; exit 1; }

ROOT="$(pwd -P)"
PATCH_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$PATCH_DIR/grok_vault_tui.py" ]] || { echo "Missing patched grok_vault_tui.py beside this script" >&2; exit 1; }
[[ -f "$ROOT/SHA256SUMS" ]] || { echo "Missing $ROOT/SHA256SUMS" >&2; exit 1; }
[[ -x "$ROOT/install.sh" ]] || { echo "Missing executable $ROOT/install.sh" >&2; exit 1; }

install -m 0644 "$PATCH_DIR/grok_vault_tui.py" "$ROOT/grok_vault_tui.py"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import hashlib, re, sys

root = Path(sys.argv[1])
manifest = root / "SHA256SUMS"
target = "./grok_vault_tui.py"
lines = manifest.read_text(encoding="utf-8").splitlines()
out = []

for line in lines:
    stripped = line.strip()
    if stripped.endswith(target):
        prefix = stripped[:-len(target)].strip().rstrip("*").strip()
        if re.fullmatch(r"[0-9A-Fa-f]{64}", prefix):
            continue
    out.append(line)

h = hashlib.sha256((root / "grok_vault_tui.py").read_bytes()).hexdigest()
out.append(f"{h}  {target}")
manifest.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"SHA256SUMS: grok_vault_tui.py -> {h}")
PY

find "$ROOT" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
find "$ROOT" -depth -type d -name '__pycache__' -empty -delete 2>/dev/null || true

(cd "$ROOT" && sha256sum --check --strict SHA256SUMS >/dev/null)
python3 -m py_compile "$ROOT/grok_vault_tui.py"
rm -rf "$ROOT/__pycache__"
echo "V8.6 login UI patch verified. Re-running installer..."
exec "$ROOT/install.sh"
