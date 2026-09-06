# Grok TUI on the chrome-patch Chromium image

This bundle adapts the supplied `scraper-chrome-docker-main` security boundary for a terminal Grok client on an amd64 DigitalOcean node.

## Pinned browser image

```text
ghcr.io/ornab74/chrome-patch-chromium@sha256:f212beff4487370a6c026a1834f779657e76c7091c6ae42b4d2cee8cfe42d2f3
```

The image is pulled by immutable digest. No GitHub token is used for the public package.

## Deploy

Copy this directory to an Ubuntu/Debian amd64 DigitalOcean droplet with at least 2 vCPU, ~4 GiB RAM, and 8 GiB free disk, then:

```bash
sudo ./deploy_digitalocean.sh
```

The deployer:

- installs Docker Engine + rootless extras from Docker's signed apt repository when needed;
- creates an unprivileged `grokbrowser` service account, outside `sudo` and `docker` groups;
- enables rootless Docker and verifies rootless + seccomp are active;
- loads the exact `chrome-patch-browser` AppArmor profile and Chromium seccomp JSON copied from the supplied ZIP;
- pulls the public GHCR image by the pinned digest;
- verifies `/opt/chromium/manifest.sha256` plus browser/driver versions;
- builds the same style of unprivileged Squid egress proxy with a Grok-specific allowlist;
- runs the image's own sandbox audit before declaring success.

## Run

SSH to the node and run:

```bash
grok-tui
```

Keys/commands:

- `Enter` sends the current prompt.
- Left/right, Home/End, Backspace/Delete edit the one-line prompt.
- `/new` opens a fresh Grok page.
- `/reload` reloads Grok.
- `/clear` clears only the local terminal transcript.
- `/quit`, Ctrl-C, or Ctrl-D exits.

The assistant bubble is polled while it grows, so the TUI redraws as Grok streams text.

## Isolation model

The browser container has no direct Internet network. Its only network is `grok-internal`, which is marked `internal: true`. The Squid container alone is attached both to that internal network and to an outbound network. Squid allows HTTPS only to the explicit Grok/xAI/X/Cloudflare domain set and rejects private, loopback, link-local, and cloud-metadata destination ranges.

The browser retains the ZIP's controls: UID/GID `10001`, read-only root filesystem, all capabilities dropped, no-new-privileges, reviewed AppArmor, reviewed seccomp, PID/memory/CPU limits, no Docker socket, no host mounts, and no `--no-sandbox`/remote-debugging escape switches.

## Authentication / verification

This is deliberately not a login or anti-bot bypass. The browser home is ephemeral. If Grok requires a CAPTCHA or an authenticated account before exposing the composer, the TUI reports that condition and stops rather than injecting cookies, passwords, or bypass logic.

## Grok selectors

The adapter currently tries the stable `data-testid` / ProseMirror selectors first and older TipTap/markdown selectors as fallbacks. Grok can change its DOM at any time; if the website changes, update only `grok_tui.py`, not the browser sandbox flags.
