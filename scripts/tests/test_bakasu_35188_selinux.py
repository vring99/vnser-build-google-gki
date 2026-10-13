import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import bakasu_35188_selinux as backport
import bakasu_selinux as upstream


def block(path, body):
    return (f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+{body}\n").encode()


class PatchFilterTests(unittest.TestCase):
    def setUp(self):
        self.hooks = block(backport.LEGACY_PATHS[0], "manual hooks")
        self.selinuxfs = block(backport.LEGACY_PATHS[1], "manual selinuxfs")
        self.avc = block("security/selinux/avc.c", "keep avc")
        self.fs = block("fs/susfs.c", "keep filesystem")
        self.expected = {
            path: hashlib.sha256(content).hexdigest()
            for path, content in zip(backport.LEGACY_PATHS, (self.hooks, self.selinuxfs))
        }
        self.source = b"patch preamble\n" + self.avc + self.hooks + self.fs + self.selinuxfs

    def test_only_reviewed_manual_hook_blocks_removed(self):
        self.assertEqual(backport.filter_patch(self.source, self.expected),
                         b"patch preamble\n" + self.avc + self.fs)

    def test_crlf_preserves_retained_bytes(self):
        source = self.source.replace(b"\n", b"\r\n")
        self.assertEqual(backport.filter_patch(source, self.expected),
                         (b"patch preamble\n" + self.avc + self.fs).replace(b"\n", b"\r\n"))

    def test_unknown_or_missing_or_duplicate_block_rejected(self):
        for content in (self.source.replace(b"manual hooks", b"new hook behavior"),
                        self.source.replace(self.hooks, b""), self.source + self.hooks):
            with self.subTest(content=content), self.assertRaises(ValueError):
                backport.filter_patch(content, self.expected)

    def test_renamed_target_rejected(self):
        source = self.source.replace(b"b/security/selinux/hooks.c", b"b/security/selinux/new.c")
        with self.assertRaises(ValueError):
            backport.filter_patch(source, self.expected)


class SourceValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.common = Path(self.temp.name)
        (self.common / "security/selinux/ss").mkdir(parents=True)
        (self.common / "Makefile").write_text("VERSION = 5\nPATCHLEVEL = 10\n")
        (self.common / backport.LEGACY_PATHS[0]).write_text(
            "LSM_HOOK_INIT(setprocattr, selinux_setprocattr),\n")
        (self.common / backport.LEGACY_PATHS[1]).write_text(
            "static ssize_t (*write_op[])(struct file *, char *, size_t) = {\n"
            "[SEL_CONTEXT] = sel_write_context,\n[SEL_ACCESS] = sel_write_access,\n};\n"
            "static const struct file_operations sel_handle_status_ops = {\n"
            ".open = sel_open_handle_status,\n};\n")
        (self.common / "security/selinux/ss/services.c").write_text("/* stock services */\n")

    def test_stock_targets_accepted(self):
        backport.verify_kernel(self.common, "android12-5.10")

    def test_wrong_family_rejected(self):
        with self.assertRaises(ValueError):
            backport.verify_kernel(self.common, "android15-6.6")

    def test_old_entrypoint_rejected(self):
        path = self.common / backport.LEGACY_PATHS[0]
        path.write_text(path.read_text().replace("selinux_setprocattr", "my_setprocattr"))
        with self.assertRaises(ValueError):
            backport.verify_kernel(self.common, "android12-5.10")

    def test_conflicting_policy_patch_rejected(self):
        path = self.common / "security/selinux/ss/services.c"
        path.write_text("static struct selinux_policy *selinux_hidden_backup_policy(void);\n")
        with self.assertRaises(ValueError):
            backport.verify_kernel(self.common, "android12-5.10")

    def test_reject_file_is_not_ignored(self):
        (self.common / (backport.LEGACY_PATHS[1] + ".rej")).write_text("rejected hunk")
        with self.assertRaises(ValueError):
            backport.verify_kernel(self.common, "android12-5.10")


@unittest.skipUnless(os.environ.get("BAKASU_35188_TEST_SOURCE"), "real 35188 source not supplied")
class RealBackportTests(unittest.TestCase):
    def test_exact_base_patch_idempotence_and_version_preservation(self):
        source = Path(os.environ["BAKASU_35188_TEST_SOURCE"]).resolve()
        config = backport.load_config()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            paths = [*config["patched_files"], "kernel/Kbuild", "kernel/Kconfig"]
            for name in paths:
                content = subprocess.check_output([
                    "git", "-C", str(source), "show", f"{config['base_commit']}:{name}"])
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            original = {name: (target / name).read_bytes() for name in paths[-2:]}
            git_dir = subprocess.check_output([
                "git", "-C", str(source), "rev-parse", "--absolute-git-dir"], text=True).strip()
            # git apply writes only the temporary work tree; the fixture's index and refs stay intact.
            with patch.dict(os.environ, {"GIT_DIR": git_dir, "GIT_WORK_TREE": str(target)}):
                before = backport.git(target, "rev-parse", "HEAD").stdout
                backport.apply_backport(target, config)
                backport.apply_backport(target, config)
                backport.verify_backport(target, config)
                self.assertEqual(before, backport.git(target, "rev-parse", "HEAD").stdout)
                for name, content in original.items():
                    self.assertEqual((target / name).read_bytes(), content)
                with (target / "kernel/feature/selinux_hide.c").open("ab") as stream:
                    stream.write(b"\n/* conflicting modification */\n")
                with self.assertRaises(ValueError):
                    backport.verify_backport(target, config)


@unittest.skipUnless(os.environ.get("SUSFS_TEST_SOURCE"), "real SUSFS sources not supplied")
class RealSusfsTests(unittest.TestCase):
    def test_all_five_families_preserve_every_other_block(self):
        config = backport.load_config()
        source = os.environ["SUSFS_TEST_SOURCE"]
        self.assertEqual(len(config["families"]), 5)
        for family, info in config["families"].items():
            with self.subTest(family=family):
                data = subprocess.check_output([
                    "git", "-C", source, "show",
                    f"{info['susfs_commit']}:kernel_patches/50_add_susfs_in_gki-{family}.patch"])
                result = backport.filter_patch(data, info["legacy_blocks"])
                # Independent diff-section split: compare every retained section verbatim.
                chunks = data.split(b"diff --git ")
                wanted = [chunks[0]]
                for chunk in chunks[1:]:
                    header = chunk.splitlines()[0]
                    if not any(header.startswith(f"a/{name} b/{name}".encode())
                               for name in backport.LEGACY_PATHS):
                        wanted.append(b"diff --git " + chunk)
                self.assertEqual(result, b"".join(wanted))
                self.assertIn(b"diff --git a/security/selinux/avc.c", result)


@unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML not installed")
class WorkflowTests(unittest.TestCase):
    def test_both_workflows_use_official_susfs_for_bakasu_only(self):
        import yaml
        selected = []
        for name in ("build.yml", "build-make.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            for job in workflow["jobs"].values():
                self.assertEqual(job["env"]["BAKASU_COMMIT"], upstream.load_config()["commit"])
            steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
            added = [step for step in steps if step.get("id") == "bakasu_susfs_setup"]
            self.assertEqual(len(added), 1)
            for step in added:
                self.assertEqual(step["if"], "inputs.ksu_variant == 'BakaSU'")
            self.assertIn("--enable KALLSYMS_ALL", added[0]["run"])
            self.assertIn('if [ "${{ inputs.enable_susfs }}" != "true" ]; then', added[0]["run"])
            self.assertIn("--include=kernel/feature/selinux_hide.c", added[0]["run"])
            self.assertNotIn("|| true", "\n".join(step["run"] for step in added))
            self.assertNotIn("bakasu_35188_selinux.py", "\n".join(step.get("run", "") for step in steps))
            setup = next(step["run"] for step in steps if step["name"] == "添加 KernelSU")
            self.assertIn("raw.githubusercontent.com/Baka-SU/BakaSU/$BAKASU_COMMIT/kernel/setup.sh", setup)
            susfs = next(step["run"] for step in steps if step["name"] == "应用 SUSFS 补丁")
            self.assertNotIn("filter-susfs", susfs)
            self.assertNotIn(".bakasu", susfs)
            self.assertIn('patch -p1 < "$SUSFS_PATCH"', susfs)
            self.assertLess(next(i for i, s in enumerate(steps) if s["name"] == "应用 SUSFS 补丁"), steps.index(added[0]))
            self.assertLess(steps.index(added[0]), next(i for i, s in enumerate(steps) if s["name"] == "编译内核"))
            selected.append(added)
        self.assertEqual(selected[0], selected[1])


if __name__ == "__main__":
    unittest.main()
