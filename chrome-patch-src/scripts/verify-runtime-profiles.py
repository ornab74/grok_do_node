#!/usr/bin/env python3
"""Fail-closed validation for the checked-in AppArmor and seccomp policies."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SECCOMP_PATH = ROOT / "docker" / "chromium-seccomp.json"
APPARMOR_PATH = ROOT / "docker" / "chrome-patch-browser.apparmor"

# Every Linux namespace bit understood by clone/unshare, including CLONE_NEWTIME.
NAMESPACE_MASK = 0x7E020080
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000
EXPECTED_CHROMIUM_MASKS = {
    CLONE_NEWPID,
    CLONE_NEWUSER,
    CLONE_NEWUSER | CLONE_NEWPID,
    CLONE_NEWUSER | CLONE_NEWNET,
    CLONE_NEWUSER | CLONE_NEWPID | CLONE_NEWNET,
}


def fail(message: str) -> None:
    raise ValueError(message)


def caps(rule: dict[str, Any], condition: str) -> set[str]:
    return set(rule.get(condition, {}).get("caps", []))


def one_masked_arg(rule: dict[str, Any], index: int) -> tuple[int, int] | None:
    args = rule.get("args", [])
    if len(args) != 1:
        return None
    arg = args[0]
    if arg.get("index") != index or arg.get("op") != "SCMP_CMP_MASKED_EQ":
        return None
    return int(arg.get("value", -1)), int(arg.get("valueTwo", 0))


def validate_seccomp() -> None:
    profile = json.loads(SECCOMP_PATH.read_text(encoding="utf-8"))
    if profile.get("defaultAction") != "SCMP_ACT_ERRNO":
        fail("seccomp default action must be SCMP_ACT_ERRNO")
    if profile.get("defaultErrnoRet") != 1:
        fail("seccomp default errno must remain EPERM")

    rules = profile.get("syscalls")
    if not isinstance(rules, list):
        fail("seccomp syscalls must be a list")

    chromium_clone_masks: set[int] = set()
    exact_user_unshare = 0
    exact_chroot = 0
    clone3_enosys = 0

    for rule in rules:
        names = set(rule.get("names", []))
        action = rule.get("action")
        sys_admin_only = caps(rule, "includes") == {"CAP_SYS_ADMIN"}

        if "clone3" in names and action == "SCMP_ACT_ERRNO" and rule.get("errnoRet") == 38:
            clone3_enosys += 1

        if action != "SCMP_ACT_ALLOW":
            continue

        # mount, setns, clone3, and broad unshare remain available only to a
        # container explicitly granted CAP_SYS_ADMIN. This project grants none.
        if names.intersection({"mount", "setns", "clone3"}) and not sys_admin_only:
            fail(f"broad namespace syscall allow detected: {sorted(names)}")

        if "unshare" in names and not sys_admin_only:
            if names != {"unshare"} or one_masked_arg(rule, 0) != (
                NAMESPACE_MASK,
                CLONE_NEWUSER,
            ):
                fail("unshare is not restricted to exactly CLONE_NEWUSER")
            exact_user_unshare += 1

        if "clone" in names and not sys_admin_only:
            index = 1 if caps(rule, "includes") == set() and "s390" in rule.get(
                "includes", {}
            ).get("arches", []) else 0
            masked = one_masked_arg(rule, index)
            if masked is None or masked[0] != NAMESPACE_MASK:
                fail("clone rule does not mask every namespace flag")
            namespace_value = masked[1]
            if namespace_value == 0:
                # Moby's ordinary clone/fork compatibility rule.
                continue
            if names != {"clone"} or namespace_value not in EXPECTED_CHROMIUM_MASKS:
                fail("unexpected clone namespace combination")
            chromium_clone_masks.add(namespace_value)

        if "chroot" in names and not caps(rule, "includes"):
            if names != {"chroot"} or caps(rule, "excludes") != {"CAP_SYS_ADMIN"}:
                fail("unexpected chroot rule")
            exact_chroot += 1

    if chromium_clone_masks != EXPECTED_CHROMIUM_MASKS:
        fail("Chromium clone namespace allowlist is incomplete")
    if exact_user_unshare != 1:
        fail("expected one exact CLONE_NEWUSER unshare rule")
    if exact_chroot != 1:
        fail("expected one kernel-capability-gated chroot rule")
    if clone3_enosys != 1:
        fail("clone3 must remain forced to ENOSYS without CAP_SYS_ADMIN")


def validate_apparmor() -> None:
    profile = APPARMOR_PATH.read_text(encoding="utf-8")
    for required in (
        "profile chrome-patch-browser",
        "userns,",
        "deny mount,",
        "deny network alg,",
        "ptrace (trace,tracedby,read,readby) peer=chrome-patch-browser,",
    ):
        if required not in profile:
            fail(f"AppArmor policy is missing: {required}")
    if "flags=(unconfined)" in profile or "apparmor=unconfined" in profile:
        fail("AppArmor policy must not be unconfined")


def validate_compose_attachment() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    for required in (
        "apparmor=chrome-patch-browser",
        "seccomp=./docker/chromium-seccomp.json",
        "no-new-privileges:true",
    ):
        if required not in compose:
            fail(f"Compose does not attach required policy: {required}")


def main() -> int:
    try:
        validate_seccomp()
        validate_apparmor()
        validate_compose_attachment()
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"RUNTIME PROFILE CHECK FAILED: {exc}", file=sys.stderr)
        return 1
    print("Runtime profiles verified: exact Chromium namespaces, confined AppArmor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
