# Grok Secure Node — from-scratch DigitalOcean bundle

This bundle is self-contained for a fresh **Ubuntu/Debian amd64 DigitalOcean node**. It does not depend on any of the earlier repair/V3/V4/V5 bundles.

It uses the public, immutable Chromium runtime you specified:

`ghcr.io/ornab74/chrome-patch-chromium@sha256:f212beff4487370a6c026a1834f779657e76c7091c6ae42b4d2cee8cfe42d2f3`

The included `chrome-patch-src/` snapshot comes from the ZIP supplied in this conversation. The installer verifies the security-critical source files against that snapshot's `SHA256SUMS`, refreshes the application/security layer as required by `runtime-image.lock.json`, and then runs the current `chrome://sandbox` probe.

## What this installs

- dedicated locked host account: `grokbrowser`
- rootless Docker for that account
- reviewed RootlessKit + Chromium AppArmor handling
- exact Chromium seccomp policy from the supplied chrome-patch source
- immutable Chromium 151.0.7922.169 runtime pulled from GHCR by digest
- current application/security refresh layer
- headless Selenium browser running only on the DigitalOcean node
- allowlisting Squid egress proxy
- terminal Grok client with account registration/login/account switching
- AES-256-GCM vault for account metadata/passwords
- separately encrypted Chromium profile/session for each account
- plaintext live Chromium profile only on container tmpfs (`/session`)
- no Selenium, VNC, browser, or application ports published on the host

CAPTCHA/anti-bot challenges are **not bypassed**. If xAI requests an email OTP or verification link, the TUI asks you to enter/paste it into the SSH terminal and the DigitalOcean browser completes the navigation.

## Fresh installation

Copy this ZIP to the Droplet, then:

```bash
unzip grok_secure_node_from_scratch.zip
cd grok_secure_node_from_scratch
sudo ./install.sh
```

Recommended runtime size: at least 2 vCPUs, 4 GB RAM, amd64 Ubuntu/Debian. The script refuses lower RAM/CPU/disk before making changes.

The installer uses Docker's signed apt repository if the required rootless Docker/Compose stack is not already installed. It never adds `grokbrowser` to the host `docker` or `sudo` group.

Successful installation ends with:

```text
INSTALL PASSED

Start:          sudo grok-tui
Sandbox audit:  sudo grok-audit
Status:         sudo grok-status
Stop proxy:     sudo grok-stop
Vault backup:   sudo grok-vault-backup
```

## First run

```bash
sudo grok-tui
```

The first launch creates the encrypted vault and asks for a master passphrase through the attached TTY. The passphrase is not put in Docker environment variables, command-line arguments, `.env` files, or the vault itself.

Main menu:

```text
C  chat with active account
A  account manager / register / login
V  vault security information
Q  quit and lock vault
```

Account manager:

```text
N      register a new xAI/Grok email account
I      import an existing account
Enter  make selected account active
L      login/test selected account
R      reveal selected stored credentials after confirmation
D      remove account + encrypted browser profile
P      rotate vault master passphrase
Esc    back
```

### Register a new account

Choose `A`, then `N`. The browser on the Droplet uses the normal xAI email signup flow. The TUI can generate a strong random password if a password stage is presented. If email verification is required, enter the OTP or paste the HTTPS xAI/Grok verification URL at the terminal prompt.

The browser page itself is never rendered on your Chromebook. SSH is the only UI transport.

### Login / session persistence

Each account has an encrypted Chromium profile. Before a session starts:

```text
encrypted profile -> AES-GCM decrypt -> /session tmpfs -> Chromium
```

After Chromium exits:

```text
/session tmpfs -> tar -> AES-GCM encrypt -> persistent vault volume
```

The plaintext session directory is removed after the browser closes.

## Vault crypto

Vault/account object:

- AES-256-GCM
- fresh 96-bit nonce for every write
- scrypt master-key derivation
- N=131072, r=8, p=1
- 128-bit random salt
- 256-bit derived master key

Each browser profile is encrypted with a separate 256-bit subkey derived by HMAC-SHA-256 domain separation from the unlocked master key and the account ID.

The encrypted persistent Docker volume has the explicit name:

`grok-secure-vault-v1`

