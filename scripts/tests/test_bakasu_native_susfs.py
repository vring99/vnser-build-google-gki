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
SOURCES = ("kernel/Kbuild", "kernel/feature/selinux_hide.c", "kernel/feature/selinux_hide.h",
           "kernel/preinit/preinit.c", "kernel/preinit/preinit.h",
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
                for source in SOURCES:
                    with self.subTest(builder=name, source=source):
                        (ksu / source).write_text("old overlay\n")
                        result = subprocess.run([BASH, "-e", "-c", run], env=env, capture_output=True)
                        self.assertNotEqual(result.returncode, 0)
                        (ksu / source).write_bytes(before[source])

    @unittest.skipUnless(os.environ.get("BAKASU_PRO_TEST_SOURCE"), "private source not supplied")
    def test_private_pin_version_and_certificate(self):
        source = os.environ["BAKASU_PRO_TEST_SOURCE"]
        pin = "eb576bf3c3787519c193865169b672ad39895431"
        for path in SOURCES:
            subprocess.run(["git", "-C", source, "cat-file", "-e", pin + ":" + path], check=True)
        count = int(subprocess.check_output(["git", "-C", source, "rev-list", "--count", pin]))
        self.assertEqual(count + 30700, 35270)
        get = lambda rev: subprocess.check_output(["git", "-C", source, "show", rev + ":kernel/manager/manager_sign.h"])
        self.assertEqual(get(pin), get("883617cdef3ee3ffa682ef501e43b9d266ec891d"))


    @unittest.skipUnless(os.environ.get("BAKASU_PRO_TEST_SOURCE"), "private source not supplied")
    def test_setup_uses_private_repo_with_official_default_setup(self):
        source = os.environ["BAKASU_PRO_TEST_SOURCE"]
        setup = subprocess.check_output([
            "git", "-C", source, "show",
            "eb576bf3c3787519c193865169b672ad39895431:kernel/setup.sh",
        ])
        for name in BUILDERS:
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            job = next(iter(workflow["jobs"].values()))
            run = next(s["run"] for s in job["steps"]
                       if 'bash "$RUNNER_TEMP/bakasu-setup.sh"' in s.get("run", ""))
            run = run.replace("${{ inputs.ksu_variant }}", "BakaSU Pro")
            with self.subTest(builder=name), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                remote = workspace / "private-remote"
                (remote / "kernel").mkdir(parents=True)
                (remote / "kernel/setup.sh").write_bytes(setup)
                for args in (["init", "-q", "-b", "codex/build-35233"],
                             ["-c", "core.autocrlf=false", "add", "."],
                             ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                              "commit", "-qm", "private fixture"]):
                    subprocess.run(["git", "-C", str(remote), *args], check=True, capture_output=True)
                pin = subprocess.check_output(["git", "-C", str(remote), "rev-parse", "HEAD"],
                                              text=True).strip()
                platform = workspace / "kernel_workspace/kernel_platform"
                drivers = platform / "common/drivers"
                drivers.mkdir(parents=True)
                (drivers / "Makefile").write_text("# fixture\n")
                (drivers / "Kconfig").write_text('menu "Drivers"\nendmenu\n')
                configs = platform / "common/arch/arm64/configs"
                configs.mkdir(parents=True)
                (configs / "gki_defconfig").touch()
                temp = workspace / "runner-temp"
                temp.mkdir()
                env = dict(os.environ, BAKASU_VERSION="30701", BAKASU_COMMIT=pin,
                           GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.autocrlf",
                           GIT_CONFIG_VALUE_0="false",
                           RUNNER_TEMP=temp.as_posix(), TEST_REMOTE=remote.as_posix(),
                           GITHUB_ENV=(workspace / "env").as_posix(),
                           GITHUB_OUTPUT=(workspace / "output").as_posix())
                # Redirect only the external downloads; execute the real setup and Git operations.
                downloads = r'''
git() {
    if [ "$1" = clone ]; then
        local arg args=() private=false
        for arg in "$@"; do
            case "$arg" in
                https://github.com/vring99/BakaSU|https://github.com/vring99/BakaSU.git)
                    args+=("$TEST_REMOTE"); private=true ;;
                https://*) echo "unexpected repository: $arg" >&2; return 89 ;;
                *) args+=("$arg") ;;
            esac
        done
        "$private" || return 90
        command git "${args[@]}"
    else
        command git "$@"
    fi
}
curl() {
    case "$*" in
        *"https://raw.githubusercontent.com/vring99/BakaSU/$BAKASU_COMMIT/kernel/setup.sh"*)
            command cp "$TEST_REMOTE/kernel/setup.sh" "$RUNNER_TEMP/bakasu-setup.sh" ;;
        *) return 91 ;;
    esac
}
export -f git curl
'''
                result = subprocess.run([BASH, "-e", "-c", downloads + run],
                                        cwd=platform if GKI else workspace, env=env,
                                        capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                ksu = platform / "KernelSU"
                actual = subprocess.check_output(["git", "-C", str(ksu), "rev-parse", "HEAD"],
                                                 text=True).strip()
                self.assertEqual(actual, pin)
                self.assertEqual((ksu / "kernel/setup.sh").read_bytes(), setup)
                self.assertIn("KSUVER=30701", (workspace / "env").read_text())
                self.assertIn("kernelsu/", (drivers / "Makefile").read_text())

if __name__ == "__main__":
    unittest.main()
