#!/usr/bin/env bash
set -euo pipefail

: "${KERNEL_ROOT:?}"
: "${REPO:?}"
: "${ANDROID17_PATCH_LEVEL:?}"
: "${ANDROID17_SUBLEVEL:?}"

cd "$KERNEL_ROOT"
branch="android17-6.18-${ANDROID17_PATCH_LEVEL}"
"$REPO" init --depth=1 -u https://android.googlesource.com/kernel/manifest \
    -b "common-${branch}"

if [[ -n "${ANDROID17_SOURCE_REVISION:-}" ]]; then
    ref="refs/tags/${branch}_${ANDROID17_SOURCE_REVISION}"
    git ls-remote --exit-code https://android.googlesource.com/kernel/common "$ref" >/dev/null
    mkdir -p .repo/local_manifests
    "${PYTHON:-python3}" - "$ref" <<'PY'
import sys
import xml.etree.ElementTree as ET
root = ET.Element("manifest")
ET.SubElement(root, "extend-project", name="kernel/common", path="common", revision=sys.argv[1])
ET.ElementTree(root).write(".repo/local_manifests/android17.xml", encoding="utf-8", xml_declaration=True)
PY
fi

"$REPO" sync -c -j"$(nproc --all)" --no-tags --fail-fast
"${PYTHON:-python3}" - "$ANDROID17_SUBLEVEL" <<'PY'
from pathlib import Path
import sys
values = dict(line.split("=", 1) for line in Path("common/Makefile").read_text().splitlines() if "=" in line)
values = {key.strip(): value.strip() for key, value in values.items()}
actual = ".".join(values[key] for key in ("VERSION", "PATCHLEVEL", "SUBLEVEL"))
expected = "6.18." + sys.argv[1]
if actual != expected:
    raise SystemExit(f"Official source version changed: expected {expected}, got {actual}")
print(f"Official Android 17 source: {actual}")
PY

test -x tools/bazel
test -f common/bazel/constants.scl
echo "Using toolchains selected by the official Android 17 manifest and Kleaf"
