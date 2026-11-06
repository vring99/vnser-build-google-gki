"""Keep legacy kernel builds on the validated host distribution."""
from pathlib import Path
import unittest

import yaml


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"


class BuildEnvironmentTests(unittest.TestCase):
    def test_kernel_builders_pin_ubuntu_2404(self):
        for filename in ("build.yml", "build-make.yml"):
            with self.subTest(workflow=filename):
                workflow = yaml.safe_load((WORKFLOWS / filename).read_text(encoding="utf-8"))
                self.assertEqual(workflow["jobs"]["build-kernel"]["runs-on"], "ubuntu-24.04")


if __name__ == "__main__":
    unittest.main()
