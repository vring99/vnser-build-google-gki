"""Contracts for the private BakaSU Pro build integration."""
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github/workflows"
GKI = (WF / "build.yml").exists()
BUILDERS = ("build.yml", "build-make.yml") if GKI else ("Build Kernel OnePlus.yml", "build-kernel-original-make.yml")
INPUT = "bakasu_version" if GKI else "BAKASU_VERSION"
PIN = "b89b9d960ec788460a7a6e4289f869762f306957"
KERNEL_PIN = "0f0eb9bca8d8d48e7dfee8c393efa14d8d5bb45b"
BASH = os.environ.get("BAKASU_TEST_BASH", "bash")

def read(name):
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))

class ProWorkflowTests(unittest.TestCase):
    def test_all_defaults_and_choices(self):
        for path in WF.glob("*.yml"):
            if path.name == "kernel-redmi-k40.yml":
                continue  # The independent legacy build keeps its own Pro pin.
            workflow = read(path.name)
            events = workflow.get("on", workflow.get(True, {}))
            for event in ("workflow_call", "workflow_dispatch"):
                schema = (events.get(event) or {}).get("inputs", {}).get(INPUT)
                if schema:
                    self.assertEqual(schema["default"], "35275", path.name)
                    if schema["type"] == "choice":
                        self.assertEqual(schema["options"], ["35275"])
            for job in workflow.get("jobs", {}).values():
                selected = job.get("with", {}).get(INPUT)
                if selected:
                    self.assertIn(selected, ("35275", "${{ inputs." + INPUT + " }}",
                                             "${{ inputs." + INPUT + " || '35275' }}"))

    def test_builders_use_native_source_without_overwriting_it(self):
        for name in BUILDERS:
            job = next(iter(read(name)["jobs"].values()))
            runs = "\n".join(s.get("run", "") for s in job["steps"])
            self.assertIn("raw.githubusercontent.com/vring99/BakaSU/$BAKASU_COMMIT/kernel/setup.sh", runs)
            self.assertIn(KERNEL_PIN, runs)
            self.assertNotIn("SUSFS_KSU_COMMIT", job.get("env", {}))
            step = next(s for s in job["steps"] if s.get("id") == "bakasu_susfs_setup")
            for forbidden in ("curl ", "cp ", "git apply", "path.write", "Please wait susfs update"):
                self.assertNotIn(forbidden, step["run"])
            self.assertIn("kernel/Kbuild", step["run"])
            self.assertIn("selinux_hide.c", step["run"])
            self.assertIn("selinux_hide.h", step["run"])
            self.assertIn("preinit/preinit.c", step["run"])
            self.assertIn("preinit/preinit.h", step["run"])
            self.assertIn("--disable KSU_MULTI_MANAGER_SUPPORT", step["run"])
            if GKI:
                self.assertEqual(step["if"], "inputs.ksu_variant == 'BakaSU Pro'")

    def test_selector_executes_and_rejects_old_versions(self):
        for name in BUILDERS:
            job = next(iter(read(name)["jobs"].values()))
            selector = next(s for s in job["steps"] if s.get("id") == "bakasu_version")
            for version in ("35275", "35274", "35273", "35270", "35268", "35240", "35239", "35238", "35237", "35236", "35235", "35226", "35227", "35266", ""):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "env"
                    path.touch()
                    result = subprocess.run([BASH, "-e", "-c", selector["run"]], capture_output=True,
                        env=dict(os.environ, BAKASU_VERSION=version, GITHUB_ENV=path.as_posix()))
                    self.assertEqual(result.returncode == 0, version == "35275", result.stderr)
                    if version == "35275":
                        self.assertEqual(path.read_text().strip(), "BAKASU_COMMIT=" + KERNEL_PIN)

    @unittest.skipUnless(GKI, "GKI manager workflow only")
    def test_manager_uses_private_signed_artifact(self):
        job = read("get-manager.yml")["jobs"]["get_ksu_manager"]
        self.assertEqual(job["env"]["BAKASU_MANAGER_COMMIT"], PIN)
        self.assertEqual(job["env"]["BAKASU_MANAGER_VERSION"], "35275")
        runs = "\n".join(s.get("run", "") for s in job["steps"])
        self.assertIn('REPO="vring99/BakaSU"', runs)
        self.assertIn('KSUM="BakaSU-Pro-release"', runs)
        self.assertIn("build-private-manager.yml", runs)
        self.assertIn("head_sha=$BAKASU_MANAGER_COMMIT", runs)
        self.assertNotIn("Baka-SU/BakaSU", runs)

    def test_fixed_native_kernel_version_ignores_commit_count(self):
        for name in BUILDERS:
            job = next(iter(read(name)["jobs"].values()))
            runs = "\n".join(s.get("run", "") for s in job["steps"])
            block = re.search(
                r'(?m)^[ \t]*KSU_VERSION=\$\(sed [^\n]*\)\n'
                r'[ \t]*if \[ "\$KSU_VERSION" != "\$BAKASU_VERSION" \]; then\n'
                r'(?:[^\n]*\n)*?[ \t]*fi', runs)
            self.assertIsNotNone(block, name)
            fixtures = ("KSU_VERSION := 35275\n", "KSU_VERSION := 35278\n",
                        "", "KSU_VERSION := $(shell expr 30000 + 4578 + 700)\n",
                        "KSU_VERSION := 35275\nKSU_VERSION := 35278\n")
            for source in fixtures:
                with self.subTest(builder=name, source=source), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / "kernel").mkdir()
                    (root / "kernel/Kbuild").write_text(source)
                    script = 'git() { echo "unexpected commit-count query" >&2; return 91; }\n'
                    result = subprocess.run(
                        [BASH, "-e", "-c", script + block[0]], cwd=root,
                        env=dict(os.environ, BAKASU_VERSION="35275"),
                        capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0 if source == fixtures[0] else 1,
                                     result.stdout + result.stderr)

    def test_all_family_matrix_preserved(self):
        name = "kernel-bakasu-35226.yml" if GKI else "Build Kernel OnePlus-35226.yml"
        jobs = read(name)["jobs"]
        self.assertEqual(len(jobs), 6 if GKI else 2)
        for job in jobs.values():
            self.assertEqual(job["with"][INPUT], "35275")
            if GKI and job["uses"].endswith(("kernel-a15-6-6.yml", "kernel-a16-6-12.yml")):
                self.assertIs(job["with"]["use_zram"], False)
            if GKI and job["uses"].endswith("kernel-a12-5-10.yml"):
                self.assertIs(job["with"]["onlyAk3"], False)

    def test_version_validation_and_artifact_identity(self):
        for name in BUILDERS:
            job = next(iter(read(name)["jobs"].values()))
            runs = "\n".join(s.get("run", "") for s in job["steps"])
            self.assertIn('if [ "$KSU_VERSION" != "$BAKASU_VERSION" ]; then', runs)
            self.assertIn('echo "KSUVER=$KSU_VERSION"', runs)
            uploads = [s["with"]["name"] for s in job["steps"]
                       if s.get("uses", "").startswith("actions/upload-artifact@")]
            self.assertTrue(any("${{ env.KSUVER }}" in name for name in uploads))
        manager = read("get-manager.yml")["jobs"]["get_ksu_manager"]
        version = next(s["run"] for s in manager["steps"] if s.get("id") == "get_version")
        self.assertIn(PIN, version)
        self.assertIn('"$KSU_VERSION" != "$BAKASU_MANAGER_VERSION"', version)
        main_runs = "\n".join(s.get("run", "") for job in read("main.yml")["jobs"].values()
                              for s in job.get("steps", []))
        self.assertIn("35275) KSU_REF=" + PIN, main_runs)

    def test_manager_rejects_kernel_version_mismatch(self):
        job = read("get-manager.yml")["jobs"]["get_ksu_manager"]
        run = next(s["run"] for s in job["steps"] if s.get("id") == "get_version")
        run = run.replace("${{ inputs.kernelsu_variant }}", "BakaSU Pro")
        stubs = '''
git() {
    case "$1" in
        clone) mkdir KernelSU ;;
        -C) return 0 ;;
        rev-list) printf '%s\\n' "$TEST_COUNT" ;;
        *) return 92 ;;
    esac
}
curl() { printf 'version=v2.4\\n'; }
'''
        for count, manager_version, expected in (("4575", "35275", 0),
                                                  ("4574", "35275", 1),
                                                  ("4575", "35274", 1)):
            with self.subTest(count=count, manager=manager_version), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = dict(os.environ, BAKASU_VERSION="35275", TEST_COUNT=count,
                           BAKASU_MANAGER_VERSION=manager_version,
                           GITHUB_ENV=(root / "env").as_posix(),
                           GITHUB_OUTPUT=(root / "output").as_posix())
                result = subprocess.run([BASH, "-e", "-c", stubs + run], cwd=root,
                                        env=env, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if expected == 0:
                    self.assertIn("kversion=35275", (root / "output").read_text())
                else:
                    self.assertIn("kernel/manager version does not match", result.stdout)

if __name__ == "__main__":
    unittest.main()
