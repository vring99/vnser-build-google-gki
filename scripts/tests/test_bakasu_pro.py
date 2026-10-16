"""Contracts for the private BakaSU Pro build integration."""
import os
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
PIN = "7797d5ab79fc20577086729304fb50f6bc20f748"
BASH = os.environ.get("BAKASU_TEST_BASH", "bash")

def read(name):
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))

class ProWorkflowTests(unittest.TestCase):
    def test_all_defaults_and_choices(self):
        for path in WF.glob("*.yml"):
            workflow = read(path.name)
            events = workflow.get("on", workflow.get(True, {}))
            for event in ("workflow_call", "workflow_dispatch"):
                schema = (events.get(event) or {}).get("inputs", {}).get(INPUT)
                if schema:
                    self.assertEqual(schema["default"], "35233", path.name)
                    if schema["type"] == "choice":
                        self.assertEqual(schema["options"], ["35233"])
            for job in workflow.get("jobs", {}).values():
                selected = job.get("with", {}).get(INPUT)
                if selected:
                    self.assertIn(selected, ("35233", "${{ inputs." + INPUT + " }}",
                                             "${{ inputs." + INPUT + " || '35233' }}"))

    def test_builders_use_native_source_without_overwriting_it(self):
        for name in BUILDERS:
            job = next(iter(read(name)["jobs"].values()))
            runs = "\n".join(s.get("run", "") for s in job["steps"])
            self.assertIn("raw.githubusercontent.com/vring99/BakaSU/$BAKASU_COMMIT/kernel/setup.sh", runs)
            self.assertIn(PIN, runs)
            self.assertNotIn("SUSFS_KSU_COMMIT", job.get("env", {}))
            step = next(s for s in job["steps"] if s.get("id") == "bakasu_susfs_setup")
            for forbidden in ("curl ", "cp ", "git apply", "path.write", "Please wait susfs update"):
                self.assertNotIn(forbidden, step["run"])
            self.assertIn("selinux_hide_susfs.c", step["run"])
            self.assertIn("--disable KSU_MULTI_MANAGER_SUPPORT", step["run"])
            if GKI:
                self.assertEqual(step["if"], "inputs.ksu_variant == 'BakaSU'")

    def test_selector_executes_and_rejects_old_versions(self):
        for name in BUILDERS:
            job = next(iter(read(name)["jobs"].values()))
            selector = next(s for s in job["steps"] if s.get("id") == "bakasu_version")
            for version in ("35233", "35226", "35227", "35266", ""):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "env"
                    path.touch()
                    result = subprocess.run([BASH, "-e", "-c", selector["run"]], capture_output=True,
                        env=dict(os.environ, BAKASU_VERSION=version, GITHUB_ENV=path.as_posix()))
                    self.assertEqual(result.returncode == 0, version == "35233", result.stderr)
                    if version == "35233":
                        self.assertEqual(path.read_text().strip(), "BAKASU_COMMIT=" + PIN)

    @unittest.skipUnless(GKI, "GKI manager workflow only")
    def test_manager_uses_private_signed_artifact(self):
        job = read("get-manager.yml")["jobs"]["get_ksu_manager"]
        self.assertEqual(job["env"]["BAKASU_MANAGER_COMMIT"], "883617cdef3ee3ffa682ef501e43b9d266ec891d")
        runs = "\n".join(s.get("run", "") for s in job["steps"])
        self.assertIn('REPO="vring99/BakaSU"', runs)
        self.assertIn('KSUM="BakaSU-Pro-release"', runs)
        self.assertIn("build-private-manager.yml", runs)
        self.assertIn("head_sha=$BAKASU_MANAGER_COMMIT", runs)
        self.assertNotIn("Baka-SU/BakaSU", runs)

    def test_all_family_matrix_preserved(self):
        name = "kernel-bakasu-35226.yml" if GKI else "Build Kernel OnePlus-35226.yml"
        jobs = read(name)["jobs"]
        self.assertEqual(len(jobs), 5 if GKI else 2)
        for job in jobs.values():
            self.assertEqual(job["with"][INPUT], "35233")
            if GKI and job["uses"].endswith(("kernel-a15-6-6.yml", "kernel-a16-6-12.yml")):
                self.assertIs(job["with"]["use_zram"], False)
            if GKI and job["uses"].endswith("kernel-a12-5-10.yml"):
                self.assertIs(job["with"]["onlyAk3"], False)

if __name__ == "__main__":
    unittest.main()
