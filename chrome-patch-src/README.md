# Chrome Patch: hardened Selenium scraping lab

This repository contains both coursework projects in one fail-closed Docker environment:

- `assignment8/`: the book-search and OWASP Top 10 scrapers.
- `capstone/`: the weather scrape, cleaning pipeline, SQLite database, query tool, and dashboard.

The image builds Chromium `151.0.7922.169` and the matching ChromeDriver from the same Chromium source tree. It applies or verifies upstream sandbox fix `4298968d02fa7a24dccc65b03071af84c5418c38`, pins `depot_tools` to revision `547d7e12fe104305c7de797d5a2b4155914ad362`, runs the related Chromium `BrokerFilePermission` unit tests, and packages only the resulting browser/runtime files. Selenium is pinned to `4.47.0`; Selenium Manager and `webdriver-manager` are not used.

## Minimum host requirements

The validated clean-host **runtime restore requires at least 2 vCPUs, 4 GB of
physical RAM, and 10 GB of free disk** on a Linux amd64 Ubuntu or Debian host.
The installer expresses the 4-GB requirement as at least `3500 MiB` reported by
`/proc/meminfo`, allowing for the difference between advertised decimal GB,
binary MiB, and memory reserved by the host.

| Operation | CPU | Physical RAM | Free disk | Purpose |
| --- | ---: | ---: | ---: | --- |
| Restore the pinned GHCR runtime | **2 vCPUs** | **4 GB minimum** | **10 GB** | Recommended for running and testing the projects |
| Small Chromium source build | 4 vCPUs | 8 GB | 180 GB | `SMALL_BUILDER=1`; use `BUILD_JOBS=1` through `5` |
| Default Chromium source build | 16 vCPUs | 30 GiB | 180 GB | Recommended dedicated build machine |

