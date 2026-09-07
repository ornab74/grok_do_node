# Chromium Linux broker finding and repair

## Finding

`BrokerFilePermission::ValidatePath` did not reject a trailing current-directory reference. A request such as `/tmp/.` could pass validation and still match a recursive `/tmp/` permission, returning a directory FD for the root capability.

- Upstream fix: `4298968d02fa7a24dccc65b03071af84c5418c38`
- Fix title: `[sandbox/linux] Fix directory traversal vulnerability`
- Upstream description: the validator now rejects `/../`, `/./`, trailing `/..`, and trailing `/.` references.

No public CVE assignment is claimed here. The earlier “introduced four months ago” hypothesis was not supported by the source history examined. The finding is an improper directory capability; arbitrary host-file access was not independently established.

## Repair

The source build fetches the exact fix commit. If the patch is already present, a reverse-apply check records `already-present`; otherwise a clean apply is mandatory and records `applied`. The build then requires the new validator marker, the trailing `/.` regression case, and a passing Chromium `BrokerFilePermission` unit-test subset.

The runtime checks the recorded source revision/fix state and all packaged file hashes before running the browser. This matters because an unpatched binary can report the same four-part version as the custom build.

## Defense in depth

The container also removes `file://` fixture navigation, blocks browser file-system permissions and downloads, exposes no host mount or Docker socket, runs without Linux capabilities, and requires the browser's own namespace and seccomp layers. Live traffic is forced through a deny-by-default proxy that blocks private/link-local destinations.
