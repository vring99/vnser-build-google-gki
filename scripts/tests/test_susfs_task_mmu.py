"""Exercise the workflow repair after a partially applied SUSFS patch."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
BUILDERS = ("build.yml", "build-make.yml")
BASH = os.environ.get("BAKASU_TEST_BASH", "bash")
CLANG = os.environ.get("SUSFS_TEST_CLANG") or shutil.which("clang")
HEADER = "#include <linux/susfs_def.h>"
SOURCE = """#include <linux/sched/mm.h>
#ifdef CONFIG_KSU_SUSFS_OPEN_REDIRECT
int redirect(int inode) { return SUSFS_IS_INODE_OPEN_REDIRECT(inode); }
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_MAP
int mapped(int inode) { return SUSFS_IS_INODE_SUS_MAP(inode); }
#endif
"""


class TaskMmuHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/kernel-a13-5-15.yml").read_text(encoding="utf-8"))
        cls.sublevels = tuple(int(entry["sub_level"]) for entry in
                              workflow["jobs"]["build-kernels-a13-5-15"]
                              ["strategy"]["matrix"]["include"])
        cls.runs = {}
        for name in BUILDERS:
            workflow = yaml.safe_load(
                (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            steps = workflow["jobs"]["build-kernel"]["steps"]
            run = next(step["run"] for step in steps
                       if "# Android 13 - 5.15" in step.get("run", ""))
            cls.runs[name] = run[run.index("# Android 13 - 5.15"):
                                 run.index("# Android 14 - 6.1")]

    def repair(self, root, run, sublevel, source=SOURCE,
               android="android13", kernel="5.15"):
        target = root / "fs/proc/task_mmu.c"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        (root / "fs/namespace.c").touch()
        for key, value in {"android_version": android, "kernel_version": kernel,
                           "os_patch_level": "2024-07"}.items():
            run = run.replace("${{ inputs." + key + " }}", value)
        result = subprocess.run(
            [BASH, "-e", "-c", run], cwd=root, capture_output=True, encoding="utf-8",
            env=dict(os.environ, CURRENT_SUB=str(sublevel)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return target.read_text(encoding="utf-8")

    def test_repairs_supported_sublevels_in_both_builders(self):
        for name, run in self.runs.items():
            for sublevel in self.sublevels:
                with self.subTest(builder=name, sublevel=sublevel):
                    with tempfile.TemporaryDirectory() as directory:
                        repaired = self.repair(Path(directory), run, sublevel)
                        self.assertIn(HEADER, repaired)

    def test_leaves_other_versions_and_unaffected_sources_unchanged(self):
        cases = [
            {"sublevel": 73}, {"sublevel": 207},
            {"sublevel": 149, "android": "android12"},
            {"sublevel": 149, "kernel": "6.1"},
            {"sublevel": 149, "source": "#include <linux/sched/mm.h>\n"},
            {"sublevel": 149, "source": HEADER + "\n" + SOURCE},
        ]
        for name, run in self.runs.items():
            for case in cases:
                with self.subTest(builder=name, case=case):
                    with tempfile.TemporaryDirectory() as directory:
                        repaired = self.repair(Path(directory), run, **case)
                        self.assertEqual(repaired, case.get("source", SOURCE))

    def test_both_header_anchors_are_idempotent(self):
        for name, run in self.runs.items():
            for anchor in ("mm", "sysctl"):
                with self.subTest(builder=name, anchor=anchor):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        source = SOURCE.replace("sched/mm.h", f"sched/{anchor}.h")
                        repaired = self.repair(root, run, 149, source)
                        self.assertEqual(repaired.count(HEADER), 1)
                        self.assertEqual(self.repair(root, run, 149, repaired), repaired)

    @unittest.skipUnless(CLANG, "Clang not supplied")
    def test_partial_patch_compiles_after_repair(self):
        for name, run in self.runs.items():
            for sublevel in (74, 149):
                with self.subTest(builder=name, sublevel=sublevel):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        include = root / "include"
                        (include / "linux/sched").mkdir(parents=True)
                        (include / "linux/sched/mm.h").touch()
                        (include / "linux/susfs_def.h").write_text(
                            "#define SUSFS_IS_INODE_OPEN_REDIRECT(inode) (inode)\n"
                            "#define SUSFS_IS_INODE_SUS_MAP(inode) (inode)\n")
                        self.repair(root, run, sublevel)
                        command = [CLANG, "-fsyntax-only", "-std=gnu11", "-Werror",
                                   "-I", str(include), str(root / "fs/proc/task_mmu.c")]
                        for defines in ([], ["CONFIG_KSU_SUSFS_SUS_MAP"],
                                        ["CONFIG_KSU_SUSFS_OPEN_REDIRECT"],
                                        ["CONFIG_KSU_SUSFS_SUS_MAP", "CONFIG_KSU_SUSFS_OPEN_REDIRECT"]):
                            result = subprocess.run(command + ["-D" + d for d in defines],
                                                    capture_output=True, encoding="utf-8")
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