The live TUI also disables core dumps and marks itself non-dumpable as defense in depth. This does **not** make a live unlocked VM safe against host-root or kernel compromise.

## Backup

Close the TUI first, then:

```bash
sudo grok-vault-backup
```

This backs up only the encrypted vault volume to `/var/backups/grok-vault/` and prints a SHA-256 checksum. It refuses to run while a TUI container is active.

## Security checks

Run at any time:

```bash
sudo grok-audit
```

Expected result includes:

```json
{
  "evaluation": "You are adequately sandboxed.",
  "required_checks": {
    "Layer 1 Sandbox": "Namespace",
    "Network namespaces": "Yes",
    "PID namespaces": "Yes",
    "Seccomp-BPF sandbox": "Yes"
  },
  "sandbox": "verified"
}
```

`sudo grok-status` shows Compose state and rootless Docker security options.

## Network boundary

The Chromium container is on an internal Docker network and cannot route directly to the Internet. Only the Squid proxy has an outbound network. Its allowlist is intentionally narrow:

- `grok.com` and subdomains
- `x.ai` and subdomains
- `accounts.x.ai`
- `challenges.cloudflare.com`

Loopback, RFC1918/private, link-local, cloud metadata (`169.254.0.0/16`), documentation ranges, multicast, and IPv6 local ranges are explicitly denied.

Google/Apple/X OAuth is intentionally not configured. This build targets xAI email registration/login.

## Host firewall

No application port is published by this stack. Restrict the DigitalOcean Cloud Firewall to the SSH sources you actually use. Do not expose the rootless Docker socket.

## Updating

The Chromium runtime is deliberately digest-pinned. Do not replace the digest with `latest`. Updating Chromium/security policy should be treated as a reviewed change: update the immutable digest, source snapshot/policies, regenerate bundle checksums, and rerun `sudo grok-audit`.

## V6 Git-clone dotfile repair

If a Git clone contains `SHA256SUMS` but is missing the root `.dockerignore`, V6 `install.sh`
reconstructs only that canonical file and verifies its fixed SHA-256 before the full bundle
integrity check. For an older checkout, run `./fix_missing_dockerignore.sh` and then rerun
`sudo ./install.sh`. The installer still fails closed if an existing `.dockerignore` differs.

## V7: rootless vault initialization fix

V7 fixes an ownership-order bug in the one-shot `vault-init` helper. In V6 the
helper chowned `/vault` to UID 10001 and then chmodded `/vault` before
`/vault/profiles`. Because the helper had dropped `CAP_DAC_OVERRIDE`, changing
the parent to mode 0700 could make the child path unreachable to container UID
0 during the same command. V7 grants `DAC_OVERRIDE` only to the network-less,
short-lived init helper and chmods the child before the parent. The browser/TUI
containers receive no additional capability.

If a V6 install already failed with `chmod: cannot access '/vault/profiles':
Permission denied`, use the V7 bundle or run `sudo ./repair_v6_vault_init.sh`
from the V6 source directory after copying the helper there.


## V8 proxy startup fix

V8 removes the obsolete `cache_dir null` directive. Modern Squid needs no
`cache_dir` when `cache deny all` is used, and Debian's packaged Squid does not
build the legacy null store module by default. The proxy image now runs
`squid -k parse` during build, the installer repeats the parse under the
runtime service, and `grok-tui` prints Squid logs if the proxy cannot become
healthy. Use `sudo grok-proxy-logs` for the last 200 proxy log lines.

For an already-installed V7 node whose `egress-proxy` exits immediately, copy
`repair_proxy_v8.sh` into the checkout and run:

```bash
chmod +x repair_proxy_v8.sh
sudo ./repair_proxy_v8.sh
sudo grok-tui
```

`sudo grok-proxy-logs` prints the proxy's last 200 log lines.

## V8.1 manifest repair
V8.1 fixes the V8 `repair_proxy_v8.sh` manifest updater. V8 used an awk rewrite
that converted the canonical `HASH  ./path` checksum line into `HASH ./path` for
three updated files, causing `sha256sum --check --strict` to report three
improperly formatted lines. V8.1 restores those entries in canonical GNU
sha256sum format and preserves unrelated manifest lines verbatim.
