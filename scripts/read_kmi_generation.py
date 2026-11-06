"""Read the source KMI generation without executing kernel build configs."""
from pathlib import Path
import re
import shlex
import sys


def read_generation(root):
    generations = set()
    for name in ("build.config.constants", "build.config.common", "bazel/constants.scl"):
        path = root / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*(?:export\s+)?KMI_GENERATION\s*=\s*(.*)$", line)
            if not match:
                continue
            values = shlex.split(match[1], comments=True)
            if len(values) != 1 or not re.fullmatch(r"[0-9]+", values[0]):
                raise ValueError(f"Non-literal KMI_GENERATION in {path}")
            generations.add(int(values[0]))
    if len(generations) != 1:
        raise ValueError(f"Missing or conflicting KMI_GENERATION in {root}: {sorted(generations)}")
    return generations.pop()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: read_kmi_generation.py KERNEL_SOURCE_DIR")
    try:
        print(read_generation(Path(sys.argv[1])))
    except (OSError, ValueError) as error:
        sys.exit(f"::error::Cannot determine KMI_GENERATION: {error}")
