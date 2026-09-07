# DigitalOcean deployment

## Build node

Use an amd64 build droplet with a dedicated volume: at least 16 vCPUs, 32 GB RAM, and 200 GB free SSD. Enable automatic security updates and Docker BuildKit. Restrict SSH in the DigitalOcean Cloud Firewall to your administration IP.

Check capacity without changing the host:

```bash
sudo ./install.sh --preflight-only
```

The installer refuses an undersized node before installing packages or creating accounts. A 1-vCPU/2-GB droplet cannot build Chromium; swap or lower parallelism does not replace the required memory and disk.

```bash
git clone https://github.com/ornab74/chrome-patch.git
cd chrome-patch
sudo BUILD_JOBS=16 ./install.sh
```

Root invocation is expected. The script uses Docker's signed apt repository, creates a dedicated `chromebuild` account, configures rootless Docker, fetches the selected branch into `/srv/chrome-patch`, and runs all project code as that account. It does not add the account to `sudo` or the root-equivalent `docker` group.

The source build intentionally downloads Chromium and its declared dependencies. Do not run live scraping during the build. Logs are stored under `/var/log/chrome-patch/`; test results are stored under `/var/lib/chrome-patch/results/`. After a successful build, push the image to DigitalOcean Container Registry if the runtime node is separate.

Root-only management wrappers preserve the rootless Docker environment:

```bash
chrome-patch-docker ps
chrome-patch-compose run --rm browser-audit
chrome-patch-results
```

### Same-host runtime repair

If the Chromium image has already finished building but Selenium reports
`No usable sandbox`, do not rebuild and do not add `--no-sandbox`. Pull the
runtime-policy update and reuse the existing image:

```bash
install -d -m 0755 /home/scraperbuild
RUNTIME_FIX_DIR="$(mktemp -d /home/scraperbuild/chrome-patch-runtime-fix.XXXXXX)"
git clone --branch codex/hardened-assignment8-capstone --single-branch \
  https://github.com/ornab74/chrome-patch.git "$RUNTIME_FIX_DIR"
cd "$RUNTIME_FIX_DIR"
sudo REF=codex/hardened-assignment8-capstone \
  SMALL_BUILDER=1 BUILD_JOBS=5 ./install.sh --skip-build
```

The installer keeps Ubuntu's global unprivileged-userns restriction enabled,
loads the reviewed `chrome-patch-browser` AppArmor profile, attaches the
Moby-derived exact namespace seccomp allowlist, refreshes only the application
layer of the existing image, and runs a nested-user-namespace preflight before
Selenium. Existing BuildKit cache and the completed Chromium image are left in
the dedicated rootless Docker data directory.
The fresh checkout avoids overwriting the earlier hand-edited experiment.

### Clean-host restore without compiling Chromium

The completed image is pinned in `chromium/runtime-image.lock.json`. A new
amd64 runtime droplet can provision the same rootless-Docker and sandbox policy
boundary, pull the exact GHCR manifest, refresh the current repository's
application layer, and run the complete validation without compiling Chromium:

```bash
git clone --branch codex/hardened-assignment8-capstone --single-branch \
  https://github.com/ornab74/chrome-patch.git
cd chrome-patch
sudo REF=codex/hardened-assignment8-capstone ./install.sh --restore-image
```

Enter a classic PAT with only `read:packages` at the hidden prompt. Do not put
the PAT in the environment, command line, Git configuration, repository, or a
host file. The temporary Docker authentication directory is removed after the
pull.

After a new source build passes its full audit, back it up with a short-lived
classic PAT containing only `write:packages`:

```bash
chrome-patch-image verify
chrome-patch-image push
```

Keep the immutable digest printed by the push and revoke the upload PAT. See
[`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) for the coordinated lock-update
process.

## Runtime node

The bootstrap requires and verifies rootless Docker plus seccomp. Do not expose the Docker daemon, egress proxy, or any application port. The only optional published port is the dashboard's host-loopback binding. Reach it with an SSH tunnel:

```bash
ssh -L 8501:127.0.0.1:8501 your-user@your-droplet
```

Run `make audit` after every host kernel, Docker Engine, image, or Chromium change. If the probe cannot verify PID namespaces, network namespaces, and Seccomp-BPF, stop the deployment. Never add `--no-sandbox`, `SYS_ADMIN`, privileged mode, or an unconfined seccomp/AppArmor profile to make the probe pass.

For normal fixture work:

```bash
make assignment8
make capstone
make results
```

Use the live profile only after reviewing current robots.txt and terms. On the
managed checkout, run through the rootless wrapper:

```bash
chrome-patch-compose --profile live run --rm assignment8-books-live
chrome-patch-compose --profile live run --rm assignment8-owasp-live
chrome-patch-compose --profile live run --rm capstone-live
chrome-patch-results
```

Stop unused services with `chrome-patch-compose --profile live --profile dashboard down`.
