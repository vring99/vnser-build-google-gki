"""Android 17 uses its own official manifest/Kleaf, with SUSFS optional."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github/workflows"
BASH = os.environ.get("BAKASU_TEST_BASH", "bash")
BUILDERS = ("build.yml", "build-make.yml")
PAIR = "inputs.android_version == 'android17' && inputs.kernel_version == '6.18'"


def read(name):
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def steps(name):
    return read(name)["jobs"]["build-kernel"]["steps"]


class Android17WorkflowTests(unittest.TestCase):
    def test_native_sync_uses_official_month_and_rejects_version_drift(self):
        script = ROOT / "scripts/sync_android17.sh"
        self.assertTrue(script.exists())
        for sublevel, sync_status, expected in (("32", "0", 0), ("31", "0", 1), ("32", "42", 42)):
            with self.subTest(sublevel=sublevel, sync_status=sync_status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "common/bazel").mkdir(parents=True)
                (root / "common/Makefile").write_text("VERSION = 6\nPATCHLEVEL = 18\nSUBLEVEL = 32\n")
                (root / "common/bazel/constants.scl").touch()
                (root / "tools").mkdir()
                (root / "tools/bazel").write_text("#!/bin/sh\nexit 0\n")
                repo = root / "repo"
                repo.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> "$KERNEL_ROOT/args"\n'
                                'if [[ "$1" == sync ]]; then exit "$SYNC_STATUS"; fi\n')
                env = dict(os.environ, KERNEL_ROOT=root.as_posix(), REPO=repo.as_posix(),
                           ANDROID17_PATCH_LEVEL="2026-09", ANDROID17_SUBLEVEL=sublevel,
                           ANDROID17_SOURCE_REVISION="", SYNC_STATUS=sync_status,
                           PYTHON=Path(sys.executable).as_posix())
                subprocess.run([BASH, "-c", 'chmod +x "$REPO" "$KERNEL_ROOT/tools/bazel"'], env=env, check=True)
                result = subprocess.run([BASH, script.as_posix()], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                args = (root / "args").read_text()
                self.assertIn("common-android17-6.18-2026-09", args)
                self.assertNotIn("--repo-rev=v2.16", args)
                if expected == 1:
                    self.assertIn("expected 6.18.31, got 6.18.32", result.stderr)

    def test_future_susfs_enable_fails_clearly_until_official_branch_exists(self):
        for name in BUILDERS:
            step = next(s for s in steps(name) if s.get("id") == "clone_susfs")
            run = step["run"].replace("${{ inputs.android_version }}", "android17").replace(
                "${{ inputs.kernel_version }}", "6.18").replace("${{ inputs.ksu_variant }}", "BakaSU Pro")
            result = subprocess.run([BASH, "-e", "-c", 'git() { return 2; };\n' + run],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Official gki-android17-6.18 is unavailable", result.stdout)

    def test_family_has_verified_versions_and_future_susfs_switch(self):
        path = WF / "kernel-a17-6-18.yml"
        self.assertTrue(path.exists(), "Android 17 family workflow is missing")
        workflow = read(path.name)
        events = workflow.get("on", workflow.get(True))
        for event in ("workflow_dispatch", "workflow_call"):
            inputs = events[event]["inputs"]
            self.assertIs(inputs["cancel_susfs"]["default"], True)
            self.assertIs(inputs["use_uni"]["default"], True)
            self.assertIn("onlyAk3", inputs)
        job = workflow["jobs"]["build-kernel-a17-6-18"]
        self.assertEqual(job["strategy"]["matrix"]["include"], [
            {"sub_level": "20", "os_patch_level": "2026-04"},
            {"sub_level": "21", "os_patch_level": "2026-06"},
            {"sub_level": "32", "os_patch_level": "2026-09"},
        ])
        self.assertEqual(job["with"]["enable_susfs"], "${{ !inputs.cancel_susfs }}")
        self.assertEqual(job["with"]["use_uni"], "${{ inputs.use_uni }}")
        for flag in ("use_zram", "use_kpm", "use_bbg", "supp_op"):
            self.assertIs(job["with"][flag], False)

    def test_all_pro_families_include_android17_without_susfs(self):
        jobs = read("kernel-bakasu-35226.yml")["jobs"]
        self.assertIn("build-a17-6-18", jobs)
        job = jobs["build-a17-6-18"]
        self.assertTrue(job["uses"].endswith("kernel-a17-6-18.yml"))
        self.assertIs(job["with"]["cancel_susfs"], True)
        self.assertIs(read("kernel-a17-6-18.yml")["jobs"]["build-kernel-a17-6-18"]["with"]["use_zram"], False)

    def test_both_builders_skip_susfs_dependencies_when_disabled(self):
        for name in BUILDERS:
            with self.subTest(builder=name):
                clone = next(s for s in steps(name) if s.get("id") == "clone_susfs")
                self.assertEqual(clone["if"], "inputs.enable_susfs")
                self.assertIn('gki-${{ inputs.android_version }}-${{ inputs.kernel_version }}', clone["run"])
                deps = next(s for s in steps(name) if s.get("name") == "克隆依赖仓库")
                self.assertNotIn("susfs4ksu", deps["run"])
                trace = next(s for s in steps(name) if s.get("id") == "bakasu_tracepoint_setup")
                self.assertEqual(trace["if"], "inputs.ksu_variant == 'BakaSU Pro' && !inputs.enable_susfs")
                self.assertIn("--disable KSU_SUSFS", trace["run"])
                self.assertIn("--disable KSU_MANUAL_HOOK", trace["run"])
                self.assertIn("--enable KSU_TRACEPOINT_HOOK", trace["run"])

    def test_both_builders_use_native_android17_steps_and_package_boot(self):
        for name in BUILDERS:
            with self.subTest(builder=name):
                selected = steps(name)
                init = next(s for s in selected if s.get("id") == "android17_sync")
                build = next(s for s in selected if s.get("id") == "android17_build")
                self.assertEqual(init["if"], PAIR)
                self.assertEqual(build["if"], PAIR)
                self.assertIn("sync_android17.sh", init["run"])
                self.assertIn("build_android17.sh", build["with"]["command"])
                legacy_sync = next(s for s in selected if s.get("name") == "初始化并同步内核源码")
                legacy_build = next(s for s in selected if s.get("name") == "编译内核")
                self.assertEqual(legacy_sync["if"], "!(" + PAIR + ")")
                self.assertEqual(legacy_build["if"], "!(" + PAIR + ")")
                boot = next(s for s in selected if s.get("name") == "构建 Boot 镜像 (Android 13+)")
                self.assertIn("'android17'", boot["if"])
                self.assertIn("boot-gz.img", boot["run"])

    def test_native_build_uses_fragment_and_stages_image_without_old_toolchain(self):
        script = ROOT / "scripts/build_android17.sh"
        self.assertTrue(script.exists(), "Native Android 17 build script is missing")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "common/arch/arm64/configs").mkdir(parents=True)
            config = root / "common/arch/arm64/configs/gki_defconfig"
            orig = 'CONFIG_MODULES=y\n# CONFIG_KSU is not set\nCONFIG_LOCALVERSION=""\n'
            config.with_suffix(".orig").write_text(orig)
            config.write_text('CONFIG_MODULES=y\nCONFIG_KSU=y\nCONFIG_KSU_TRACEPOINT_HOOK=y\n'
                              '# CONFIG_KSU_SUSFS is not set\nCONFIG_LOCALVERSION="-custom"\n')
            (root / "tools").mkdir()
            bazel = root / "tools/bazel"
            bazel.write_text('#!/bin/bash\nset -eu\nprintf "%s\\n" "$@" > args\n'
                             'mkdir -p bazel-bin/common/kernel_aarch64\n'
                             'printf "Linux version 6.18.32" > bazel-bin/common/kernel_aarch64/Image\n'
                             'mkdir -p bazel-bin/common/kernel_aarch64_config/out_dir\n'
                             'cp resolved.config bazel-bin/common/kernel_aarch64_config/out_dir/.config\n')
            resolved = root / "resolved.config"
            resolved.write_text('CONFIG_KSU=y\nCONFIG_KALLSYMS=y\nCONFIG_KALLSYMS_ALL=y\n'
                                '# CONFIG_KSU_MULTI_MANAGER_SUPPORT is not set\n'
                                'CONFIG_KSU_TRACEPOINT_HOOK=y\nCONFIG_HAVE_SYSCALL_TRACEPOINTS=y\n'
                                'CONFIG_TRACEPOINTS=y\n# CONFIG_KSU_SUSFS is not set\n'
                                '# CONFIG_KSU_MANUAL_HOOK is not set\n')
            env = dict(os.environ, KERNEL_ROOT=root.as_posix(), DEFCONFIG=config.as_posix(),
                       CCACHE_DIR=(root / "cache").as_posix(), PYTHON=Path(sys.executable).as_posix())
            subprocess.run([BASH, "-c", 'chmod +x "$KERNEL_ROOT/tools/bazel"'], check=True, env=env)
            result = subprocess.run([BASH, script.as_posix()], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(config.read_text(), orig)
            fragment = (config.parent / "ksu.fragment").read_text()
            self.assertIn("CONFIG_KSU=y\n", fragment)
            self.assertIn('# CONFIG_KSU_SUSFS is not set\n', fragment)
            self.assertIn('CONFIG_LOCALVERSION="-custom"\n', fragment)
            self.assertNotIn("CONFIG_MODULES", fragment)
            args = (root / "args").read_text().splitlines()
            for flag in ("--lto=thin", "--notrim", "--nokmi_symbol_list_strict_mode",
                         "--defconfig_fragment=//common:arch/arm64/configs/ksu.fragment",
                         "//common:kernel_aarch64"):
                self.assertIn(flag, args)
            self.assertEqual((root / "common/out/arch/arm64/boot/Image").read_text(),
                             "Linux version 6.18.32")
            retry = subprocess.run([BASH, script.as_posix()], env=env, capture_output=True, text=True)
            self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
            self.assertEqual((config.parent / "ksu.fragment").read_text(), fragment)
            resolved.write_text(resolved.read_text().replace("CONFIG_KSU=y\n", "CONFIG_KSU=m\n"))
            wrong = subprocess.run([BASH, script.as_posix()], env=env, capture_output=True, text=True)
            self.assertNotEqual(wrong.returncode, 0)
            self.assertIn("resolved config is missing: CONFIG_KSU=y", wrong.stdout)
            source = script.read_text()
            self.assertNotIn("cctv18", source)
            self.assertNotIn("-Wno-error", source)


if __name__ == "__main__":
    unittest.main()
