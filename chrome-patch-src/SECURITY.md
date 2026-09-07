# Security boundary

## Enforced invariants

- Chromium and ChromeDriver are built together from the exact source tag in `chromium/source.lock.json`.
- Chromium `depot_tools` is checked out at the exact approved revision and cannot auto-update.
- The build fetches the exact upstream broker fix and applies it if the tag does not already contain it.
- Source inspection and Chromium's `BrokerFilePermission` unit tests are build-stopping gates.
- Runtime metadata and SHA-256 hashes are checked before either executable is invoked.
- Browser and driver must both report the exact approved version.
- Selenium Manager and `webdriver-manager` are absent; driver download overrides are rejected.
- Chromium sandbox bypasses, insecure TLS switches, WebDriver debug exposure, and arbitrary proxy overrides are rejected.
- Chromium must report PID, network-namespace, and Seccomp-BPF sandboxes as enabled before every browser-executing job. Exact read-only result-viewer, query, and dashboard commands still pass the static image-integrity and container-boundary gate but do not launch an unnecessary browser.
- Host provisioning runs as root, but it creates a dedicated non-login account and requires that all Docker build/runtime operations use that account's rootless Unix socket with seccomp enabled.
- The deployment source is fetched to `/srv` at an exact remote commit, verified against `SHA256SUMS`, made root-owned, and recorded with a source-archive SHA-256 before the build account runs it.
- Builder base-image tags are pulled and resolved to immutable registry digests before Compose invokes the Dockerfile.
- The completed Chromium runtime base is separately pinned to an immutable GHCR SHA-256 manifest digest. Restore never trusts the mutable image tag, verifies the pulled image's internal runtime manifest, refreshes only the current repository application layer, and then runs the full sandbox and coursework audit before accepting it.
- App containers run as UID 10001 with a read-only root filesystem, zero capabilities, `no-new-privileges`, process/resource limits, and no host paths or devices.
- Browser jobs attach the confined `chrome-patch-browser` AppArmor profile and a Moby-default-derived seccomp profile. AppArmor grants `userns` only to that label. Seccomp allows only `CLONE_NEWUSER`, Chromium's exact user/PID/network namespace combinations, and `chroot` subject to the kernel's capability check. `clone3`, `mount`, and `setns` remain unavailable without `CAP_SYS_ADMIN`.
- The installer keeps `kernel.apparmor_restrict_unprivileged_userns=1`, uses the distribution RootlessKit profile's supported local include, validates both runtime policies before loading them, and proves nested user-namespace creation before Selenium starts.
- Offline jobs have no network interface. Live jobs can reach only an internal egress proxy; the proxy denies private and metadata address ranges before its HTTPS domain allowlist.
- Local fixtures use loopback HTTP. Enterprise policy blocks `file://`, file-system access prompts, downloads, extensions, DevTools, media capture, printing, and sync.

## Validated finding boundary

The relevant Chromium Linux broker code accepted a path ending in `/.` because it rejected parent references but not self references. For a recursive allowlisted root, that could return a directory capability at the root rather than the intended descendant. The upstream fix changes validation to reject both parent and self references, including trailing `/.` and `/..`.

This establishes an improperly returned directory FD/capability. It does **not**, by itself, establish arbitrary host-file access: downstream syscall policy, the capability root, container mount namespace, and lack of host mounts still constrain what can be reached. This repo fixes the broker path and also removes host paths from the runtime boundary.

## Operational requirements

Keep the host kernel and Docker Engine patched, leave the DigitalOcean Cloud Firewall closed except for administrative SSH, and never publish the egress proxy or Docker daemon. Treat a failed host-capacity check, rootless-Docker check, source-integrity check, or Chromium sandbox probe as a deployment failure.

Do not add `privileged`, `cap_add`, host networking, Docker socket mounts, host directory mounts, `seccomp=unconfined`, `apparmor=unconfined`, or any switch rejected by `common/security_gate.py`. Do not disable `kernel.apparmor_restrict_unprivileged_userns` globally; update the reviewed application profile instead.

GHCR credentials are operational secrets, not repository material. Use a
short-lived classic PAT with only `write:packages` for backup or only
`read:packages` for private restore. The image helper must keep shell tracing
disabled, accept the PAT only through its hidden interactive prompt, use an
ephemeral mode-0700 Docker configuration directory, and remove it on exit.
Never commit a PAT or grant repository, workflow, delete-package,
organization, user, or administration scopes for this workflow.
