#!/usr/bin/env python3
"""Validate the reviewed BakaSU release and integrate its upstream SELinux fixes."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

import resukisu_35188_selinux as integration


CONFIG = integration.ROOT / "config/bakasu.json"
SUSFS_COMPAT = "kernel/tools/susfs_compat.mk"
SUSFS_STOP = (
    b"\n$(info -- $(REPO_NAME): susfs has NOT update with latest KernelSU source)\n"
    b"$(info -- $(REPO_NAME): Please wait simonpunk update)\n"
    b"$(error Please wait susfs update.)"
)


def load_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def verify_upstream(ksu, config):
    actual = integration.git(ksu, "rev-parse", "HEAD").stdout.strip()
    if actual != config["commit"]:
        raise ValueError(f"Expected BakaSU {config['commit']}, got {actual}")
    for name, expected in config["upstream_files"].items():
        data = (ksu / name).read_bytes().replace(b"\r\n", b"\n")
        # Accept our single reviewed change, but reject any other source edits.
        if name == SUSFS_COMPAT and not data.endswith(SUSFS_STOP):
            data += SUSFS_STOP
        if integration.digest(data) != expected:
            raise ValueError(f"BakaSU source is missing or modified: {name}")


def integrate(ksu, common, family, susfs, config):
    verify_upstream(ksu, config)
    integration.verify_kernel(common, family)
    if susfs:
        path = ksu / SUSFS_COMPAT
        data = path.read_bytes().replace(b"\r\n", b"\n")
        if data.endswith(SUSFS_STOP):
            path.write_bytes(data[:-len(SUSFS_STOP)])
        print("SUSFS legacy SELinux hooks absent; enabling reviewed BakaSU integration")
    verify_upstream(ksu, config)
    print(f"{family}: BakaSU {config['version_code']} SELinux preflight passed")


def main():
    config = load_config()
    families = integration.load_config()["families"]
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-source")
    check.add_argument("--ksu", required=True, type=Path)
    prepare = commands.add_parser("filter-susfs")
    prepare.add_argument("--family", required=True, choices=families)
    prepare.add_argument("--patch", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    finish = commands.add_parser("integrate")
    finish.add_argument("--family", required=True, choices=families)
    finish.add_argument("--ksu", required=True, type=Path)
    finish.add_argument("--common", required=True, type=Path)
    finish.add_argument("--susfs", required=True, choices=("true", "false"))
    args = parser.parse_args()
    try:
        if args.command == "check-source":
            verify_upstream(args.ksu, config)
            print(f"BakaSU {config['version_code']}: upstream SELinux fixes present; no backport needed")
        elif args.command == "filter-susfs":
            result = integration.filter_patch(args.patch.read_bytes(), families[args.family]["legacy_blocks"])
            args.output.write_bytes(result)
            print(f"{args.family}: using upstream BakaSU SELinux hooks; other SUSFS diffs preserved")
        else:
            integrate(args.ksu, args.common, args.family, args.susfs == "true", config)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        print(f"::error::{detail.strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
