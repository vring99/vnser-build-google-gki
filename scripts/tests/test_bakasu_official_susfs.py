import json
from itertools import product
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
VERSION_INPUT = "bakasu_version" if WORKFLOWS[0] == "build.yml" else "BAKASU_VERSION"
BUILDS = {
    "35227": CONFIG["commit"],
    "35226": "59afa46b325126b12a48234063cd0c2a6b735b57",
}


def jobs():
    for name in WORKFLOWS:
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
        yield next(iter(workflow["jobs"].values()))


class OfficialWorkflowTests(unittest.TestCase):
    def test_pins_and_official_integration_match_between_builders(self):
        bodies = []
        for job in jobs():
            self.assertEqual(job["env"]["BAKASU_VERSION"], "${{ inputs." + VERSION_INPUT + " }}")
            self.assertEqual(job["env"]["SUSFS_KSU_COMMIT"], CONFIG["susfs_ksu_commit"])
            runs = "\n".join(step.get("run", "") for step in job["steps"])
            for legacy in ("filter-susfs", "bakasu_selinux.py", "bakasu_35188_selinux.py", "BAKASU_TOOLS_DIR"):
                self.assertNotIn(legacy, runs)
            step = next(step for step in job["steps"] if step.get("id") == "bakasu_susfs_setup")
            bodies.append(step["run"].split('OFFICIAL_ROOT=', 1)[1])
        self.assertEqual(bodies[0], bodies[1])

    def test_existing_callers_default_to_35226(self):
        for name in WORKFLOWS:
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
            inputs = workflow.get("on", workflow.get(True))["workflow_call"]["inputs"]
            self.assertEqual(inputs[VERSION_INPUT]["default"], "35226")
            self.assertFalse(inputs[VERSION_INPUT]["required"])

    def test_selectors_resolve_pins_and_reject_unknown_versions(self):
        for job in jobs():
            selector = next((step for step in job["steps"] if step.get("id") == "bakasu_version"), None)
            self.assertIsNotNone(selector, "missing version selector")
            self.assertEqual(job["steps"][0], selector)
            if VERSION_INPUT == "bakasu_version":
                self.assertEqual(selector["if"], "inputs.ksu_variant == 'BakaSU'")
            for version in (*BUILDS, "35266", ""):
                with self.subTest(builder=job.get("name"), version=version), tempfile.TemporaryDirectory() as directory:
                    env_path = Path(directory) / "github-env"
                    env_path.touch()
                    result = subprocess.run([BASH, "-e", "-c", selector["run"]], capture_output=True,
                                            env=dict(os.environ, BAKASU_VERSION=version,
                                                     GITHUB_ENV=env_path.as_posix()))
                    if version in BUILDS:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(env_path.read_text().strip(), "BAKASU_COMMIT=" + BUILDS[version])
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(env_path.read_bytes(), b"")

    def test_full_35226_entrypoint_reuses_all_formal_matrices(self):
        if VERSION_INPUT == "bakasu_version":
            name = "kernel-bakasu-35226.yml"
            removed = "kernel-test-a12-5-10-236-bakasu-35226.yml"
            families = ("a12-5-10", "a13-5-15", "a14-6-1", "a15-6-6", "a16-6-12")
            expected = {f"kernel-{family}.yml" for family in families}
        else:
            name = "Build Kernel OnePlus-35226.yml"
            removed = "Build Kernel OnePlus-35226-SUSFS-TEST.yml"
            expected = {"Build Kernel OnePlus Snapdragon.yml", "Build Kernel OnePlus MTK.yml"}
        self.assertFalse((ROOT / ".github/workflows" / removed).exists())
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
        self.assertEqual({job["uses"].removeprefix("./.github/workflows/")
                          for job in workflow["jobs"].values()}, expected)
        for job in workflow["jobs"].values():
            self.assertEqual(job["with"][VERSION_INPUT], "35226")
            if VERSION_INPUT == "bakasu_version":
                self.assertEqual(job["with"]["kernelsu_variant"], "BakaSU")
                self.assertFalse(job["with"]["cancel_susfs"])
                self.assertFalse(job["with"]["use_kpm"])
                if job["uses"].endswith(("kernel-a15-6-6.yml", "kernel-a16-6-12.yml")):
                    self.assertFalse(job["with"]["use_zram"])
                if job["uses"].endswith("kernel-a12-5-10.yml"):
                    self.assertIs(job["with"]["onlyAk3"], False)
            parent = yaml.safe_load((ROOT / job["uses"]).read_text(encoding="utf-8"))
            on = parent.get("on", parent.get(True))
            self.assertEqual(on["workflow_call"]["inputs"][VERSION_INPUT]["default"], "35226")
            self.assertEqual(on["workflow_dispatch"]["inputs"][VERSION_INPUT]["default"], "35226")
            matrix_job = next(iter(parent["jobs"].values()))
            if VERSION_INPUT == "bakasu_version" and job["uses"].endswith("kernel-a12-5-10.yml"):
                self.assertIs(on["workflow_call"]["inputs"]["onlyAk3"]["default"], False)
                self.assertEqual(matrix_job["with"]["onlyAk3"], "${{ inputs.onlyAk3 || false }}")
            self.assertTrue(matrix_job["strategy"]["matrix"]["include"])
            self.assertEqual(matrix_job["with"][VERSION_INPUT],
                             "${{ inputs." + VERSION_INPUT + " || '35226' }}")

    def test_all_version_defaults_and_forwarding_select_35226(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
            on = workflow.get("on", workflow.get(True, {}))
            for event in ("workflow_dispatch", "workflow_call"):
                schema = (on.get(event) or {}).get("inputs", {}).get(VERSION_INPUT)
                if schema is not None:
                    with self.subTest(workflow=path.name, event=event):
                        self.assertEqual(schema["default"], "35226")
                        if schema["type"] == "choice":
                            self.assertEqual(set(schema["options"]), {"35226", "35227"})
            for job in workflow.get("jobs", {}).values():
                selected = job.get("with", {}).get(VERSION_INPUT)
                if selected is not None:
                    with self.subTest(workflow=path.name, selected=selected):
                        self.assertIn(selected, ("35226", "${{ inputs." + VERSION_INPUT + " }}",
                                                 "${{ inputs." + VERSION_INPUT + " || '35226' }}"))

    def test_manager_and_release_use_selected_version_pin(self):
        if VERSION_INPUT != "bakasu_version":
            self.skipTest("GKI manager and release workflow only")
        root = ROOT / ".github/workflows"
        manager = yaml.safe_load((root / "get-manager.yml").read_text(encoding="utf-8"))
        main = yaml.safe_load((root / "main.yml").read_text(encoding="utf-8"))
        manager_job = manager["jobs"]["get_ksu_manager"]
        release_job = main["jobs"]["release"]
        self.assertEqual(manager_job["env"].get("BAKASU_VERSION"), "${{ inputs.bakasu_version }}")
        self.assertEqual(release_job["env"].get("BAKASU_VERSION"), "${{ inputs.bakasu_version || '35226' }}")
        for filename in ("main.yml", "kernel-custom.yml"):
            workflow = yaml.safe_load((root / filename).read_text(encoding="utf-8"))
            manager_call = next(job for job in workflow["jobs"].values()
                                if job.get("uses") == "./.github/workflows/get-manager.yml")
            self.assertEqual(manager_call["with"].get("bakasu_version"),
                             "${{ inputs.bakasu_version || '35226' }}")
        for job, output in ((manager_job, "BAKASU_COMMIT"), (release_job, "KSU_REF")):
            runs = "\n".join(step.get("run", "") for step in job["steps"])
            match = re.search(r'case "\$BAKASU_VERSION" in.*?esac', runs, re.S)
            self.assertIsNotNone(match, "missing selected-version pin resolution")
            for version in (*BUILDS, "35266"):
                with self.subTest(output=output, version=version):
                    command = match[0] + '\nprintf "%s" "$' + output + '"'
                    result = subprocess.run([BASH, "-e", "-c", command], capture_output=True,
                                            text=True, env=dict(os.environ, BAKASU_VERSION=version))
                    if version in BUILDS:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, BUILDS[version])
                    else:
                        self.assertNotEqual(result.returncode, 0)


