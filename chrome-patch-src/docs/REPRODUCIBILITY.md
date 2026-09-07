# Reproducibility and disaster recovery

The repository contains two independent recovery paths. Neither path stores a
GitHub token, browser profile, cookie, password, host file, or Docker socket.

| Path | Input | Result |
| --- | --- | --- |
| Source rebuild | `chromium/source.lock.json`, Dockerfile, patch verifier, and build scripts | Rebuilds Chromium and ChromeDriver from their pinned source revisions |
| Runtime restore | `chromium/runtime-image.lock.json` plus the current repository | Pulls the compiled browser base by immutable GHCR digest, refreshes only `/app`, and runs the complete audit |

The runtime restore is for disaster recovery and quick Selenium reuse. The
source rebuild remains the authoritative way to create a changed browser.
The pinned artifact preserves the expensive compiled Chromium tree. The
restore workflow deliberately replaces its repository application layer so
later security-gate and coursework fixes are not frozen at the build date.

## What is versioned

- Exact Chromium version, source revision, upstream sandbox-fix revision,
  Selenium version, and `depot_tools` revision.
- Dockerfile, Compose topology, rootless-Docker bootstrap, checkpointed
  BuildKit cache mounts, and runtime package manifest generator.
- Confined AppArmor profile and Moby-default-derived seccomp policy.
- Enterprise Chromium policy, egress allowlist, security gate, and sandbox
  status probe.
- Assignment 8, the capstone, offline fixtures, result viewers, and tests.
- GHCR backup/restore script and the immutable digest of the validated image.
- Repository-wide `SHA256SUMS` plus the independent
  `/opt/chromium/manifest.sha256` inside the image.

The repository does not contain BuildKit's mutable cache database. Losing that
cache makes a future source rebuild slower, but it does not prevent either a
clean source build or restoration of the pinned runtime image.

## Restore the validated image on a clean host

Create a classic GitHub PAT with only `read:packages`. Do not put it in an
environment variable, command argument, file, GitHub issue, or shell history.
The installer invokes the hidden prompt and removes its temporary Docker auth
directory when the pull finishes.

```bash
git clone --branch codex/hardened-assignment8-capstone --single-branch \
  https://github.com/ornab74/chrome-patch.git
cd chrome-patch
sudo REF=codex/hardened-assignment8-capstone ./install.sh --restore-image
```

The restore command:

1. Verifies the repository SHA-256 manifest.
2. Provisions the dedicated `chromebuild` rootless-Docker account.
3. Pulls the exact `linux/amd64` manifest from
   `chromium/runtime-image.lock.json`, never a mutable tag.
4. Verifies every packaged Chromium runtime file against the image's internal
   manifest and checks browser/driver versions.
5. Installs the reviewed AppArmor and seccomp profiles without weakening the
   host-wide user-namespace restriction.
6. Runs the nested-user-namespace and `chrome://sandbox` audits.
7. Runs both fixture projects and their output validators.

The currently pinned artifact is:

```text
ghcr.io/ornab74/chrome-patch-chromium@sha256:f212beff4487370a6c026a1834f779657e76c7091c6ae42b4d2cee8cfe42d2f3
```

After a successful managed installation, these root-only wrappers operate the
dedicated daemon:

```bash
chrome-patch-image verify
chrome-patch-compose run --rm browser-audit
chrome-patch-results
```

## Back up a completed source build

Create a short-lived classic PAT with only `write:packages`. On a managed
installation run:

```bash
chrome-patch-image verify
chrome-patch-image push
```

On a developer machine already using rootless Docker, run:

```bash
make image-verify
make image-backup
```

A standalone restore must refresh the application layer before it is audited:

```bash
make image-restore
make runtime-refresh
make audit
```

The script disables shell tracing, reads the PAT without echo, passes it to
`docker login` over standard input, uses a mode-0700 auth directory under the
user runtime directory, pushes the image, pulls the returned immutable digest
as a verification step, records a mode-0600 local lock, logs out, and deletes
the temporary auth material. Revoke the upload PAT after the backup.

If the pushed digest differs from the checked-in digest, the script emits a
warning. Do not edit `runtime-image.lock.json` merely to silence it. First run
the full browser audit and coursework tests, review the coordinated source
pins, and update the lock in a reviewed commit.

## Build from source

For the normal build profile, use a Linux amd64 machine with at least 16 vCPUs,
32 GB RAM, and 200 GB free SSD:

```bash
git clone --branch codex/hardened-assignment8-capstone --single-branch \
  https://github.com/ornab74/chrome-patch.git
cd chrome-patch
sudo REF=codex/hardened-assignment8-capstone BUILD_JOBS=16 ./install.sh
```

The explicit small-builder profile supports the previously tested 8-vCPU,
16-GB droplet, but a clean Chromium build may take many hours:

```bash
sudo REF=codex/hardened-assignment8-capstone \
  SMALL_BUILDER=1 BUILD_JOBS=5 ./install.sh
```

BuildKit cache mounts retain `depot_tools`, the Chromium source checkout,
downloads, and `out/Secure` objects inside the dedicated rootless Docker data
directory. Repeating the same build on the same host reuses those objects.

## Change Chromium or Selenium safely

1. Create a new branch; never overwrite the current runtime lock in place.
2. Update all coordinated version/source pins in
   `chromium/source.lock.json`, `Dockerfile`, `compose.yaml`,
   `requirements.txt`, and `common/security_gate.py`.
3. Review whether the broker fix is still present and whether its regression
   test names or source locations changed.
4. Run `./install.sh --local-only`, then perform the source build and complete
   runtime audit.
5. Push the validated image using `scripts/ghcr-image.sh push`.
6. Update `chromium/runtime-image.lock.json` to the returned digest, update the
   constants in the script, regenerate `SHA256SUMS`, and rerun all tests.
7. Keep the previous digest available until the new image passes on the target
   host.

## Credential boundary

Only GHCR requires a PAT. Source cloning from this public repository does not.
The package is private unless its GitHub Packages visibility is deliberately
changed. A private restore needs `read:packages`; uploading needs
`write:packages`. Repository, workflow, delete-package, organization, user,
and administration scopes are not required.
