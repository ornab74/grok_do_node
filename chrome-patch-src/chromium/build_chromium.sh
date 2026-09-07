#!/bin/sh
set -eu

: "${CHROMIUM_VERSION:?CHROMIUM_VERSION is required}"
: "${SANDBOX_FIX_COMMIT:?SANDBOX_FIX_COMMIT is required}"
: "${DEPOT_TOOLS_REVISION:?DEPOT_TOOLS_REVISION is required}"
: "${BUILD_JOBS:=8}"

if [ "$(uname -m)" != "x86_64" ]; then
  echo "This locked build supports linux-amd64 only." >&2
  exit 1
fi

export PATH="/opt/depot_tools:${PATH}"
export DEPOT_TOOLS_UPDATE=0
export VPYTHON_BYPASS="manually managed python not supported by chrome operations"

if [ ! -d /opt/depot_tools/.git ]; then
  git init /opt/depot_tools
  git -C /opt/depot_tools remote add origin \
    https://chromium.googlesource.com/chromium/tools/depot_tools.git
else
  git -C /opt/depot_tools remote set-url origin \
    https://chromium.googlesource.com/chromium/tools/depot_tools.git
fi
git -C /opt/depot_tools fetch --depth 1 origin "${DEPOT_TOOLS_REVISION}"
git -C /opt/depot_tools checkout --detach FETCH_HEAD
depot_tools_revision="$(git -C /opt/depot_tools rev-parse HEAD)"
[ "${depot_tools_revision}" = "${DEPOT_TOOLS_REVISION}" ] || {
  echo "depot_tools revision mismatch" >&2
  exit 1
}

# The pinned checkout intentionally disables auto-update, so bootstrap the
# wrapper's local Python/tool metadata explicitly before any GN invocation.
ensure_bootstrap
test -f /opt/depot_tools/python3_bin_reldir.txt || {
  echo "depot_tools bootstrap did not create python3_bin_reldir.txt" >&2
  exit 1
}

mkdir -p /src/chromium
cd /src/chromium
if [ ! -d src/.git ]; then
  gclient config --name=src --unmanaged https://chromium.googlesource.com/chromium/src.git
  git clone --depth 1 --branch "${CHROMIUM_VERSION}" \
    https://chromium.googlesource.com/chromium/src.git src
else
  git -C src remote set-url origin https://chromium.googlesource.com/chromium/src.git
  git -C src fetch --depth 1 origin "${CHROMIUM_VERSION}"
  git -C src checkout --detach --force FETCH_HEAD
  git -C src clean -ffd
fi

cd /src/chromium/src
./build/install-build-deps.sh --no-prompt --no-chromeos-fonts
cd /src/chromium
gclient sync --no-history --nohooks -D
gclient runhooks

# BuildKit cache mounts can preserve downloaded tool files without their
# executable mode. Restore modes for Chromium's cached Node toolchain.
if [ -d /src/chromium/src/third_party/node ]; then
  find /src/chromium/src/third_party/node -type f -path '*/bin/*' -exec chmod 0755 {} +
fi

cd /src/chromium/src
source_revision="$(git rev-parse HEAD)"
git fetch --depth 2 origin "${SANDBOX_FIX_COMMIT}"
git show --format=email --full-index --binary "${SANDBOX_FIX_COMMIT}" > /tmp/sandbox-fix.patch
test -s /tmp/sandbox-fix.patch || {
  echo "sandbox fix commit produced an empty patch" >&2
  exit 1
}
if git apply --reverse --check /tmp/sandbox-fix.patch >/dev/null 2>&1; then
  fix_state="already-present"
else
  if git apply --check /tmp/sandbox-fix.patch >/dev/null 2>&1; then
    git apply /tmp/sandbox-fix.patch
  else
    git apply --check --3way /tmp/sandbox-fix.patch
    git apply --3way /tmp/sandbox-fix.patch
  fi
  fix_state="applied"
fi

python3 /build-tools/verify_source_fix.py /src/chromium/src
gn gen out/Secure --args='is_debug=false is_component_build=false is_official_build=true symbol_level=0 blink_symbol_level=0 v8_symbol_level=0 use_sysroot=true chrome_pgo_phase=0'
if ! autoninja -C out/Secure -j "${BUILD_JOBS}" chrome chromedriver sandbox_linux_unittests; then
  echo "Chromium build failed; printing failed Siso commands and recent output." >&2
  if [ -f out/Secure/siso_failed_commands.sh ]; then
    sed -n '1,120p' out/Secure/siso_failed_commands.sh >&2
  fi
  if [ -f out/Secure/siso_output ]; then
    tail -n 200 out/Secure/siso_output >&2
  fi
  exit 1
fi
out/Secure/sandbox_linux_unittests --gtest_list_tests | grep -q 'BrokerFilePermission'
out/Secure/sandbox_linux_unittests --gtest_filter='*BrokerFilePermission*'

python3 /build-tools/package_runtime.py \
  --output /src/chromium/src/out/Secure \
  --destination /chromium-runtime \
  --version "${CHROMIUM_VERSION}" \
  --source-revision "${source_revision}" \
  --fix-commit "${SANDBOX_FIX_COMMIT}" \
  --fix-state "${fix_state}" \
  --depot-tools-revision "${depot_tools_revision}"
