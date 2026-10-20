"""Exercise exec.c header repair for all supported Android 16 sources."""
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
SOURCE = """#include <linux/ksm.h>
#ifdef CONFIG_KSU_SUSFS
int exec_check(void) { return susfs_is_current_proc_no_su(); }
#endif
"""


class ExecHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/kernel-a16-6-12.yml").read_text(encoding="utf-8"))
        cls.sublevels = tuple(int(entry["sub_level"]) for entry in
                              workflow["jobs"]["build-kernel-a16-6-12"]
                              ["strategy"]["matrix"]["include"])
        cls.runs = {}
        for name in BUILDERS:
            workflow = yaml.safe_load(
                (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            steps = workflow["jobs"]["build-kernel"]["steps"]
            run = next(step["run"] for step in steps
                       if "# Android 16 - 6.12" in step.get("run", ""))
            lines = run.splitlines(keepends=True)
            start = next(i + 1 for i, line in enumerate(lines)
                         if "# Android 16 - 6.12" in line)
            indent = lines[start].split("if [[", 1)[0]
            end = next(i + 1 for i in range(start + 1, len(lines))
                       if lines[i].rstrip() == indent + "fi")
            cls.runs[name] = "".join(lines[start:end])

    def repair(self, root, run, sublevel, source=SOURCE,
               android="android16", kernel="6.12"):
        common = root / "common"
        target = common / "fs/exec.c"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        for key, value in {"android_version": android, "kernel_version": kernel}.items():
            run = run.replace("${{ inputs." + key + " }}", value)
        result = subprocess.run(
            [BASH, "-e", "-c", run], cwd=common, capture_output=True, encoding="utf-8",
            env=dict(os.environ, CURRENT_SUB=str(sublevel), KERNEL_ROOT=root.as_posix()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return target.read_text(encoding="utf-8")

    def test_repairs_entire_matrix_and_preserves_later_version_coverage(self):
        for name, run in self.runs.items():
            for sublevel in (*self.sublevels, 57, 70):
                with self.subTest(builder=name, sublevel=sublevel):
                    with tempfile.TemporaryDirectory() as directory:
                        repaired = self.repair(Path(directory), run, sublevel)
                        self.assertIn(HEADER, repaired)

    def test_leaves_other_versions_and_unaffected_sources_unchanged(self):
        cases = [
            {"sublevel": 22},
            {"sublevel": 23, "android": "android15"},
            {"sublevel": 23, "kernel": "6.6"},
            {"sublevel": 23, "source": "#include <linux/ksm.h>\n"},
            {"sublevel": 23, "source": HEADER + "\n" + SOURCE},
        ]
        for name, run in self.runs.items():
            for case in cases:
                with self.subTest(builder=name, case=case):
                    with tempfile.TemporaryDirectory() as directory:
                        repaired = self.repair(Path(directory), run, **case)
                        self.assertEqual(repaired, case.get("source", SOURCE))

    def test_both_header_anchors_are_idempotent(self):
        for name, run in self.runs.items():
            for anchor in ("ksm", "dma-buf"):
                with self.subTest(builder=name, anchor=anchor):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        source = SOURCE.replace("linux/ksm.h", f"linux/{anchor}.h")
                        repaired = self.repair(root, run, 23, source)
                        self.assertEqual(repaired.count(HEADER), 1)
                        self.assertEqual(self.repair(root, run, 23, repaired), repaired)

    @unittest.skipUnless(CLANG, "Clang not supplied")
    def test_partial_patch_compiles_after_repair(self):
        for name, run in self.runs.items():
            for sublevel in (23, 38):
                with self.subTest(builder=name, sublevel=sublevel):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        include = root / "common/include"
                        (include / "linux").mkdir(parents=True)
                        (include / "linux/ksm.h").touch()
                        (include / "linux/susfs_def.h").write_text(
                            "static inline int susfs_is_current_proc_no_su(void) { return 0; }\n")
                        self.repair(root, run, sublevel)
                        command = [CLANG, "-fsyntax-only", "-std=gnu11", "-Werror",
                                   "-I", str(include), str(root / "common/fs/exec.c")]
                        for defines in ([], ["-DCONFIG_KSU_SUSFS"]):
                            result = subprocess.run(command + defines, capture_output=True,
                                                    encoding="utf-8")
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
