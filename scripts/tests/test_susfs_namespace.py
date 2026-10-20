"""Run the namespace repair against partial SUSFS patches and version boundaries."""
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
DECLARATIONS = (
    "extern bool susfs_is_current_ksu_domain(void);",
    "extern struct static_key_true susfs_is_sdcard_android_data_not_decrypted;",
    "#define CL_COPY_MNT_NS BIT(25)",
)
SOURCE = """#include <linux/mnt_idmapping.h>
#include "pnode.h"
#include "internal.h"

#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
int mount_check(unsigned int flags)
{
    return susfs_is_current_ksu_domain() &&
        static_branch_unlikely(&susfs_is_sdcard_android_data_not_decrypted) &&
        (flags & (VFSMOUNT_MNT_FLAGS_KSU_UNSHARED_MNT | DEFAULT_KSU_MNT_ID |
                  DEFAULT_KSU_MNT_GROUP_ID | CL_COPY_MNT_NS)) &&
        susfs_is_current_proc_umounted();
}
#endif
"""


class NamespaceHeaderTests(unittest.TestCase):
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
        target = root / "fs/namespace.c"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        (root / "fs/proc").mkdir(exist_ok=True)
        (root / "fs/proc/task_mmu.c").touch()
        (root / "mm").mkdir(exist_ok=True)
        (root / "mm/mmap.c").touch()
        for key, value in {"android_version": android, "kernel_version": kernel,
                           "os_patch_level": "2024-07"}.items():
            run = run.replace("${{ inputs." + key + " }}", value)
        result = subprocess.run(
            [BASH, "-e", "-c", run], cwd=root, capture_output=True, encoding="utf-8",
            env=dict(os.environ, CURRENT_SUB=str(sublevel)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return target.read_text(encoding="utf-8")

    def assert_complete(self, source):
        self.assertEqual(source.count(HEADER), 1)
        for declaration in DECLARATIONS:
            self.assertEqual(source.count(declaration), 1)
            self.assertLess(source.index(declaration), source.index("int mount_check"))
        self.assertNotIn("\\n", source)

    def test_recovers_missing_declaration_block_for_entire_supported_matrix(self):
        for name, run in self.runs.items():
            for sublevel in self.sublevels:
                with self.subTest(builder=name, sublevel=sublevel):
                    with tempfile.TemporaryDirectory() as directory:
                        self.assert_complete(self.repair(Path(directory), run, sublevel))

    def test_recovers_declarations_even_when_header_is_already_present(self):
        for name, run in self.runs.items():
            with self.subTest(builder=name):
                with tempfile.TemporaryDirectory() as directory:
                    self.assert_complete(self.repair(
                        Path(directory), run, 123, HEADER + "\n" + SOURCE))

    def test_each_missing_declaration_is_recovered_without_duplicates(self):
        complete = HEADER + "\n" + "\n".join(DECLARATIONS) + "\n" + SOURCE
        for name, run in self.runs.items():
            for missing in DECLARATIONS:
                with self.subTest(builder=name, missing=missing):
                    with tempfile.TemporaryDirectory() as directory:
                        self.assert_complete(self.repair(
                            Path(directory), run, 123, complete.replace(missing + "\n", "")))

    def test_both_header_anchors_and_repeated_runs(self):
        for name, run in self.runs.items():
            for source in (SOURCE, SOURCE.replace("#include <linux/mnt_idmapping.h>\n", "")):
                with self.subTest(builder=name, source=source):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        repaired = self.repair(root, run, 123, source)
                        self.assert_complete(repaired)
                        self.assertEqual(self.repair(root, run, 123, repaired), repaired)

    def test_other_versions_and_sources_without_susfs_are_unchanged(self):
        cases = [
            {"sublevel": 73}, {"sublevel": 207},
            {"sublevel": 123, "android": "android12"},
            {"sublevel": 123, "kernel": "6.1"},
            {"sublevel": 123, "source": '#include "internal.h"\n'},
        ]
        for name, run in self.runs.items():
            for case in cases:
                with self.subTest(builder=name, case=case):
                    with tempfile.TemporaryDirectory() as directory:
                        repaired = self.repair(Path(directory), run, **case)
                        self.assertEqual(repaired, case.get("source", SOURCE))

    @unittest.skipUnless(CLANG, "Clang not supplied")
    def test_partial_patch_fails_to_compile_before_repair_and_compiles_after(self):
        for name, run in self.runs.items():
            for sublevel in (123, 197):
                with self.subTest(builder=name, sublevel=sublevel):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        include = root / "include"
                        (include / "linux").mkdir(parents=True)
                        (include / "linux/mnt_idmapping.h").touch()
                        (include / "pnode.h").touch()
                        (include / "internal.h").write_text(
                            "typedef _Bool bool;\n"
                            "struct static_key_true { int enabled; };\n"
                            "#define BIT(n) (1U << (n))\n"
                            "#define static_branch_unlikely(key) ((key)->enabled)\n")
                        (include / "linux/susfs_def.h").write_text(
                            "#define VFSMOUNT_MNT_FLAGS_KSU_UNSHARED_MNT 0x80000000\n"
                            "#define DEFAULT_KSU_MNT_ID 2000000000\n"
                            "#define DEFAULT_KSU_MNT_GROUP_ID 200000\n"
                            "static inline int susfs_is_current_proc_umounted(void) { return 0; }\n")
                        target = root / "fs/namespace.c"
                        target.parent.mkdir()
                        target.write_text(SOURCE, encoding="utf-8")
                        command = [CLANG, "-fsyntax-only", "-std=gnu11", "-Werror",
                                   "-I", str(include), str(target)]
                        enabled = ["-DCONFIG_KSU_SUSFS", "-DCONFIG_KSU_SUSFS_SUS_MOUNT"]
                        before = subprocess.run(command + enabled, capture_output=True, text=True)
                        self.assertNotEqual(before.returncode, 0)
                        self.assertIn("undeclared", before.stderr)
                        self.repair(root, run, sublevel)
                        for defines in ([], enabled[:1], enabled):
                            after = subprocess.run(command + defines, capture_output=True, text=True)
                            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)

    @unittest.skipUnless(os.environ.get("SUSFS_NAMESPACE_MATRIX_ROOT"),
                         "official source audit not supplied")
    def test_repairs_real_partially_patched_official_sources(self):
        audit_root = Path(os.environ["SUSFS_NAMESPACE_MATRIX_ROOT"])
        for name, run in self.runs.items():
            for sublevel in self.sublevels:
                with self.subTest(builder=name, sublevel=sublevel):
                    source = (audit_root / str(sublevel) / "fs/namespace.c").read_text(encoding="utf-8")
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        repaired = self.repair(root, run, sublevel, source)
                        self.assertEqual(repaired.count(HEADER), 1)
                        for declaration in DECLARATIONS:
                            self.assertEqual(repaired.count(declaration), 1)
                        self.assertNotIn("\\n#ifdef", repaired)
                        marker = "/* Maximum number of mounts in a mount namespace */"
                        self.assertEqual(source[source.index(marker):],
                                         repaired[repaired.index(marker):])
                        self.assertEqual(self.repair(root, run, sublevel, repaired), repaired)


if __name__ == "__main__":
    unittest.main()
