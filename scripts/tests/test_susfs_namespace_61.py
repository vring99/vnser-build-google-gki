"""Exercise Android 14/6.1 namespace recovery in both kernel builders."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
GKI = (ROOT / ".github/workflows/build.yml").exists()
BUILDERS = ("build.yml", "build-make.yml") if GKI else (
    "Build Kernel OnePlus.yml", "build-kernel-original-make.yml")
BASH = os.environ.get("BAKASU_TEST_BASH", "bash")
CLANG = os.environ.get("SUSFS_TEST_CLANG") or shutil.which("clang")
HEADER = "#include <linux/susfs_def.h>"
DECLARATIONS = (
    "extern bool susfs_is_current_ksu_domain(void);",
    "extern struct static_key_true susfs_is_sdcard_android_data_not_decrypted;",
    "#define CL_COPY_MNT_NS BIT(25)",
)
SOURCE = """#include <linux/mnt_idmapping.h>
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


class Namespace61Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {}
        for name in BUILDERS:
            workflow = yaml.safe_load(
                (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            steps = next(iter(workflow["jobs"].values()))["steps"]
            run = next(step["run"] for step in steps
                       if "# Android 14 - 6.1" in step.get("run", ""))
            cls.runs[name] = run[run.index("# Android 14 - 6.1"):
                                 run.index("# Android 15 - 6.6")]

    def repair(self, root, builder, source=SOURCE, sublevel=115,
               android="android14", kernel="6.1"):
        target = root / "fs/namespace.c"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        (root / "fs/proc").mkdir(exist_ok=True)
        (root / "fs/proc/base.c").write_text("#include <linux/cpufreq_times.h>\n")
        (root / "fs/proc/task_mmu.c").touch()
        patches = root / "patches"
        for folder in ("wild/archived", "wild"):
            patch = patches / folder / "susfs_fix_patches/vfixture/a14-6.1/base.c.patch"
            patch.parent.mkdir(parents=True, exist_ok=True)
            patch.touch()
        run = self.runs[builder]
        for key, value in {"android_version": android, "kernel_version": kernel,
                           "os_patch_level": "2024-07"}.items():
            run = run.replace("${{ inputs." + key + " }}", value)
        result = subprocess.run(
            [BASH, "-e", "-c", run], cwd=root, capture_output=True, encoding="utf-8",
            env=dict(os.environ, GKI_V=android + "-" + kernel,
                     CURRENT_SUB=str(sublevel), SUSFS_VERSION="fixture",
                     KERNEL_PATCHES=patches.as_posix()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return target.read_text(encoding="utf-8")

    def assert_complete(self, source):
        self.assertEqual(source.count(HEADER), 1)
        for declaration in DECLARATIONS:
            self.assertEqual(source.count(declaration), 1)
            self.assertLess(source.index(declaration), source.index("int mount_check"))
        self.assertNotIn("\\n", source)
        self.assertEqual(source[source.index("int mount_check"):],
                         SOURCE[SOURCE.index("int mount_check"):])

    def test_recovers_declarations_across_61_sublevels(self):
        for builder in BUILDERS:
            for sublevel in (68, 75, 115, 128, 134, 141, 145, 162):
                with self.subTest(builder=builder, sublevel=sublevel), tempfile.TemporaryDirectory() as directory:
                    self.assert_complete(self.repair(Path(directory), builder, sublevel=sublevel))

    def test_existing_header_and_individual_declarations_are_preserved(self):
        complete = HEADER + "\n" + "\n".join(DECLARATIONS) + "\n" + SOURCE
        for builder in BUILDERS:
            for source in (HEADER + "\n" + SOURCE, complete,
                           *(complete.replace(item + "\n", "") for item in DECLARATIONS)):
                with self.subTest(builder=builder, source=source), tempfile.TemporaryDirectory() as directory:
                    self.assert_complete(self.repair(Path(directory), builder, source))

    def test_both_include_anchors_and_repeated_runs(self):
        for builder in BUILDERS:
            for source in (SOURCE, SOURCE.replace("#include <linux/mnt_idmapping.h>\n", "")):
                with self.subTest(builder=builder, source=source), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    repaired = self.repair(root, builder, source)
                    self.assert_complete(repaired)
                    self.assertEqual(self.repair(root, builder, repaired), repaired)

    def test_other_kernel_families_and_unpatched_sources_are_unchanged(self):
        for builder in BUILDERS:
            for android, kernel, source in (("android13", "5.15", SOURCE),
                                             ("android15", "6.6", SOURCE),
                                             ("android14", "6.1", '#include "internal.h"\n')):
                with self.subTest(builder=builder, android=android, kernel=kernel), tempfile.TemporaryDirectory() as directory:
                    self.assertEqual(self.repair(Path(directory), builder, source,
                                                 android=android, kernel=kernel), source)

    @unittest.skipUnless(CLANG, "Clang not supplied")
    def test_reported_namespace_symbols_compile_after_repair(self):
        for builder in BUILDERS:
            with self.subTest(builder=builder), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                include = root / "include"
                (include / "linux").mkdir(parents=True)
                (include / "linux/mnt_idmapping.h").touch()
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
                self.repair(root, builder)
                for defines in ([], enabled[:1], enabled):
                    after = subprocess.run(command + defines, capture_output=True, text=True)
                    self.assertEqual(after.returncode, 0, after.stdout + after.stderr)


if __name__ == "__main__":
    unittest.main()
