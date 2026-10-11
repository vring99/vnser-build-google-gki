#!/usr/bin/env python3
"""Backport the reviewed SELinux fixes without advancing ReSukiSU 35188."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/resukisu-35188-selinux.json"
PATCH = ROOT / "other_patch/resukisu-35188/selinux-fixes.patch"
LEGACY_PATHS = ("security/selinux/hooks.c", "security/selinux/selinuxfs.c")


def load_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def digest(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def git(directory, *args, check=True):
    return subprocess.run(["git", "-C", str(directory), *args], check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8")


def verify_base(ksu, config):
    actual = git(ksu, "rev-parse", "HEAD").stdout.strip()
    if actual != config["base_commit"]:
        raise ValueError(f"Expected ReSukiSU 35188 ({config['base_commit']}), got {actual}")


def verify_backport(ksu, config):
    verify_base(ksu, config)
    for name, expected in config["patched_files"].items():
        if digest((ksu / name).read_bytes()) != expected:
            raise ValueError(f"Backport is missing or modified: {name}")


def apply_backport(ksu, config):
    verify_base(ksu, config)
    if digest(PATCH.read_bytes()) != config["patch_sha256"]:
        raise ValueError("SELinux backport patch checksum mismatch")
    reverse = git(ksu, "apply", "--reverse", "--check", str(PATCH), check=False)
    if reverse.returncode != 0:
        git(ksu, "apply", "--check", str(PATCH))
        git(ksu, "apply", str(PATCH))
    verify_backport(ksu, config)
    print("ReSukiSU 35188: SID table and SELinux timing fixes applied; HEAD unchanged")


def filter_patch(data, expected):
    """Remove only the two reviewed SELinux diffs, preserving all other bytes."""
    if set(expected) != set(LEGACY_PATHS):
        raise ValueError("Invalid SELinux block manifest")
    headers = list(re.finditer(rb"^diff --git ([^\r\n]+)\r?$", data, re.MULTILINE))
    kept = []
    found = set()
    cursor = 0
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(data)
        names = header[1].decode("utf-8").split()
        touched = [name for name in LEGACY_PATHS if f"a/{name}" in names or f"b/{name}" in names]
        if not touched:
            continue
        if len(touched) != 1 or names != [f"a/{touched[0]}", f"b/{touched[0]}"]:
            raise ValueError("Unexpected SELinux diff header")
        name = touched[0]
        if name in found:
            raise ValueError(f"Duplicate SELinux diff: {name}")
        if digest(data[header.start():end]) != expected[name]:
            raise ValueError(f"Unreviewed SUSFS SELinux changes in {name}; review before building")
        found.add(name)
        kept.append(data[cursor:header.start()])
        cursor = end
    if found != set(LEGACY_PATHS):
        raise ValueError("Missing reviewed SUSFS SELinux blocks; refusing to silently skip integration")
    kept.append(data[cursor:])
    return b"".join(kept)


def verify_kernel(common, family):
    version = family.split("-", 1)[1].split(".")
    makefile = (common / "Makefile").read_text(encoding="utf-8")
    for key, expected in zip(("VERSION", "PATCHLEVEL"), version):
        match = re.search(rf"^{key}\s*=\s*(\d+)\s*$", makefile, re.MULTILINE)
        if not match or match[1] != expected:
            raise ValueError(f"Kernel Makefile does not match {family}")
    sources = {}
    for name in (*LEGACY_PATHS, "security/selinux/ss/services.c"):
        path = common / name
        if path.with_suffix(path.suffix + ".rej").exists():
            raise ValueError(f"Unresolved SELinux patch rejection: {name}")
        text = path.read_text(encoding="utf-8")
        for marker in ("ksu_selinux_hide", "fake_state", "my_setprocattr", "my_write_context",
                       "my_write_access", "my_sel_open_handle_status", "selinux_hidden_"):
            if marker in text:
                raise ValueError(f"Conflicting SELinux implementation in {name}: {marker}")
        sources[name] = text
    targets = {
        LEGACY_PATHS[0]: [r"LSM_HOOK_INIT\(\s*setprocattr\s*,\s*selinux_setprocattr\s*\)"],
        LEGACY_PATHS[1]: [
            r"\bwrite_op\[\]",
            r"\[SEL_CONTEXT\]\s*=\s*sel_write_context\b",
            r"\[SEL_ACCESS\]\s*=\s*sel_write_access\b",
            r"\bsel_handle_status_ops\s*=\s*\{[^}]*\.open\s*=\s*sel_open_handle_status\b",
        ],
    }
    for name, patterns in targets.items():
        for pattern in patterns:
            if len(re.findall(pattern, sources[name])) != 1:
                raise ValueError(f"Missing or ambiguous SELinux hook target in {name}: {pattern}")


def main():
    config = load_config()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--ksu", required=True, type=Path)
    prepare = commands.add_parser("filter-susfs")
    prepare.add_argument("--family", required=True, choices=config["families"])
    prepare.add_argument("--patch", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--family", required=True, choices=config["families"])
    verify.add_argument("--ksu", required=True, type=Path)
    verify.add_argument("--common", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "apply":
            apply_backport(args.ksu, config)
        elif args.command == "filter-susfs":
            info = config["families"][args.family]
            result = filter_patch(args.patch.read_bytes(), info["legacy_blocks"])
            args.output.write_bytes(result)
            print(f"{args.family}: using backported ReSukiSU SELinux hooks; other SUSFS diffs preserved")
        else:
            verify_backport(args.ksu, config)
            verify_kernel(args.common, args.family)
            print(f"{args.family}: ReSukiSU 35188 SELinux backport preflight passed")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        print(f"::error::{detail.strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
