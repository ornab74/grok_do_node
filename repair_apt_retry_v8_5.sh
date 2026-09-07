#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

[[ ${EUID} -eq 0 ]] || { echo "Run as root: sudo ./repair_apt_retry_v8_5.sh" >&2; exit 1; }

ROOT="$(pwd -P)"
INSTALL="$ROOT/install.sh"
MANIFEST="$ROOT/SHA256SUMS"

[[ -f "$INSTALL" ]] || { echo "Missing $INSTALL" >&2; exit 1; }
[[ -f "$MANIFEST" ]] || { echo "Missing $MANIFEST" >&2; exit 1; }

python3 - "$INSTALL" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text()

if "apt_output_is_lock_contention(){" in s:
    print("install.sh: APT retry support already present")
    raise SystemExit(0)

const_anchor = 'readonly MIN_DISK_MIB=10000\n'
const_add = '''readonly MIN_DISK_MIB=10000

# Fresh DigitalOcean/Ubuntu nodes may still have cloud-init, apt-daily, or
# unattended-upgrades holding APT/dpkg locks. Never delete lock files.
readonly APT_LOCK_MAX_WAIT_SECONDS="${APT_LOCK_MAX_WAIT_SECONDS:-900}"
readonly APT_LOCK_RETRY_DELAY_SECONDS="${APT_LOCK_RETRY_DELAY_SECONDS:-10}"
readonly APT_DPKG_LOCK_TIMEOUT_SECONDS="${APT_DPKG_LOCK_TIMEOUT_SECONDS:-60}"
'''
if const_anchor not in s:
    raise SystemExit("install.sh: constants anchor not found")
s = s.replace(const_anchor, const_add, 1)

helper = r'''apt_output_is_lock_contention(){
  local file="$1"
  grep -Eqi \
    'Could not get lock|Unable to acquire .*lock|held by process [0-9]+|is another process using it|/var/lib/dpkg/lock|/var/lib/dpkg/lock-frontend|/var/lib/apt/lists/lock|/var/cache/apt/archives/lock' \
    "$file"
}

show_apt_lock_owner(){
  local file="$1" pid found=0
  while read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    found=1
    printf 'APT/dpkg lock owner: '
    ps -p "$pid" -o pid=,etime=,comm=,args= 2>/dev/null || printf 'pid=%s (already exited)\n' "$pid"
  done < <(grep -Eo 'held by process [0-9]+' "$file" | awk '{print $4}' | sort -u)

  if (( found == 0 )); then
    ps -eo pid=,etime=,comm=,args= 2>/dev/null \
      | grep -E '[a]pt(-get)?|[d]pkg|[u]nattended-upgrade|[p]ackagekit' \
      | head -12 \
      | sed 's/^/Possible package-manager owner: /' || true
  fi
}

apt_retry(){
  local started now elapsed remaining sleep_for attempt=1 rc tmp
  started="$(date +%s)"

  while :; do
    tmp="$(mktemp /tmp/grok-apt.XXXXXX.log)"
    if "$@" 2>&1 | tee "$tmp"; then
      rm -f "$tmp"
      return 0
    else
      rc="${PIPESTATUS[0]}"
    fi

    if ! apt_output_is_lock_contention "$tmp"; then
      rm -f "$tmp"
      return "$rc"
    fi

    now="$(date +%s)"
    elapsed=$((now - started))
    remaining=$((APT_LOCK_MAX_WAIT_SECONDS - elapsed))

    if (( remaining <= 0 )); then
      warn "APT/dpkg stayed locked for ${elapsed}s; giving up without deleting any lock file"
      show_apt_lock_owner "$tmp"
      rm -f "$tmp"
      return "$rc"
    fi

    show_apt_lock_owner "$tmp"
    sleep_for="$APT_LOCK_RETRY_DELAY_SECONDS"
    (( sleep_for > remaining )) && sleep_for="$remaining"
    warn "APT/dpkg is busy (attempt ${attempt}); waiting ${sleep_for}s, then retrying. Max wait=${APT_LOCK_MAX_WAIT_SECONDS}s."
    rm -f "$tmp"
    sleep "$sleep_for"
    attempt=$((attempt + 1))
  done
}

'''
anchor = 'load_os_release(){\n'
if anchor not in s:
    raise SystemExit("install.sh: load_os_release anchor not found")
s = s.replace(anchor, helper + anchor, 1)

s = s.replace(
    '  apt-get update\n',
    '  apt_retry apt-get -o "DPkg::Lock::Timeout=${APT_DPKG_LOCK_TIMEOUT_SECONDS}" update\n'
)
s = s.replace(
    '  apt-get install -y --no-install-recommends \\\n',
    '  apt_retry apt-get -o "DPkg::Lock::Timeout=${APT_DPKG_LOCK_TIMEOUT_SECONDS}" install -y --no-install-recommends \\\n'
)

p.write_text(s)
print("install.sh: added bounded APT/dpkg wait+retry handling")
PY

chmod 0755 "$INSTALL"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import hashlib, re, sys

root = Path(sys.argv[1])
manifest = root / "SHA256SUMS"
target = "./install.sh"
lines = manifest.read_text(encoding="utf-8").splitlines()
out = []

for line in lines:
    stripped = line.strip()
    if stripped.endswith(target):
        prefix = stripped[:-len(target)].strip().rstrip("*").strip()
        if re.fullmatch(r"[0-9A-Fa-f]{64}", prefix):
            continue
    out.append(line)

h = hashlib.sha256((root / "install.sh").read_bytes()).hexdigest()
out.append(f"{h}  {target}")
manifest.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"SHA256SUMS: install.sh -> {h}")
PY

(cd "$ROOT" && sha256sum --check --strict SHA256SUMS >/dev/null)
bash -n "$INSTALL"

echo
echo "V8.5 APT RETRY PATCH PASSED"
echo "The installer now waits/retries instead of deleting package-manager locks."
echo
exec "$INSTALL"
