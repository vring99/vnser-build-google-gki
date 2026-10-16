"""Run the native preflight without allowing it to rewrite BakaSU sources."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GKI = (ROOT / ".github/workflows/build.yml").exists()
BUILDERS = ("build.yml", "build-make.yml") if GKI else ("Build Kernel OnePlus.yml", "build-kernel-original-make.yml")
BASH = os.environ.get("BAKASU_TEST_BASH", "bash")
SOURCES = ("kernel/feature/selinux_hide.c", "kernel/feature/selinux_hide_susfs.c",
           "kernel/tools/susfs_compat.mk", "kernel/manager/apk_sign.c", "kernel/manager/manager_sign.h")

class NativePreflightTests(unittest.TestCase):
    def test_preflight_preserves_source_and_rejects_local_overlay(self):
        for name in BUILDERS:
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            job = next(iter(workflow["jobs"].values()))
            run = next(s["run"] for s in job["steps"] if s.get("id") == "bakasu_susfs_setup")
            with tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                platform = workspace / "kernel_workspace/kernel_platform"
                common = platform / "common"
                (common / "scripts").mkdir(parents=True)
                config = common / "scripts/config"
                config.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$CONFIG_ARGS"\n')
                ksu = platform / "KernelSU"
                ksu.mkdir()
                for source in SOURCES:
                    path = ksu / source
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("unchanged\n")
                for args in (["init", "-q"], ["add", "."],
                             ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"]):
                    subprocess.run(["git", "-C", str(ksu), *args], check=True, capture_output=True)
                before = {s: (ksu / s).read_bytes() for s in SOURCES}
                env = dict(os.environ, GITHUB_WORKSPACE=workspace.as_posix(), KERNEL_ROOT=platform.as_posix(),
                           DEFCONFIG=(common / "gki_defconfig").as_posix(), CONFIG_ARGS=(workspace / "args").as_posix())
                subprocess.run([BASH, "-c", 'chmod +x "$KERNEL_ROOT/common/scripts/config"'], env=env, check=True)
                result = subprocess.run([BASH, "-e", "-c", run], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(before, {s: (ksu / s).read_bytes() for s in SOURCES})
                self.assertIn("--disable\nKSU_MULTI_MANAGER_SUPPORT", (workspace / "args").read_text())
                (ksu / SOURCES[0]).write_text("old overlay\n")
                result = subprocess.run([BASH, "-e", "-c", run], env=env, capture_output=True)
                self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(os.environ.get("BAKASU_PRO_TEST_SOURCE"), "private source not supplied")
    def test_private_pin_version_and_certificate(self):
        source = os.environ["BAKASU_PRO_TEST_SOURCE"]
        pin = "067d7083f516e42a165a8d2df449267cb846b55e"
        count = int(subprocess.check_output(["git", "-C", source, "rev-list", "--count", pin]))
        self.assertEqual(count + 30700, 35235)
        get = lambda rev: subprocess.check_output(["git", "-C", source, "show", rev + ":kernel/manager/manager_sign.h"])
        self.assertEqual(get(pin), get("883617cdef3ee3ffa682ef501e43b9d266ec891d"))

if __name__ == "__main__":
    unittest.main()