A 1-vCPU/2-GB node is intentionally refused before Docker or GHCR work begins.
Swap does not satisfy the physical-memory check, `BUILD_JOBS` does not change
the runtime requirement, and `SMALL_BUILDER=1` applies only to source
compilation. Do not remove or deceive the preflight gate. Resize the host to at
least 2 vCPUs/4 GB, then follow the
[private-repository clean-host restore](#private-repository-clean-host-restore-recommended).

## Validated outcome

On 2026-08-22 UTC, runtime commit
[`d955c3ca`](https://github.com/ornab74/chrome-patch/commit/d955c3ca2e1fbd2756c629d024690704cb77cc8f)
completed the full workflow on an 8-vCPU, 16-GB AMD DigitalOcean node:

- The immutable GHCR Chromium base was restored and its internal SHA-256
  manifest verified.
- The current application layer was refreshed without recompiling Chromium.
- Chromium and ChromeDriver both reported `151.0.7922.169`.
- A nested user namespace was created inside rootless Docker.
- `chrome://sandbox` reported the namespace layer, PID namespaces, network
  namespaces, and Seccomp-BPF sandbox as enabled.
- The complete container test stage, Assignment 8, output validators, capstone
  pipeline, SQLite checks, result viewers, and query command passed.
- The final capstone query reported nine cities with a 13.0 °C minimum,
  24.0 °C average, and 38.0 °C maximum.
- The installer ended with `FULL VALIDATION PASSED`.

The validated source archive SHA-256 is:

```text
8dc1fc819b469c02268a576d17728e8dbab3ccae84260c1f09da4dddcbb9cfd5
```

The compiled recovery base is pinned independently as:

```text
ghcr.io/ornab74/chrome-patch-chromium@sha256:f212beff4487370a6c026a1834f779657e76c7091c6ae42b4d2cee8cfe42d2f3
```

The documentation commits after `d955c3ca` record this result; they do not
rewrite history or claim that a documentation-only revision received the same
runtime validation.

The operator-captured proof, complete terminal transcript, result tables,
machine-readable validation summary, and evidence hashes are preserved in the
[`20260822T020520Z` validation bundle](docs/evidence/runs/20260822T020520Z/RESULTS.md).
The [evidence index](docs/evidence/README.md) explains what each artifact proves,
what it does not prove, and how to reproduce the run independently.

## Engineering postmortem: how this was completed

This project did not begin as a clean Chromium build recipe. It began as two
Python scraping assignments, several source references, and a requirement to
run Selenium safely on a remote node. Reaching the validated result required
solving four separate systems problems: reproducible Chromium compilation,
rootless container deployment, Ubuntu user-namespace policy, and safe
coursework execution.

### 1. The original goal and non-negotiable constraints

The initial deliverable needed to contain Assignment 8 and the capstone in one
Docker environment, produce inspectable results, and later split cleanly into
separate coursework repositories. Once browser-sandbox concerns became part of
the work, the security requirements became explicit:

- No Debian Chromium or opportunistically downloaded ChromeDriver.
- Chromium and ChromeDriver must come from the same pinned source revision.
- No Selenium Manager or `webdriver-manager` network download.
- No `--no-sandbox`, privileged container, `SYS_ADMIN`, unconfined AppArmor or
  seccomp, Docker socket, host networking, or host directory mount.
- No global disabling of Ubuntu's restricted unprivileged-userns policy.
- No live network access during ordinary tests; live scraping must be opt-in
  and pass through a destination allowlist.
- Repository files, packaged browser files, versions, and source-fix state must
  fail closed when they differ from the approved values.

Those constraints explain why the final design is more than a Dockerfile. It
is a source lock, host bootstrap, compiler pipeline, runtime policy, security
gate, test suite, recovery mechanism, and coursework runner working together.

### 2. Calibrating the browser finding honestly

The investigation focused on Chromium's Linux syscall-broker path validation.
The relevant code rejected parent references but accepted a trailing current
directory reference such as `/.`. Upstream commit
`4298968d02fa7a24dccc65b03071af84c5418c38` changes the validation to reject
both parent and self references and includes regression coverage.

The repository therefore does three things during the source build:

1. Fetches that exact upstream commit.
2. Detects whether it is already present or applies its patch.
3. Inspects the resulting source and runs the Chromium
   `BrokerFilePermission` unit tests.

The evidence establishes an improperly returned directory capability. It does
not independently prove arbitrary host-file access, and the project does not
invent an unknown CVE or zero-day label. `docs/FINDING.md` preserves that
boundary. The runtime is additionally isolated so that even a browser defect
does not silently become a host mount or Docker-daemon compromise.

### 3. Reworking the installer around rootless Docker

The first installer expected a non-root caller and immediately refused the
root shell normally used on a fresh DigitalOcean node. It was redesigned into
two phases:

1. A small root phase installs host packages from Docker's signed repository,
   creates `chromebuild`, configures subordinate UID/GID ranges and lingering
   user services, checks out the requested remote ref under `/srv`, verifies
   hashes, installs the reviewed AppArmor policy, and creates root-only helper
   commands.
2. All Docker builds and workloads run as `chromebuild` against its rootless
   Unix socket. The account is never added to the root-equivalent Docker group.

The explicit `REF` handling also fixed an early deployment mistake in which
the managed checkout returned to `main` and contained only the initial commit.
The installer now fetches the requested branch, records the exact commit,
checks it out detached, verifies `SHA256SUMS`, makes it root-owned, and only
then hands execution to the build account.

### 4. Building Chromium exposed the complete dependency chain

Chromium's build scripts assume a much richer build workstation than a slim
container. The failures arrived one prerequisite at a time. Each was fixed in
the builder image or pinned build script instead of being bypassed on the
host.

| Failure observed | Actual cause | Durable correction |
| --- | --- | --- |
| `ModuleNotFoundError: httplib2` | Pinned `depot_tools` imported a system Python module absent from the slim image | Added `python3-httplib2` and the system dist-packages path |
| `lsb_release not found` | Chromium's dependency installer identifies the Linux distribution | Added `lsb-release` |
| `sudo: No such file or directory` | `install-build-deps.py` invokes `sudo apt-get` even while the builder is root | Added `sudo` inside the disposable builder stage |
| `file: No such file or directory` | The dependency script inspects ELF architecture with `file` | Added the `file` package |
| `TarFile.extractall(..., filter=...)` failed | The selected `depot_tools` expected a Python tar API newer than the original builder provided | Moved the pinned builder/runtime base to the compatible Python 3.13 Bookworm image |
| `git apply: failed to read` | The fetched patch was not reliably addressed from the Chromium source working directory | Wrote it to the fixed absolute path `/tmp/sandbox-fix.patch` and checked it before applying |
| Source verifier rejected missing trailing `/.` coverage | Checking only the production symbol could accept an incomplete repair | Required the broker regression suite and self-reference test evidence |
| `python3_bin_reldir.txt` missing | Auto-update was intentionally disabled, but `depot_tools` had not been bootstrapped | Pinned the checkout and called `ensure_bootstrap` explicitly |
| CIPD requested `curl` or `wget` | The bootstrap client had no supported fetch program | Added `curl` to the builder image |
| Missing PGO profile stopped `gn gen` | The pinned checkout had no downloaded production profile | Set `chrome_pgo_phase=0`; this changes optimization, not sandbox behavior |
| Cached Node binary returned `Permission denied` | A BuildKit cache restored tool files without executable mode | Restored execute bits under Chromium's cached Node `bin` directories after hooks |
| Repository manifest omitted a new file | The security test correctly detected that `SHA256SUMS` was incomplete | Regenerated the whole manifest after every reviewed source change |

The dependency list is now declared in `Dockerfile`, while all revision logic
and build gates live in `chromium/build_chromium.sh`. No host-side ad-hoc
package installation is needed to reproduce those fixes.

### 5. Why a small source change still compiled tens of thousands of targets

The security patch was small; the first build was not incremental. Building
the `chrome` executable requires Blink, V8, Skia, networking, media, UI,
generated Mojo bindings, Rust components, DevTools resources, and many
architecture-specific libraries. Siso initially planned roughly 74,000 build
steps because none of those objects existed yet. They were dependencies of the
requested target, not 74,000 files modified by the patch.

On the 8-vCPU/16-GB node, five local jobs kept memory below the host limit but
turned the clean build into an approximately twelve-hour operation. Increasing
parallelism beyond the RAM available would have traded a slow build for OOM
kills and lost work. The long final `ld.lld` processes were expected: linking
the large Chromium binaries is a distinct, CPU- and memory-intensive phase
after compilation.

The final checkpoint design uses locked BuildKit cache mounts for:

- `/opt/depot_tools`
- `/src/chromium`, including the source checkout and `out/Secure` objects
- `/root/.cache` downloads

That makes retries on the same rootless Docker data directory incremental.
The cache is not a magical shard system and is not the distributable product;
the final GHCR image is the disaster-recovery artifact. Deleting the builder
cache makes the next source build slow again but does not affect the restored
runtime.

Docker's progress stream eventually reached its 2-MiB display limit. That
message clipped presentation, not compilation. Progress was confirmed through
the Siso/Clang process tree, CPU use, the builder namespace, and persistent
installer logs. Tmux protected the terminal session, while `/var/log/chrome-patch`
remained the authoritative record when an SSH connection or pane disappeared.

### 6. Packaging only the runtime and proving its identity

After linking, `chromium/package_runtime.py` copied the browser, matching
driver, resources, libraries, locales, and source metadata into the runtime
stage. It deliberately excluded the source tree and compiler toolchain. The
packaged directory contains its own `manifest.sha256`.

Every container entry passes a static gate that verifies:

- Browser and driver are executable, non-writable, and version matched.
- Packaged source metadata records the exact Chromium revision, fix state,
  fix commit, and `depot_tools` revision.
- Every runtime file matches the internal manifest.
- The process is non-root, has zero Linux capabilities, uses a read-only root,
  has `no-new-privileges`, and is already inside an outer seccomp filter.
- Unsafe browser flags, driver downloads, arbitrary proxies, and remote
  Selenium endpoints are absent.

This was the point where the expensive compilation succeeded. It did not yet
mean Selenium could start securely on Ubuntu.

### 7. Solving Ubuntu's `No usable sandbox` failure without weakening the host

ChromeDriver initially timed out because Chromium terminated with:

```text
No usable sandbox!
```

Ubuntu's AppArmor policy had `kernel.apparmor_restrict_unprivileged_userns=1`.
The tempting workarounds were to disable that sysctl globally, use
`--no-sandbox`, add `SYS_ADMIN`, or run privileged. All would have defeated the
point of the container, so none were accepted.

The repair required both host and container policy:

- Preserve Ubuntu's global user-namespace restriction.
- Use the distribution's RootlessKit AppArmor profile and supported local
  include for the dedicated rootless daemon.
- Remove only the byte-for-byte known duplicate legacy RootlessKit profile
  created by an earlier installer revision, after backing it up.
- Load the confined `chrome-patch-browser` AppArmor label for browser jobs.
- Start from Moby's pinned default seccomp profile and permit only the namespace
  combinations Chromium actually needs.
- Continue blocking unrestricted `clone3`, `mount`, `setns`, added
  capabilities, host devices, and host paths.

The first hand-edited AppArmor attempts demonstrated why the policy had to be
reviewed as code: successively denied dynamic libraries, locale and timezone
data, `/proc` status, Unix sockets, Crashpad execution, signals, and process
inspection. The final repository profile declares the complete narrow runtime
set and is validated by `scripts/verify-runtime-profiles.py`; it no longer
depends on interactive `aa-logprof` edits.

Two separate tests now prove the result:

1. `unshare --user --map-root-user` must succeed inside the hardened browser
   service.
2. Selenium must load `chrome://sandbox` and observe Namespace, PID namespace,
   network namespace, and Seccomp-BPF status as enabled, plus Chromium's
   `You are adequately sandboxed.` evaluation.

### 8. Separating offline coursework from opt-in live scraping

The normal Assignment 8 and capstone runs use local HTML fixtures served over
ephemeral loopback HTTP. They have `network_mode: none`, so a routine test
cannot contact the Internet or cloud metadata. They write only to named result
volumes.

Live services are a separate Compose profile. Browser containers have no
direct outbound route; they can reach only an internal Squid service. The proxy
first rejects private, loopback, link-local, and metadata destinations and then
applies the approved HTTPS hostname list. The dashboard is also opt-in and
publishes only to host loopback.

### 9. Making the twelve-hour build recoverable

A successful local image was too expensive to leave on one droplet. The final
runtime base was pushed to GHCR and pinned by its immutable OCI digest in
`chromium/runtime-image.lock.json`. `scripts/ghcr-image.sh`:

- Refuses rootful Docker.
- Reads a short-lived PAT from a hidden terminal prompt.
- Uses `docker login --password-stdin` and an ephemeral mode-0700 config
  directory.
- Verifies the local browser manifest before push.
- Pulls the returned digest after push as a registry verification step.
- Logs out, deletes temporary auth material, and records a local mode-0600
  lock.

The GHCR helper itself requires `read:packages` for a private restore and
`write:packages` for backup. Because this source repository is also private, a
clean-host clone needs repository read access. The simplest one-token restore
uses a short-lived classic PAT with only `repo` and `read:packages`; add
`write:packages` only when uploading a replacement image. Workflow,
delete-package, user, organization, and administrative scopes are unnecessary.

The GHCR image contains the compiled browser but may contain application code
from the build date. A restore therefore does not blindly run that old `/app`.
`docker/Dockerfile.runtime-refresh` verifies that `requirements.txt` has not
drifted, reuses the pinned Python environment and `/opt/chromium` tree, and
replaces only the current application layer. A dependency change fails closed
and requires a reviewed full image build.

Both `--restore-image` and `--skip-build` now perform that application refresh,
verify the resulting image, and execute the complete runtime validation.

### 10. The final regression was outside Chromium

After the browser sandbox and both coursework pipelines passed, the first
result viewer still exited with code 126. The shared container entrypoint was
starting a second Selenium sandbox probe before every command—including
read-only CSV/JSON viewers that intentionally used the non-browser Compose
policy.

The fix did not disable the gate. It split its responsibilities:

- Every command still passes browser-file integrity, version, environment, and
  container-boundary checks.
- Browser scrapers and test commands still require the dynamic Selenium probe.
- Only four exact commands—the two result viewers, summary query, and fixed
  dashboard command—skip the unnecessary browser launch.
- Extra arguments, similar filenames, arbitrary shells, and modified commands
  fall back to requiring the browser probe.

`tests/test_entrypoint.py` locks that behavior down. The final `--skip-build`
run refreshed this fix into the existing image and reached
`FULL VALIDATION PASSED` without recompiling Chromium or weakening the sandbox.

### 11. What is preserved now

| Asset | Location | Purpose |
| --- | --- | --- |
| Reproducible source and policy | This Git branch and PR | Rebuild, review, and future maintenance |
| Known-good runtime commit | `d955c3ca2e1fbd2756c629d024690704cb77cc8f` | Exact code that passed on the target node |
| Compiled Chromium base | Pinned GHCR digest | Restore without another twelve-hour compilation |
| Repository integrity manifest | `SHA256SUMS` | Detect missing or modified project files |
| Browser integrity manifest | `/opt/chromium/manifest.sha256` | Detect packaged runtime modification |
| Mutable incremental cache | Rootless Docker BuildKit data | Accelerate same-host source-build retries |
| Named result volumes | Rootless Docker volume store | Persist coursework CSV, JSON, and SQLite outputs |
| Host evidence | `/var/log/chrome-patch` and `/var/lib/chrome-patch/results` | Retain logs, summaries, and copied test output |

The final node evidence for the validated run was written to:

```text
/var/log/chrome-patch/install-20260822T020520Z.log
/var/log/chrome-patch/bootstrap-20260822T020513Z.log
/var/lib/chrome-patch/results/20260822T020520Z
```

No PAT, Docker authentication file, browser profile, cookie, credential, host
file, or build secret is committed. The full recovery and update procedure is
in `docs/REPRODUCIBILITY.md`.

## Mini-blog and build journal: the twenty-hour Chromium side quest

> What started out as a simple “code-the-dream” test environment, created
> because of limitations on my host machine, turned into a twenty-hour
> Chromium / secure scraping surface / Docker / GHCR / AppArmor /
> user-namespace side quest—a super-build.

That sentence is the shortest honest description of this project. The longer
version is a story about how software changes category while it is being built.
A scraper looks small when described from the outside: open a page, inspect the
DOM, extract fields, clean rows, save a CSV. That mental model is accurate only
at the application layer. The moment the scraper must execute an actual browser
on a remote machine, distrust its input, preserve evidence, survive a lost SSH
connection, avoid touching the host, and be reproducible months later, the
problem is no longer “write a Python script.” It becomes systems engineering.

### Journal entry 1: the small idea

The original idea was practical. My local host had environmental limitations,
so I wanted a disposable DigitalOcean test environment for Week 8 and the
capstone. Docker looked like the clean boundary: put Selenium and its
dependencies in a container, run the assignments, inspect the results, and
throw the machine away when finished. The first version of the dream was almost
classical in its simplicity:

```text
Python scraper -> Selenium -> browser -> website -> CSV
```

But each arrow concealed a trust decision. Which browser binary? Which driver?
Who downloaded it? Did the versions match? Could the browser see the host? Was
the Docker daemon rootful? Could a compromised renderer reach a metadata
service? Would a future package update silently replace the tested browser? If
the build succeeded once, could it be reproduced without spending another day
compiling?

The improved model became:

```text
pinned source
  -> reviewed patch
  -> deterministic build gates
  -> hashed runtime package
  -> rootless container boundary
  -> browser sandbox proof
  -> controlled scraping job
  -> validated persistent result
```

The second diagram is longer because the real system was always longer. The
project did not create that complexity; it made the hidden complexity visible.

### Journal entry 2: the host said no

The first DigitalOcean node was a 1-vCPU/2-GB droplet. The installer also
refused to run as root, even though root is the normal starting context on a
fresh server. Those two failures taught different lessons. The root failure was
an architecture problem: host provisioning legitimately needs root, but the
browser workload should never inherit it. The capacity failure was physics:
Chromium cannot be negotiated into a tiny memory budget by enthusiasm or a
smaller `BUILD_JOBS` value.

The installer was split into a root bootstrap and an unprivileged execution
phase. The node was resized first to 4 vCPUs/8 GB and eventually to 8 vCPUs/16
GB. The normal preflight still recommends a larger builder because it is the
reliable choice; the explicit small-builder mode exists because we accepted a
different point in the time-memory trade space.

The basic build-time model is:

```text
T_build(J) = T_serial + T_parallel / J + T_IO(J) + T_link + T_validation
```

where `J` is the local compiler-job count. If compilation were perfectly
parallel, doubling `J` would halve the parallel term. Chromium is not perfectly
parallel. Source synchronization, code generation, disk contention, and final
linking remain partly serial. Worse, memory use grows with concurrent workers:

```text
M_peak(J) ~= M_OS + J * M_worker + M_link + M_cache
J_safe <= floor((M_host - M_OS - M_link - M_cache) / M_worker)
```

Eight virtual CPUs did not imply eight safe compiler jobs. Five jobs kept the
16-GB machine productive without deliberately walking it into an OOM kill.
That choice made the build slower, but “slow and completing” dominated “fast
until the kernel kills it.”

### Journal entry 3: death by one missing package at a time

Once the host was large enough, the builder began teaching us what a Chromium
workstation contains. `httplib2` was missing. Then `lsb_release`. Then `sudo`.
Then `file`. A Python tar API was too old for the pinned `depot_tools`. The
bootstrap metadata was incomplete. CIPD could not find a download program. GN
wanted a PGO profile that had never been synchronized. A cached Node binary
existed but had lost its executable permission.

Each error looked absurd in isolation. Why would a root-owned container need
`sudo`? Because the Chromium dependency script invokes it as part of its own
contract. Why would `file` matter? Because the script uses it to distinguish
ELF architectures. Why would the build care about Node? Because DevTools
frontend assets are part of Chrome. A browser is not one program. It is a
federation of C++, Rust, JavaScript, generated interfaces, graphics libraries,
network stacks, codecs, resources, and packaging tools.

The wrong response would have been to install random packages manually on the
droplet until the current shell happened to work. That creates a successful
machine but not a reproducible system. Every discovered prerequisite was moved
into the builder Dockerfile or pinned build script. Failure became a form of
dependency discovery, and dependency discovery became source code.

This pattern can be written as a loop:

```text
observe failure
  -> identify violated assumption
  -> encode prerequisite or invariant
  -> add a regression check
  -> rebuild from the last trustworthy checkpoint
```

The crucial word is “encode.” If the fix lives only in terminal history, it is
not a fix; it is an anecdote.

### Journal entry 4: 74,000 steps and the long middle

Then the real compile began. The progress counter showed roughly 74,000
actions. That number felt incompatible with a minor broker-path patch, but it
was not the number of changed files. It was the transitive work required to
produce clean `chrome`, `chromedriver`, and sandbox unit-test targets from an
empty output directory.

The dependency graph explains it:

```text
Work(target) = union of every unbuilt transitive dependency of target
```

For a clean Chromium target, that union is enormous. Blink does not disappear
because the patch was in the Linux sandbox. Skia, V8, Mojo, networking, media,
DevTools, resources, XNNPACK variants, Rust libraries, and generated code still
have to exist before the linker can produce the final browser.

For parallel fraction `p`, Amdahl's law gives an optimistic ceiling:

```text
Speedup(J) = 1 / ((1 - p) + p / J)
```

The actual speedup was lower because the droplet shared storage and because
linking could not be divided into thousands of independent tasks. The build ran
for hours. Docker clipped the visible log at 2 MiB. An SSH terminal died. The
old log counter stopped changing even while `clang++` processes continued to
consume CPU. We learned to distinguish a dead presentation channel from a dead
build by checking Siso, compiler workers, process elapsed time, CPU saturation,
and the builder namespace.

BuildKit cache mounts changed retries from catastrophe into inconvenience. The
retry equation became:

```text
T_retry ~= T_sync_delta + T_invalidated_objects + T_relink + T_tests
```

instead of repeating `T_clean_build`. The source checkout, downloads,
`depot_tools`, and `out/Secure` objects remained inside the rootless Docker data
directory. The cache did not make the first build fast. It made accumulated
work survive mistakes.

Eventually `clang++` gave way to multiple `ld.lld` processes. That was the
moment the project crossed from “still manufacturing pieces” to “assembling the
browser.” The linker was slow, memory-hungry, and glorious. When the image
finally appeared, it represented much more than one patched source file: it was
the closure of the dependency graph under a fixed toolchain.

### Journal entry 5: the security claim had to become smaller to become true

The motivating sandbox issue involved a Linux broker validator accepting a
self-reference path component such as a trailing `/.`. In a recursive
allowlisted directory, accepting that component could cause the broker to
return a directory capability at a boundary that should have been rejected.
The upstream change used by this repository rejects parent and self references
and adds regression coverage.

It was tempting to translate that into a dramatic claim about arbitrary
host-file access or an unknown zero-day. We did not. A returned file descriptor
is a capability, but its real authority depends on the directory it names, the
syscalls available to the process, the mount namespace, and the container's
host exposure. Security reasoning fails when possibility is promoted to proof.

The honest implication is:

```text
broker validation flaw
  -> improperly returned directory capability
  -> possible additional reach inside the broker's visible boundary
  != automatically proven arbitrary host-file access
```

That smaller claim is stronger because it can survive review. The source fix
is applied or proven present, source structure is inspected, the upstream
regression suite is required, and the specific Chromium sandbox unit tests are
executed. Then runtime controls reduce the value of any browser capability by
removing host mounts, devices, capabilities, Docker sockets, and unrestricted
network destinations.

### Journal entry 6: building Chrome was easier than safely starting Chrome

The source build eventually passed, yet Selenium immediately failed with
`No usable sandbox!`. This was the most important decision point in the entire
journey. The easy commands were well known: disable the sandbox, run privileged,
add `SYS_ADMIN`, use an unconfined profile, or set Ubuntu's global
`apparmor_restrict_unprivileged_userns` control to zero. Any of them might have
made the browser start. Any of them would also have invalidated the security
goal.

We chose to make the policy accurately describe the required behavior instead
of erasing the policy. That meant learning how rootless Docker's user namespace
interacted with Chromium's nested user, PID, and network namespaces; how Ubuntu
labeled RootlessKit; how Compose attached AppArmor; and how Docker's seccomp
profile treated `clone`, `clone3`, and `unshare`.

The effective authority of the process can be pictured as an intersection:

```text
Authority_effective =
    capabilities
  INTERSECT seccomp-permitted syscalls
  INTERSECT AppArmor-permitted operations
  INTERSECT namespace-visible resources
  INTERSECT mounted filesystems
  INTERSECT network-reachable destinations
  INTERSECT browser enterprise policy
```

An operation denied by any layer remains unavailable. Defense in depth is not
“many security words”; it is the deliberate reduction of the intersection.

The AppArmor work was painful because the first profile was narrower than the
actual runtime. The loader could not read `libdl.so.2`. Then locale data,
timezone data, `/proc` state, Unix sockets, Crashpad execution, signals, and
process inspection surfaced one after another. Interactive experiments helped
identify the runtime, but a pile of `aa-logprof` answers was not an acceptable
final product. The final policy was rewritten, checked into the repository,
validated structurally, installed automatically, and attached only to browser
services.

The seccomp profile followed the same philosophy. It was derived from a pinned
Moby default and modified only for Chromium's exact namespace creation patterns.
It did not become `unconfined`. `clone3`, `mount`, and `setns` stayed
capability-gated, and the container still had every Linux capability dropped.

The resulting proof had two levels. First, a nested user namespace had to be
created inside the container. Second, a real Selenium session had to visit
`chrome://sandbox` and read Chromium's own status table. Only then did the
system accept:

```text
Layer 1 Sandbox   = Namespace
PID namespaces    = Yes
Network namespaces = Yes
Seccomp-BPF        = Yes
Overall evaluation = You are adequately sandboxed.
```

### Journal entry 7: a secure scraper is a constrained data pipeline

With the browser alive, the coursework architecture could finally be judged as
a security system rather than a collection of scripts. Offline fixtures run
without a network interface. Live jobs must opt into a separate profile. Even
then, the browser cannot speak directly to the Internet; it reaches a private
proxy that rejects local, private, link-local, and cloud-metadata addresses
before evaluating the approved hostname list.

The reachable attack surface is therefore not the union of everything the
browser knows how to do. It is closer to:

```text
Reachable_surface =
    browser_features
  INTERSECT container_syscalls
  INTERSECT container_mounts
  INTERSECT namespace_view
  INTERSECT proxy_allowlist
  INTERSECT site-specific workflow
```

Results cross the ephemeral-container boundary through named volumes, not host
directory mounts. Assignment 8 produces book and OWASP datasets. The capstone
preserves raw observations, cleans city names and temperatures, calculates both
temperature scales, classifies bands, writes CSV, and loads SQLite. Its basic
numeric transformations remain inspectable:

```text
F = (9 / 5) * C + 32
average_C = sum(C_i) / n
```

For the final fixture set, `n = 9`, the minimum was 13.0 °C, the mean was
24.0 °C, and the maximum was 38.0 °C. Those numbers matter less than the path
that produced them: scrape, preserve raw, normalize, validate, persist, query.

### Journal entry 8: backup had to include identity, not just bytes

After roughly a day of work, leaving the only compiled browser on one droplet
would have been reckless. BuildKit cache was useful but local and mutable. A
Docker tag was readable but mutable. The recovery artifact needed an immutable
registry digest plus an internal manifest and source metadata.

The integrity model became a conjunction:

```text
Trusted_runtime =
    approved_version
  AND matching_driver_version
  AND approved_source_revision
  AND required_fix_state
  AND approved_toolchain_revision
  AND valid_runtime_hashes
  AND valid_container_boundary
  AND passing_dynamic_sandbox_probe
```

For a file `x`, the basic integrity observation is:

```text
H_x = SHA-256(bytes(x))
accept x only when H_x == manifest[x]
```

Hashing does not prove that code is benevolent; it proves that the reviewed
bytes are the bytes being executed. That distinction is central to reproducible
operations.

The image was pushed to GHCR, pulled back by its returned digest, and locked in
the repository. Token handling was treated as another boundary: minimal package
scope, hidden prompt, `--password-stdin`, ephemeral Docker configuration,
logout, deletion, and revocation. The desired credential exposure can be
summarized qualitatively as:

```text
Credential_risk proportional to privilege_scope * lifetime * persistence
```

Reducing all three factors is better than trusting one long-lived powerful
token to remain secret forever.

One subtle problem remained. The backed-up image captured `/app` at build time,
while the repository continued to improve. Restoring it verbatim would restore
an old security gate. The final recovery flow therefore treats the GHCR image
as the expensive compiled base. It verifies dependency equality and refreshes
only the application layer from the current hashed checkout. Chromium does not
recompile, but stale Python code does not survive merely because the browser
was expensive.

### Journal entry 9: the last failure arrived after success

The restored image passed the nested namespace test. It passed
`chrome://sandbox`. The test container passed. Assignment 8 produced its books
and OWASP rows. The capstone produced nine cleaned weather records and a valid
database. Then the read-only result viewer failed with exit code 126.

That failure was almost funny. The browser security gate was so aggressive
that it launched a dynamic Selenium probe before commands that did not use a
browser. Result viewers inherited the static container policy, not the special
browser AppArmor label, so the unnecessary probe died. The secure browser was
working; the presentation command was asking it to work where it did not
belong.

The correction preserved the principle while improving its precision. Every
command still verifies image integrity and the outer container boundary.
Browser commands still prove the dynamic sandbox. Four exact non-browser
commands skip only that dynamic launch. Similar paths, extra arguments, or an
arbitrary shell are not accepted as viewers. This was a final lesson in policy
design: an invariant that is too broad can become both unusable and less clear.
The answer is not to remove the invariant, but to state exactly where it
applies and test the boundary cases.

### Epilogue: what failure contributed

The final line—`FULL VALIDATION PASSED`—was not the erasure of everything that
went wrong. It was the compression of those failures into a working system.
`httplib2` became a declared dependency. The dead SSH connection became
persistent logging and tmux discipline. The clipped Docker output became
process-level observation. The twelve-hour compile became BuildKit checkpoints
and an immutable GHCR base. The AppArmor denials became a reviewed profile. The
seccomp conflict became an exact namespace policy. The stale image became an
application-refresh stage. The viewer failure became an exact command policy
and regression tests.

The deepest lesson is that secure engineering is not the absence of failure.
It is the conversion of each failure into an explicit boundary, repeatable
procedure, or automated test. We did not finish by finding a command that made
Chrome open. We finished when a new checkout could reconstruct the environment,
prove what it built, start Chromium without a sandbox bypass, run both projects,
validate their data, preserve the results, and recover the expensive artifact
without inheriting stale code or persistent credentials.

What began as a workaround for a constrained host became a compact browser
laboratory. It now has source provenance, binary integrity, least-privilege
execution, network containment, sandbox evidence, persistent artifacts,
disaster recovery, and an honest account of what the security fix does and does
not prove. The side quest became the project—and the project became a system.

## One-command build, audit, test, and results

The Chromium source build is large. Use a Linux amd64 machine with at least 16
vCPUs, 32 GB RAM, and 200 GB free SSD. The first build can take hours. Runtime
nodes should normally use the private GHCR restore procedure in the next
section instead.

After installing the private bootstrap command documented below, a full source
build is:

```bash
sudo env SMALL_BUILDER=0 BUILD_JOBS=16 \
  /usr/local/sbin/chrome-patch-private-bootstrap rebuild
```

`install.sh` performs the full workflow:

1. Checks every repository file in `SHA256SUMS`, validates amd64, and measures CPU, RAM, and free disk before changing the host.
2. On Ubuntu/Debian, installs Docker Engine, Compose v2, and rootless prerequisites from Docker's signed apt repository when they are missing.
3. Creates the dedicated `chromebuild` account without `sudo` or Docker-group membership and starts a rootless Docker daemon for it.
4. Fetches the selected remote branch into `/srv/chrome-patch`, checks out its exact commit detached, verifies its hashes, and makes the source root-owned/read-only to the build account.
5. Resolves the Debian and Python base images to immutable registry digests, then builds the pinned Chromium and matching ChromeDriver from source.
6. Installs and validates a confined Chromium AppArmor profile plus an exact, Moby-derived seccomp namespace allowlist; the global Ubuntu user-namespace restriction stays enabled.
7. Runs a nested-user-namespace preflight, the browser sandbox audit, Assignment 8, capstone, output checks, and capstone query.
8. Saves bootstrap/build logs under `/var/log/chrome-patch/` and timestamped results under `/var/lib/chrome-patch/results/`.

This follows the useful deployment pattern from `roadscanner/docker-install.sh`: root performs host provisioning, but Docker and the project workload run under a dedicated rootless service account. The Chromium build retains its own stricter source, binary, and runtime sandbox gates.

After installation, root can inspect the isolated daemon and replay the result viewers without rebuilding:

```bash
chrome-patch-docker ps
chrome-patch-results
```

### Host changes made by the root phase

- Adds Docker's HTTPS apt source and signing key only when the complete rootless Docker stack is unavailable; it refuses conflicting distro Docker packages instead of silently removing them.
- Creates `chromebuild`, subordinate UID/GID ranges, a lingering user systemd manager, and a user-owned rootless Docker data directory. The account is removed from `sudo`, `wheel`, and `docker`, then login-locked after a successful run.
- Manages source only at `/srv/chrome-patch`, state/results only at `/var/lib/chrome-patch`, logs only at `/var/log/chrome-patch`, and three root-only helper commands under `/usr/local/bin`.
- On Ubuntu systems that restrict unprivileged user namespaces, uses the distribution's `rootlesskit` profile and its supported `local/rootlesskit` include. It also loads the confined `chrome-patch-browser` profile for browser containers. The installer never disables `kernel.apparmor_restrict_unprivileged_userns` globally.
- Attaches `docker/chromium-seccomp.json`, derived from Moby's default profile at commit `f9bc03ec19b2dc4c091449b08e88f85c0caa9f0b`. Its only non-default namespace permissions are `CLONE_NEWUSER` and Chromium's exact user/PID/network combinations. `clone3`, `mount`, and `setns` remain blocked without `CAP_SYS_ADMIN`; the containers receive zero capabilities.

The bootstrap is based on [`roadscanner/docker-install.sh`](https://github.com/ornab74/roadscanner/blob/main/docker-install.sh) and Docker's official [Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [Debian](https://docs.docker.com/engine/install/debian/), and [rootless-mode](https://docs.docker.com/engine/security/rootless/) documentation. It never executes Docker's convenience script.

The run succeeds only if Chromium reports all of these as enabled:

- Seccomp-BPF sandbox
- PID namespaces
- Network namespaces

Do not add `--no-sandbox`, `SYS_ADMIN`, privileged mode, an unconfined security profile, or a host mount to make a failed audit pass.

### Private-repository clean-host restore (recommended)

This is the supported path for a new runtime node. It restores the completed
Chromium base from the immutable GHCR digest, refreshes the application layer,
and performs full validation without recompiling Chromium.

Before starting:

1. Use a Linux amd64 Ubuntu or Debian **host VM with at least 2 vCPUs, 4 GB of
   physical RAM, and 10 GB of free disk**. Run these commands on the host, not
   inside another Docker container.
2. Create a short-lived classic GitHub PAT with only `repo` and
   `read:packages`. The first scope reads the private source repository; the
   second pulls the private GHCR package. Add `write:packages` only if you will
   upload an image later.
3. Never paste the PAT into a URL, command argument, shell history, Git config,
   file, or chat. The bootstrap below reads it from a hidden terminal prompt,
   keeps the Git credential in an in-memory one-hour cache, and removes the
   credential on exit.

Install the private bootstrap command:

```bash
sudo tee /usr/local/sbin/chrome-patch-private-bootstrap >/dev/null <<'SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly MODE="${1:-restore}"
readonly GITHUB_USER="ornab74"
readonly REPO_URL="https://github.com/ornab74/chrome-patch.git"
readonly BRANCH="codex/hardened-assignment8-capstone"
readonly SERVICE_USER="chromebuild"
readonly SERVICE_HOME="/home/chromebuild"
readonly BOOTSTRAP_DIR="/opt/chrome-patch-bootstrap"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

(( EUID == 0 )) || fail "run this script as root using sudo"
[[ ! -e /.dockerenv ]] ||
  fail "run on the Ubuntu/Debian VM host, not inside a Docker container"
[[ "$(uname -m)" == "x86_64" ]] ||
  fail "the pinned image requires linux/amd64"

case "$MODE" in
  restore|refresh|rebuild) ;;
  *) fail "usage: $0 {restore|refresh|rebuild}" ;;
esac

. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *) fail "supported host OS: Ubuntu or Debian" ;;
esac

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  git ca-certificates curl tmux

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$SERVICE_USER"
else
  usermod --home "$SERVICE_HOME" --shell /bin/bash "$SERVICE_USER"
fi

install -d -m 0750 \
  -o "$SERVICE_USER" -g "$SERVICE_USER" \
  "$SERVICE_HOME"

run_builder() {
  runuser -u "$SERVICE_USER" -- env \
    HOME="$SERVICE_HOME" \
    USER="$SERVICE_USER" \
    LOGNAME="$SERVICE_USER" \
    PATH="/usr/local/bin:/usr/bin:/bin" \
    "$@"
}

cleanup() {
  local status=$?
  set +e
  if id "$SERVICE_USER" >/dev/null 2>&1; then
    printf 'protocol=https\nhost=github.com\nusername=%s\n\n' \
      "$GITHUB_USER" |
      run_builder git credential reject >/dev/null 2>&1
    run_builder git credential-cache exit >/dev/null 2>&1
    run_builder git config --global \
      --unset-all credential.helper >/dev/null 2>&1
  fi
  unset GH_PAT 2>/dev/null || true
  return "$status"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[[ -r /dev/tty ]] ||
  fail "an interactive terminal is required for the hidden token prompt"

printf 'GitHub PAT classic (repo + read:packages; input hidden): ' >/dev/tty
IFS= read -r -s GH_PAT </dev/tty
printf '\n' >/dev/tty
[[ -n "$GH_PAT" ]] || fail "empty token"

run_builder git config --global \
  credential.helper 'cache --timeout=3600'

printf 'protocol=https\nhost=github.com\nusername=%s\npassword=%s\n\n' \
  "$GITHUB_USER" "$GH_PAT" |
  run_builder git credential approve

run_builder git ls-remote --exit-code \
  "$REPO_URL" "refs/heads/${BRANCH}" >/dev/null ||
  fail "the token cannot read the private repository branch"

unset GH_PAT

if [[ -d "$BOOTSTRAP_DIR/.git" ]]; then
  [[ -z "$(run_builder git -C "$BOOTSTRAP_DIR" status --porcelain)" ]] ||
    fail "$BOOTSTRAP_DIR has local changes; refusing to overwrite them"
elif [[ -e "$BOOTSTRAP_DIR" ]]; then
  fail "$BOOTSTRAP_DIR exists but is not a Git checkout"
else
  install -d -m 0750 \
    -o "$SERVICE_USER" -g "$SERVICE_USER" \
    "$BOOTSTRAP_DIR"
  run_builder git clone --no-checkout \
    "$REPO_URL" "$BOOTSTRAP_DIR"
fi

run_builder git -C "$BOOTSTRAP_DIR" \
  remote set-url origin "$REPO_URL"

run_builder git -C "$BOOTSTRAP_DIR" \
  fetch --force --prune origin \
  "refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"

target_commit="$(
  run_builder git -C "$BOOTSTRAP_DIR" \
    rev-parse "refs/remotes/origin/${BRANCH}"
)"

[[ "$target_commit" =~ ^[0-9a-f]{40}$ ]] ||
  fail "could not resolve the branch commit"

run_builder git -C "$BOOTSTRAP_DIR" \
  checkout --detach --force "$target_commit"

(
  cd "$BOOTSTRAP_DIR"
  sha256sum --check --strict SHA256SUMS
)

cd "$BOOTSTRAP_DIR"

case "$MODE" in
  restore)
    printf '\nEnter the same PAT again when the GHCR prompt appears.\n'
    REF="$BRANCH" bash ./install.sh --restore-image
    ;;
  refresh)
    REF="$BRANCH" bash ./install.sh --skip-build
    ;;
  rebuild)
    REF="$BRANCH" \
      SMALL_BUILDER="${SMALL_BUILDER:-1}" \
      BUILD_JOBS="${BUILD_JOBS:-5}" \
      bash ./install.sh
    ;;
esac
SCRIPT

sudo chmod 0700 /usr/local/sbin/chrome-patch-private-bootstrap
sudo bash -n /usr/local/sbin/chrome-patch-private-bootstrap
```

Run the restore in a named `tmux` session so an SSH or DigitalOcean console
disconnect does not terminate it. The final `exec bash` deliberately keeps the
session open after success or failure so its output remains inspectable:

```bash
sudo apt-get update
sudo apt-get install -y tmux

SESSION="chrome-restore-$(date -u +%Y%m%dT%H%M%SZ)"
sudo tmux new-session -s "$SESSION" \
  "bash -lc 'BUILD_JOBS=1 /usr/local/sbin/chrome-patch-private-bootstrap restore; rc=\$?; echo; echo RESTORE_RC=\$rc; exec bash'"
```

The restore has two hidden prompts. Enter the same PAT first for the private Git
checkout and again when the GHCR helper requests `read:packages`. Nothing is
displayed while the token is typed; this is expected.

Inside `tmux`, detach with `Ctrl-b`, release both keys, and press `d`. Reattach
from a later shell with:

```bash
sudo tmux list-sessions
sudo tmux attach-session -t "$SESSION"
```

If the shell no longer has the `SESSION` variable, copy the session name shown
by `sudo tmux list-sessions`. Do not press `Ctrl-c` merely to detach; that sends
an interrupt to the foreground command.

The successful run must end with `FULL VALIDATION PASSED`. Verify and replay the
proof commands without rebuilding:

```bash
sudo chrome-patch-image verify
sudo chrome-patch-compose run --rm browser-audit
sudo chrome-patch-compose run --rm test
sudo chrome-patch-results
```

The pinned recovery image used by restore is:

```text
ghcr.io/ornab74/chrome-patch-chromium@sha256:f212beff4487370a6c026a1834f779657e76c7091c6ae42b4d2cee8cfe42d2f3
```

### Refresh or rebuild after the first restore

Use `refresh` on a machine that already has the verified local Chromium image.
It updates the private checkout, refreshes only the application layer, verifies
the browser, and reruns the security and coursework checks:

```bash
sudo /usr/local/sbin/chrome-patch-private-bootstrap refresh
```

Use `rebuild` only when Chromium itself must be compiled again. A small source
builder needs at least 4 vCPUs, 8 GB RAM, and 180 GB free disk and can still take
many hours:

```bash
sudo env SMALL_BUILDER=1 BUILD_JOBS=5 \
  /usr/local/sbin/chrome-patch-private-bootstrap rebuild
```

`refresh` and `restore` do not compile Chromium. See
[`docs/REPRODUCIBILITY.md`](docs/REPRODUCIBILITY.md) for image backup, restore,
and future Chromium/Selenium update procedures.

## Installer options

```bash
./install.sh --preflight-only # hashes plus CPU/RAM/disk; makes no host changes
./install.sh --local-only   # hashes, Python tests, shell/YAML checks; no Docker build
./install.sh --build-only   # local checks plus the Chromium image build
./install.sh --skip-build   # test an image already built from this exact source
./install.sh --restore-image # pull the pinned GHCR digest, audit, and test it
./install.sh --help
```

Useful individual commands:

```bash
make local-check
make build
make audit
make test
make assignment8
make capstone
make results
make query
make image-verify
make image-backup
make image-restore
make runtime-refresh
```

The normal fixture jobs have `network_mode: none`. Fixtures are served from ephemeral loopback-only HTTP servers, so Chromium never receives a `file://` URL. Result artifacts live in Docker named volumes; `install.sh` prints them and records the output on the host.

## SHA-256 verification

Verify the checked-in source manifest directly:

```bash
sha256sum --check --strict SHA256SUMS
```

After an intentional, reviewed source change, regenerate it with:

```bash
./scripts/generate-sha256.sh
sha256sum --check --strict SHA256SUMS
```

The manifest detects accidental or post-checkout file changes. It is not a substitute for verifying the Git commit or a future signed release, because an attacker able to replace both a file and the manifest can create matching hashes. The root bootstrap therefore fetches the selected remote ref, records its commit and source-archive SHA-256, and never builds from the caller's mutable `/root` checkout.

The built browser has a second independent manifest at `/opt/chromium/manifest.sha256`. The container entry point checks that manifest, source revision, exact browser/driver versions, required fix metadata, UID/capabilities, read-only root, `no-new-privileges`, and the outer seccomp filter before starting Selenium.

## Optional live requests

Live scraping is deliberately opt-in. Review current robots rules and site terms immediately before each run:

```bash
make live-assignment8  # Durham County book search
make live-owasp
make live-capstone
```

On the managed DigitalOcean installation, use the root-only wrapper so the
jobs run through the dedicated rootless Docker daemon:

```bash
chrome-patch-compose --profile live run --rm assignment8-books-live
chrome-patch-compose --profile live run --rm assignment8-owasp-live
chrome-patch-compose --profile live run --rm capstone-live
chrome-patch-results
```

Live browser traffic can reach only an internal Squid proxy. The proxy blocks private, loopback, link-local, and cloud-metadata destinations before applying an HTTPS/443 domain allowlist; it has no published host port. The browser container itself has no direct outbound network.

Project references: [OWASP robots.txt](https://owasp.org/robots.txt), [MDN DOM](https://developer.mozilla.org/en-US/docs/Web/API/Document_Object_Model), [Selenium WebDriver](https://www.selenium.dev/documentation/webdriver/), and [Timeanddate weather](https://www.timeanddate.com/weather/).

## Dashboard

```bash
make dashboard
```

The dashboard profile is disabled by default and binds only to `127.0.0.1:8501`. On a server, use the SSH tunnel in [`docs/DIGITALOCEAN.md`](docs/DIGITALOCEAN.md); never bind it to a public interface.

## Security claim and update policy

The repaired Chromium broker validation accepted a trailing current-directory reference such as `/.`; the upstream fix rejects it. This proves an improperly returned directory capability. It does not, by itself, prove arbitrary host-file access, and this repository does not claim an unknown zero-day or public CVE. See [`docs/FINDING.md`](docs/FINDING.md) for the exact evidence boundary.

No browser can honestly be guaranteed free of every vulnerability. This repo instead permits one reviewed source build and fails closed if anything differs. To update Chromium or its build toolchain, change the coordinated pins in `chromium/source.lock.json`, `Dockerfile`, `compose.yaml`, and `common/security_gate.py`; review the fix status; rebuild without cache; then rerun the complete installer.

See [`SECURITY.md`](SECURITY.md) for enforced invariants and [`docs/DIGITALOCEAN.md`](docs/DIGITALOCEAN.md) for deployment guidance.
The complete source-build and immutable-image recovery model is documented in
[`docs/REPRODUCIBILITY.md`](docs/REPRODUCIBILITY.md).
