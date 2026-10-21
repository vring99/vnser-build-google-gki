from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
BUILDERS = {"build.yml", "build-make.yml"}
TOGGLE = "use_uni"
FORWARD = "${{ inputs.use_uni }}"
PATCH_STEP = "\u5e94\u7528 Unicode \u7ed5\u8fc7\u4fee\u590d"


def load_workflows():
    return {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in (ROOT / ".github/workflows").glob("*.yml")
    }


def events(workflow):
    return workflow.get("on", workflow.get(True, {}))


def callees(workflow):
    for job in workflow.get("jobs", {}).values():
        uses = job.get("uses", "")
        if uses.startswith("./.github/workflows/"):
            yield uses.removeprefix("./.github/workflows/"), job


def build_chain(workflows):
    affected = set(BUILDERS)
    while True:
        callers = {
            name for name, workflow in workflows.items()
            if any(target in affected for target, _ in callees(workflow))
        }
        expanded = affected | callers
        if expanded == affected:
            return affected
        affected = expanded


class UnicodeToggleTests(unittest.TestCase):
    def test_manual_variant_choices_use_pro_name(self):
        for name, workflow in self.workflows.items():
            inputs = (events(workflow).get("workflow_dispatch") or {}).get("inputs", {})
            variant = inputs.get("kernelsu_variant", {})
            if variant:
                with self.subTest(workflow=name):
                    self.assertIn("BakaSU Pro", variant["options"])
                    self.assertNotIn("BakaSU", variant["options"])

    @classmethod
    def setUpClass(cls):
        cls.workflows = load_workflows()
        cls.chain = build_chain(cls.workflows)

    def assert_toggle_schema(self, workflow, event):
        schema = (events(workflow)[event] or {}).get("inputs", {}).get(TOGGLE)
        self.assertIsNotNone(schema, f"missing {event} Unicode toggle")
        self.assertEqual(schema["type"], "boolean")
        self.assertIs(schema.get("default"), True)
        self.assertFalse(schema.get("required", False))

    def test_builders_enable_unicode_by_default(self):
        for name in BUILDERS:
            with self.subTest(workflow=name):
                self.assert_toggle_schema(self.workflows[name], "workflow_call")

    def test_every_manual_build_entrypoint_exposes_toggle(self):
        for name in self.chain - BUILDERS:
            with self.subTest(workflow=name):
                self.assert_toggle_schema(self.workflows[name], "workflow_dispatch")

    def test_intermediate_reusable_workflows_accept_toggle(self):
        for name in self.chain - BUILDERS:
            if "workflow_call" in events(self.workflows[name]):
                with self.subTest(workflow=name):
                    self.assert_toggle_schema(self.workflows[name], "workflow_call")

    def test_kpm_is_disabled_by_default_in_every_build_entrypoint(self):
        for name in self.chain:
            for event, config in events(self.workflows[name]).items():
                schema = (config or {}).get("inputs", {}).get("use_kpm")
                if schema is not None:
                    with self.subTest(workflow=name, event=event):
                        self.assertEqual(schema["type"], "boolean")
                        self.assertIs(schema.get("default"), False)
            for target, job in callees(self.workflows[name]):
                if target in self.chain:
                    with self.subTest(workflow=name, target=target):
                        inputs = events(self.workflows[target])["workflow_call"]["inputs"]
                        if "use_kpm" in inputs:
                            self.assertIn(job["with"]["use_kpm"], (False, "${{ inputs.use_kpm }}"))
                        else:
                            self.assertNotIn("use_kpm", job["with"])

    def test_all_callers_preserve_false_without_truthy_fallback(self):
        for name in self.chain:
            for target, job in callees(self.workflows[name]):
                if target in self.chain:
                    with self.subTest(workflow=name, target=target):
                        self.assertEqual(job.get("with", {}).get(TOGGLE), FORWARD)
                        schema = events(self.workflows[target])["workflow_call"]["inputs"]
                        self.assertIn(TOGGLE, schema)

    def test_only_unicode_patch_is_guarded_in_both_builders(self):
        runs = []
        for name in BUILDERS:
            job = self.workflows[name]["jobs"]["build-kernel"]
            guarded = [step for step in job["steps"] if "inputs.use_uni" in step.get("if", "")]
            with self.subTest(workflow=name):
                self.assertEqual(len(guarded), 1)
                self.assertEqual(guarded[0]["name"], PATCH_STEP)
                self.assertEqual(guarded[0]["if"], "inputs.use_uni")
                self.assertIn("unicode_bypass_fix_6.1-.patch", guarded[0]["run"])
                self.assertIn("unicode_bypass_fix_6.1+.patch", guarded[0]["run"])
                runs.append(guarded[0]["run"])
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0], runs[1])

    def test_build_summary_records_toggle(self):
        for name in BUILDERS:
            steps = self.workflows[name]["jobs"]["build-kernel"]["steps"]
            with self.subTest(workflow=name):
                self.assertTrue(any("Unicode" in step.get("run", "")
                                    and FORWARD in step["run"] for step in steps))


if __name__ == "__main__":
    unittest.main()
