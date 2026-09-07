#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

[[ ${EUID} -eq 0 ]] || { echo "Run as root: sudo ./repair_pycache_manifest_v8_4.sh" >&2; exit 1; }

ROOT="$(pwd -P)"
MANIFEST="$ROOT/SHA256SUMS"
[[ -f "$MANIFEST" ]] || { echo "Missing $MANIFEST" >&2; exit 1; }
[[ -x "$ROOT/install.sh" ]] || { echo "Missing executable install.sh in $ROOT" >&2; exit 1; }

cp -a "$MANIFEST" "$MANIFEST.pre-v8.4.bak"

python3 - "$MANIFEST" <<'PY'
from pathlib import Path
import re, sys

p = Path(sys.argv[1])
lines = p.read_text(encoding="utf-8").splitlines()
out = []
removed = []

for line in lines:
    # Canonical sha256sum record: 64 hex + mode separator + path.
    m = re.match(r'^([0-9A-Fa-f]{64}) [ *](.+)$', line)
    if not m:
        out.append(line)
        continue

    path = m.group(2)
    if "/__pycache__/" in f"/{path}" or path.endswith(".pyc") or path.endswith(".pyo"):
        removed.append(path)
        continue
    out.append(line)

p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("Removed transient manifest entries:")
for x in removed:
    print(f"  {x}")
if not removed:
    print("  (none; manifest was already clean)")
PY

find "$ROOT" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
find "$ROOT" -depth -type d -name '__pycache__' -empty -delete 2>/dev/null || true

echo "[V8.4] verifying all remaining immutable files"
(cd "$ROOT" && sha256sum --check --strict SHA256SUMS)

echo "[V8.4] manifest repaired; rerunning installer"
exec "$ROOT/install.sh"
