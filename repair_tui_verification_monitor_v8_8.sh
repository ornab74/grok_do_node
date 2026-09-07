#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

[[ ${EUID} -eq 0 ]] || {
  echo "Run as root: sudo ./repair_tui_verification_monitor_v8_8.sh" >&2
  exit 1
}

ROOT="$(pwd -P)"
PATCH_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC="$PATCH_DIR/grok_vault_tui.py"
DST="$ROOT/grok_vault_tui.py"

[[ -f "$SRC" ]] || { echo "Missing $SRC" >&2; exit 1; }
[[ -f "$DST" ]] || { echo "Missing $DST" >&2; exit 1; }
[[ -f "$ROOT/SHA256SUMS" ]] || { echo "Missing SHA256SUMS" >&2; exit 1; }
[[ -x "$ROOT/install.sh" ]] || { echo "Missing executable install.sh" >&2; exit 1; }

if [[ "$(readlink -f "$SRC")" == "$(readlink -f "$DST")" ]]; then
  echo "Patched TUI is already the checkout copy; skipping self-copy"
elif cmp -s "$SRC" "$DST"; then
  echo "TUI already matches V8.8"
else
  install -m 0644 "$SRC" "$DST"
fi

python3 - "$ROOT" <<'PY'
from pathlib import Path
import hashlib, re, sys
root = Path(sys.argv[1])
manifest = root / "SHA256SUMS"
target = "./grok_vault_tui.py"
lines = manifest.read_text().splitlines()
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
manifest.write_text("\n".join(out) + "\n")
print(f"SHA256SUMS: {target} -> {h}")
PY

find "$ROOT" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
find "$ROOT" -depth -type d -name '__pycache__' -empty -delete 2>/dev/null || true

python3 -m py_compile "$DST"
rm -rf "$ROOT/__pycache__"
(cd "$ROOT" && sha256sum --check --strict SHA256SUMS >/dev/null)

echo "V8.8 TUI verification monitor installed; rebuilding application."
exec "$ROOT/install.sh"
