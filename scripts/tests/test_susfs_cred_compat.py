"""Reproduce optional SUSFS credential helpers under -Werror."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/susfs_cred_compat.py"
GKI = (ROOT / ".github/workflows/build.yml").exists()
BUILDERS = ("build.yml", "build-make.yml") if GKI else (
    "Build Kernel OnePlus.yml", "build-kernel-original-make.yml")
COMPILER = os.environ.get("SUSFS_TEST_CC") or shutil.which("gcc") or shutil.which("clang")
STUBS = """
#define __maybe_unused __attribute__((unused))
#define NULL ((void *)0)
#define SUSFS_LOGE(...) ((void)0)
struct cred { int value; };
extern struct cred *prepare_creds(void);
extern void abort_creds(struct cred *);
extern void commit_creds(struct cred *);
extern void setup_selinux(const char *, struct cred *);
extern int susfs_is_current_ksu_domain(void);
"""
HELPERS = """
static struct cred *susfs_switch_to_ksu_domain(void) {
    return prepare_creds();
}
static void susfs_restore_creds(struct cred *orig_cred) {
    if (orig_cred)
        commit_creds(orig_cred);
}
"""
CALLERS = """
#ifdef CONFIG_KSU_SUSFS_PRIORITY_UID_FILTER
void exercise_helpers(void) {
    struct cred *orig = susfs_switch_to_ksu_domain();
    susfs_restore_creds(orig);
}
#endif
"""


class SusfsCredCompatTests(unittest.TestCase):
    def apply(self, root):
        return subprocess.run([sys.executable, str(SCRIPT), str(root)],
                              capture_output=True, text=True, encoding="utf-8")

    def test_only_marks_two_helpers_and_preserves_bytes_on_repeat(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=newline), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "fs/susfs.c"
                target.parent.mkdir()
                original = ("/* fixture */\n" + HELPERS + CALLERS).replace("\n", newline).encode()
                target.write_bytes(original)
                result = self.apply(root)
                self.assertEqual(result.returncode, 0, result.stderr)
                repaired = target.read_bytes()
                expected = original.replace(b"static struct cred *susfs_switch_to_ksu_domain(",
                                             b"static __maybe_unused struct cred *susfs_switch_to_ksu_domain(")
                expected = expected.replace(b"static void susfs_restore_creds(",
                                            b"static __maybe_unused void susfs_restore_creds(")
                self.assertEqual(repaired, expected)
                self.assertEqual(self.apply(root).returncode, 0)
                self.assertEqual(target.read_bytes(), expected)

    def test_legacy_without_helpers_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(self.apply(root).returncode, 0)
            target = root / "fs/susfs.c"
            target.parent.mkdir()
            target.write_bytes(b"/* legacy SUSFS */\r\n")
            self.assertEqual(self.apply(root).returncode, 0)
            self.assertEqual(target.read_bytes(), b"/* legacy SUSFS */\r\n")

    def test_drift_or_duplicate_definitions_fail_without_partial_edits(self):
        for source in (HELPERS + HELPERS,
                       HELPERS.replace("struct cred *orig_cred", "const struct cred *orig_cred")):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "fs/susfs.c"
                target.parent.mkdir()
                target.write_text(source, encoding="utf-8")
                before = target.read_bytes()
                self.assertNotEqual(self.apply(root).returncode, 0)
                self.assertEqual(target.read_bytes(), before)

    @unittest.skipUnless(COMPILER, "host C compiler required")
    def test_disabled_feature_fails_before_and_both_modes_compile_after(self):
        self.compile_helpers(HELPERS)

    @unittest.skipUnless(COMPILER and os.environ.get("SUSFS_COMPAT_TEST_REPO"),
                         "official SUSFS checkout and compiler required")
    def test_current_official_source_helpers_across_five_families(self):
        repo = os.environ["SUSFS_COMPAT_TEST_REPO"]
        for family in ("android12-5.10", "android13-5.15", "android14-6.1",
                       "android15-6.6", "android16-6.12"):
            with self.subTest(family=family):
                source = subprocess.check_output(
                    ["git", "-C", repo, "show",
                     f"origin/gki-{family}:kernel_patches/fs/susfs.c"],
                    text=True, encoding="utf-8")
                helpers = []
                for name in ("susfs_switch_to_ksu_domain", "susfs_restore_creds"):
                    match = re.search(r"^static [^\n]*\b" + name + r"\([^;]*?\)\s*\{",
                                      source, re.M)
                    self.assertIsNotNone(match)
                    end, depth = match.end(), 1
                    while depth:
                        depth += (source[end] == "{") - (source[end] == "}")
                        end += 1
                    helpers.append(source[match.start():end])
                self.compile_helpers("\n".join(helpers))

    def compile_helpers(self, helpers):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "fs/susfs.c"
            target.parent.mkdir()
            target.write_text(STUBS + helpers + CALLERS, encoding="utf-8")
            command = [COMPILER, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                       "-c", str(target), "-o", str(root / "susfs.o")]
            before = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(before.returncode, 0, before.stdout + before.stderr)
            self.assertIn("unused-function", before.stderr)
            self.assertEqual(self.apply(root).returncode, 0)
            for defines in ([], ["-DCONFIG_KSU_SUSFS_PRIORITY_UID_FILTER"]):
                result = subprocess.run(command + defines, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_builder_applies_repair_after_susfs_copy_before_build(self):
        for name in BUILDERS:
            with self.subTest(builder=name):
                workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
                steps = next(iter(workflow["jobs"].values()))["steps"]
                repair_index = next(i for i, s in enumerate(steps) if s.get("id") == "susfs_cred_compat")
                repair = steps[repair_index]
                self.assertIn("scripts/susfs_cred_compat.py", repair["run"])
                copies = [i for i, s in enumerate(steps) if "kernel_patches/fs/*" in s.get("run", "")]
                self.assertTrue(copies)
                self.assertGreater(repair_index, max(copies))
                builds = [i for i, s in enumerate(steps) if s.get("name") in (
                    "编译内核", "Build Kernel FAST", "Build Kernel Repo Toolchain Make")]
                self.assertTrue(builds)
                self.assertLess(repair_index, min(builds))
                if GKI:
                    self.assertEqual(repair["if"], "inputs.enable_susfs")


if __name__ == "__main__":
    unittest.main()
