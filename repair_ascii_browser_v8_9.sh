#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

[[ ${EUID} -eq 0 ]] || {
  echo "Run as root: sudo ./repair_ascii_browser_v8_9.sh" >&2
  exit 1
}

ROOT="$(pwd -P)"
PATCH_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

for f in grok_vault_tui.py ascii_browser.py Dockerfile.vault; do
  [[ -f "$PATCH_DIR/$f" ]] || { echo "Missing $PATCH_DIR/$f" >&2; exit 1; }
done
[[ -f "$ROOT/SHA256SUMS" ]] || { echo "Missing $ROOT/SHA256SUMS" >&2; exit 1; }
[[ -x "$ROOT/install.sh" ]] || { echo "Missing executable $ROOT/install.sh" >&2; exit 1; }

copy_if_needed() {
  local src="$1" dst="$2"
  if [[ "$(readlink -f "$src")" == "$(readlink -f "$dst" 2>/dev/null || true)" ]]; then
    echo "$(basename "$dst"): already checkout copy"
  elif [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
    echo "$(basename "$dst"): already V8.9"
  else
    install -m 0644 "$src" "$dst"
    echo "$(basename "$dst"): patched"
  fi
}

copy_if_needed "$PATCH_DIR/grok_vault_tui.py" "$ROOT/grok_vault_tui.py"
copy_if_needed "$PATCH_DIR/ascii_browser.py" "$ROOT/ascii_browser.py"
copy_if_needed "$PATCH_DIR/Dockerfile.vault" "$ROOT/Dockerfile.vault"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import hashlib, re, sys

root = Path(sys.argv[1])
manifest = root / "SHA256SUMS"
targets = [
    "./grok_vault_tui.py",
    "./ascii_browser.py",
    "./Dockerfile.vault",
]

lines = manifest.read_text(encoding="utf-8").splitlines()
out = []
for line in lines:
    stripped = line.strip()
    matched = False
    for target in targets:
        if stripped.endswith(target):
            prefix = stripped[:-len(target)].strip().rstrip("*").strip()
            if re.fullmatch(r"[0-9A-Fa-f]{64}", prefix):
                matched = True
                break
    if not matched:
        out.append(line)

for target in targets:
    p = root / target[2:]
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    out.append(f"{h}  {target}")
    print(f"SHA256SUMS: {target} -> {h}")

manifest.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

find "$ROOT" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
find "$ROOT" -depth -type d -name '__pycache__' -empty -delete 2>/dev/null || true

python3 -m py_compile "$ROOT/grok_vault_tui.py" "$ROOT/ascii_browser.py"
rm -rf "$ROOT/__pycache__"
(cd "$ROOT" && sha256sum --check --strict SHA256SUMS >/dev/null)

echo
echo "V8.9 ASCII Chromium patch verified."
echo "Rebuilding hardened image and reinstalling wrappers..."
exec "$ROOT/install.sh"
