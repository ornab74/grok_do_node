# Runtime validation evidence

This directory preserves operator-captured evidence from completed hardened
Chromium/Selenium runs. It is intentionally separate from fixtures and source
tests: fixtures prove deterministic program behavior, while these records show
what occurred on the target runtime host.

## Evidence index

| Run | Outcome | Runtime commit | Evidence |
| --- | --- | --- | --- |
| `20260822T020520Z` | `FULL VALIDATION PASSED` | [`d955c3ca`](https://github.com/ornab74/chrome-patch/commit/d955c3ca2e1fbd2756c629d024690704cb77cc8f) | [Results and verification record](runs/20260822T020520Z/RESULTS.md) |

## Evidence model

The bundle uses several independent forms of evidence:

1. The terminal capture records the exact visible installer, test, sandbox,
   scraper, and query output supplied by the operator.
2. The screenshot records the final terminal state and successful validation
   marker as it appeared on the DigitalOcean node.
3. `validation-summary.json` makes the important versions, digests, sandbox
   checks, test counts, and result counts machine-readable.
4. Repository `SHA256SUMS` covers every evidence file, so later modification is
   detected by the same fail-closed integrity gate used for source files.
5. The immutable GHCR digest and pinned source commit allow another operator to
   reproduce the environment instead of trusting the captured output alone.

These records are supporting evidence, not a third-party audit, remote
attestation, or cryptographic proof that the screenshot originated on a
particular physical machine. The strongest verification remains independent
reproduction from the pinned commit and immutable image digest followed by a
fresh sandbox audit.

## Privacy and repository policy

Before publication, the capture was checked for GitHub PAT formats,
authorization headers, bearer credentials, and password/token assignments. No
matching credential material was present. Browser profiles, Docker credential
stores, host SSH keys, and cloud metadata are not included.

Ephemeral container names are retained because the capture is verbatim. They
are runtime identifiers, not reusable credentials.
