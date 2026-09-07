#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
EXPECTED="9e98387b577bed5d55ca41bc334eef7c2bdb0368bf91a8890fd476e00152745e"
if [[ -e .dockerignore ]]; then
  ACTUAL="$(sha256sum .dockerignore | awk '{print $1}')"
  if [[ "$ACTUAL" == "$EXPECTED" ]]; then
    echo ".dockerignore already present and correct"
    exit 0
  fi
  echo "ERROR: .dockerignore exists but has unexpected sha256=$ACTUAL" >&2
  exit 1
fi
cat > .dockerignore <<'EOF_DOCKERIGNORE'
chrome-patch-src
refresh-context
security
README.md
install.sh
SHA256SUMS
*.zip
*.tar.gz
EOF_DOCKERIGNORE
chmod 600 .dockerignore
ACTUAL="$(sha256sum .dockerignore | awk '{print $1}')"
[[ "$ACTUAL" == "$EXPECTED" ]] || { echo "ERROR: reconstructed .dockerignore hash mismatch" >&2; exit 1; }
echo "Restored .dockerignore sha256=$ACTUAL"
echo "Now rerun: sudo ./install.sh"
