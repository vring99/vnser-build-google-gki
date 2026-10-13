import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "config/bakasu.json").read_text(encoding="utf-8"))
BASH = os.environ.get("BAKASU_TEST_BASH", "bash")
WORKFLOWS = ("build.yml", "build-make.yml") if (ROOT / ".github/workflows/build.yml").exists() else (
    "build-kernel-original-make.yml", "Build Kernel OnePlus.yml")


def jobs():
    for name in WORKFLOWS:
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
        yield next(iter(workflow["jobs"].values()))


class OfficialWorkflowTests(unittest.TestCase):
    def test_pins_and_official_integration_match_between_builders(self):
        bodies = []
        for job in jobs():
            self.assertEqual(job["env"]["BAKASU_COMMIT"], CONFIG["commit"])
            self.assertEqual(job["env"]["SUSFS_KSU_COMMIT"], CONFIG["susfs_ksu_commit"])
            runs = "\n".join(step.get("run", "") for step in job["steps"])
            for legacy in ("filter-susfs", "bakasu_selinux.py", "bakasu_35188_selinux.py", "BAKASU_TOOLS_DIR"):
                self.assertNotIn(legacy, runs)
            step = next(step for step in job["steps"] if step.get("id") == "bakasu_susfs_setup")
            bodies.append(step["run"].split('OFFICIAL_ROOT=', 1)[1])
        self.assertEqual(bodies[0], bodies[1])


@unittest.skipUnless(os.environ.get("BAKASU_TEST_SOURCE") and os.environ.get("SUSFS_TEST_SOURCE"),
                     "real BakaSU and official SUSFS sources not supplied")
class OfficialSourceTests(unittest.TestCase):
    def test_real_workflow_installs_exact_official_file_and_only_removes_build_stop(self):
        source = os.environ["BAKASU_TEST_SOURCE"]
        susfs = os.environ["SUSFS_TEST_SOURCE"]
        families = ("android12-5.10", "android13-5.15", "android14-6.1", "android15-6.6", "android16-6.12")
        run = next(step["run"] for step in next(jobs())["steps"]
                   if step.get("id") == "bakasu_susfs_setup")
        run = 'OFFICIAL_ROOT=' + run.split('OFFICIAL_ROOT=', 1)[1]
        run = run.replace("python3 -", shlex.quote(sys.executable.replace("\\", "/")) + " -")
        for family in families:
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                ksu = temp / "KernelSU"
                before = {}
                for name in CONFIG["upstream_files"]:
                    content = subprocess.check_output(["git", "-C", source, "show", f"{CONFIG['commit']}:{name}"])
                    before[name] = content
                    path = ksu / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                patch_path = temp / "susfs/kernel_patches/KernelSU/10_enable_susfs_for_ksu.patch"
                patch_path.parent.mkdir(parents=True)
                official = subprocess.check_output([
                    "git", "-C", susfs, "show",
                    f"origin/gki-{family}:kernel_patches/KernelSU/10_enable_susfs_for_ksu.patch",
                ])
                patch_path.write_bytes(official)
                env = dict(os.environ, KSU_SOURCE=ksu.as_posix(), SUSFS_SOURCE=(temp / "susfs").as_posix(),
                           RUNNER_TEMP=temp.as_posix(), SUSFS_KSU_COMMIT=CONFIG["susfs_ksu_commit"])
                result = subprocess.run([BASH, "-e", "-c", run], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                match = re.search(rb"diff --git a/kernel/feature/selinux_hide.c.*?\nindex \w+\.\.(\w+)",
                                  official, re.S)
                actual = subprocess.check_output([
                    "git", "hash-object", str(ksu / "kernel/feature/selinux_hide.c")], text=True).strip()
                self.assertTrue(actual.startswith(match[1].decode()), actual)
                changed = {name for name, content in before.items() if (ksu / name).read_bytes() != content}
                self.assertEqual(changed, {"kernel/feature/selinux_hide.c", "kernel/tools/susfs_compat.mk"})
                guard = (ksu / "kernel/tools/susfs_compat.mk").read_bytes()
                self.assertNotIn(b"$(error Please wait susfs update.)", guard)
                self.assertIn(b"KSU_COMPAT_HAS_SUSFS_FEATURE_SELINUX_HIDE", guard)
                self.assertEqual(patch_path.read_bytes(), official)


if __name__ == "__main__":
    unittest.main()
