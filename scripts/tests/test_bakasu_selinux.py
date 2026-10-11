import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import bakasu_selinux as upstream


@unittest.skipUnless(os.environ.get("BAKASU_TEST_SOURCE"), "real BakaSU source not supplied")
class UpstreamIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ksu = Path(self.temp.name) / "KernelSU"
        self.config = upstream.load_config()
        source = Path(os.environ["BAKASU_TEST_SOURCE"]).resolve()
        for name in (*self.config["upstream_files"], "kernel/Kbuild", "kernel/Kconfig"):
            path = self.ksu / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(subprocess.check_output([
                "git", "-C", str(source), "show", f"{self.config['commit']}:{name}"]))
        git_dir = subprocess.check_output([
            "git", "-C", str(source), "rev-parse", "--absolute-git-dir"], text=True).strip()
        self.environment = patch.dict(os.environ, {"GIT_DIR": git_dir, "GIT_WORK_TREE": str(self.ksu)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.common = Path(self.temp.name) / "common"
        (self.common / "security/selinux/ss").mkdir(parents=True)
        (self.common / "Makefile").write_text("VERSION = 5\nPATCHLEVEL = 10\n")
        (self.common / upstream.integration.LEGACY_PATHS[0]).write_text(
            "LSM_HOOK_INIT(setprocattr, selinux_setprocattr),\n")
        (self.common / upstream.integration.LEGACY_PATHS[1]).write_text(
            "static ssize_t (*write_op[])(struct file *, char *, size_t) = {\n"
            "[SEL_CONTEXT] = sel_write_context,\n[SEL_ACCESS] = sel_write_access,\n};\n"
            "static const struct file_operations sel_handle_status_ops = {\n"
            ".open = sel_open_handle_status,\n};\n")
        (self.common / "security/selinux/ss/services.c").write_text("/* stock services */\n")

    def snapshot(self):
        return {path.relative_to(self.ksu).as_posix(): path.read_bytes()
                for path in self.ksu.rglob("*") if path.is_file()}

    def test_only_suspend_notice_removed_after_validation_and_idempotent(self):
        before = self.snapshot()
        upstream.verify_upstream(self.ksu, self.config)
        self.assertEqual(before, self.snapshot())
        for _ in range(2):
            upstream.integrate(self.ksu, self.common, "android12-5.10", True, self.config)
        expected = dict(before)
        expected[upstream.SUSFS_COMPAT] = before[upstream.SUSFS_COMPAT][:-len(upstream.SUSFS_STOP)]
        self.assertEqual(expected, self.snapshot())

    def test_without_susfs_does_not_modify_upstream(self):
        before = self.snapshot()
        upstream.integrate(self.ksu, self.common, "android12-5.10", False, self.config)
        self.assertEqual(before, self.snapshot())

    def test_conflicting_hook_keeps_build_stop(self):
        before = self.snapshot()
        (self.common / upstream.integration.LEGACY_PATHS[0]).write_text("my_setprocattr\n")
        with self.assertRaises(ValueError):
            upstream.integrate(self.ksu, self.common, "android12-5.10", True, self.config)
        self.assertEqual(before, self.snapshot())

    def test_modified_upstream_rejected(self):
        path = self.ksu / "kernel/feature/selinux_hide.c"
        path.write_bytes(path.read_bytes() + b"\n/* changed */\n")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            upstream.integrate(self.ksu, self.common, "android12-5.10", True, self.config)
        self.assertEqual(before, self.snapshot())

    def test_modified_suspension_guard_rejected(self):
        path = self.ksu / upstream.SUSFS_COMPAT
        path.write_bytes(path.read_bytes().replace(b"Please wait susfs update.", b"New upstream restriction."))
        with self.assertRaises(ValueError):
            upstream.verify_upstream(self.ksu, self.config)

    def test_wrong_revision_rejected(self):
        with self.assertRaises(ValueError):
            upstream.verify_upstream(self.ksu, {**self.config, "commit": "0" * 40})

    @unittest.skipUnless(os.environ.get("AOSP_SELINUX_TEST_SOURCE"), "real kernel fixtures not supplied")
    def test_all_five_real_kernel_entrypoints(self):
        source = Path(os.environ["AOSP_SELINUX_TEST_SOURCE"])
        before = self.snapshot()
        for family in upstream.integration.load_config()["families"]:
            with self.subTest(family=family):
                target = Path(self.temp.name) / family
                shutil.copytree(source / family, target)
                upstream.integrate(self.ksu, target, family, True, self.config)
        expected = dict(before)
        expected[upstream.SUSFS_COMPAT] = before[upstream.SUSFS_COMPAT][:-len(upstream.SUSFS_STOP)]
        self.assertEqual(expected, self.snapshot())


if __name__ == "__main__":
    unittest.main()