@unittest.skipUnless(os.environ.get("BAKASU_TEST_SOURCE") and os.environ.get("SUSFS_TEST_SOURCE"),
                     "real BakaSU and official SUSFS sources not supplied")
class OfficialSourceTests(unittest.TestCase):
    def test_real_workflow_preserves_official_code_except_redundant_function_guards(self):
        source = os.environ["BAKASU_TEST_SOURCE"]
        susfs = os.environ["SUSFS_TEST_SOURCE"]
        families = ("android12-5.10", "android13-5.15", "android14-6.1", "android15-6.6", "android16-6.12")
        run = next(step["run"] for step in next(jobs())["steps"]
                   if step.get("id") == "bakasu_susfs_setup")
        run = 'OFFICIAL_ROOT=' + run.split('OFFICIAL_ROOT=', 1)[1]
        run = run.replace("python3 -", shlex.quote(sys.executable.replace("\\", "/")) + " -")
        for (version, commit), family in product(BUILDS.items(), families):
            with self.subTest(version=version, family=family), tempfile.TemporaryDirectory() as directory:
                temp = Path(directory)
                ksu = temp / "KernelSU"
                before = {}
                for name in CONFIG["upstream_files"]:
                    content = subprocess.check_output(["git", "-C", source, "show", f"{commit}:{name}"])
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
                content = (ksu / "kernel/feature/selinux_hide.c").read_bytes().replace(b"\r\n", b"\n")
                guards = (
                    (b'    if (security_dump_masked_av_fn)\n'
                     b'        security_dump_masked_av_fn(policydb, scontext, tcontext, tclass, masked, "bounds");',
                     b'    security_dump_masked_av_fn(policydb, scontext, tcontext, tclass, masked, "bounds");'),
                    (b'    if (context_struct_compute_av_fn) {\n'
                     b'        context_struct_compute_av_fn(policydb, scontext, tcontext, tclass, avd, NULL);\n'
                     b'    } else {\n'
                     b'        context_struct_compute_av(policydb, scontext, tcontext, tclass, avd, NULL);\n'
                     b'    }',
                     b'    context_struct_compute_av_fn(policydb, scontext, tcontext, tclass, avd, NULL);'),
                )
                restored = content
                for original, direct in guards:
                    self.assertNotIn(original, content)
                    self.assertEqual(content.count(direct), 1)
                    restored = restored.replace(direct, original, 1)
                # Restoring only the two constant guards must recover the exact official blob.
                actual = subprocess.check_output([
                    "git", "hash-object", "--stdin"], input=restored).decode().strip()
                self.assertTrue(actual.startswith(match[1].decode()), actual)
                changed = {name for name, content in before.items() if (ksu / name).read_bytes() != content}
                self.assertEqual(changed, {"kernel/feature/selinux_hide.c", "kernel/tools/susfs_compat.mk"})
                guard = (ksu / "kernel/tools/susfs_compat.mk").read_bytes()
                self.assertNotIn(b"$(error Please wait susfs update.)", guard)
                self.assertIn(b"KSU_COMPAT_HAS_SUSFS_FEATURE_SELINUX_HIDE", guard)
                self.assertEqual(patch_path.read_bytes(), official)

    def test_selected_commits_have_matching_version_codes(self):
        for version, commit in BUILDS.items():
            count = int(subprocess.check_output([
                "git", "-C", os.environ["BAKASU_TEST_SOURCE"], "rev-list", "--count", commit]))
            self.assertEqual(count + 30700, int(version))


if __name__ == "__main__":
    unittest.main()
