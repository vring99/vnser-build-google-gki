#!/usr/bin/env bash
set -euo pipefail

: "${KERNEL_ROOT:?}"
: "${DEFCONFIG:?}"
cd "$KERNEL_ROOT"

fragment="common/arch/arm64/configs/ksu.fragment"
# Keep the original defconfig for Kleaf's checks; retain the fragment on timeout retries.
if ! cmp -s "$DEFCONFIG.orig" "$DEFCONFIG" || [[ ! -f "$fragment" ]]; then
    "${PYTHON:-python3}" - "$DEFCONFIG.orig" "$DEFCONFIG" "$fragment" <<'PY'
from pathlib import Path
import re
import sys
pattern = re.compile(r"^(CONFIG_\w+)=.*$|^# (CONFIG_\w+) is not set$")
def config(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        match = pattern.fullmatch(line)
        if match:
            result[match[1] or match[2]] = line
    return result
original, modified = config(sys.argv[1]), config(sys.argv[2])
lines = [line for key, line in modified.items() if original.get(key) != line]
Path(sys.argv[3]).write_text("\n".join(lines) + "\n")
PY
    cp "$DEFCONFIG.orig" "$DEFCONFIG"
fi

cat "$fragment"
tools/bazel build --disk_cache="${HOME}/.cache/bazel" --config=fast \
    --lto=thin --notrim --nokmi_symbol_list_strict_mode \
    --defconfig_fragment=//common:arch/arm64/configs/ksu.fragment \
    //common:kernel_aarch64

resolved="bazel-bin/common/kernel_aarch64_config/out_dir/.config"
test -s "$resolved"
required=(CONFIG_KSU=y CONFIG_KALLSYMS=y CONFIG_KALLSYMS_ALL=y
    '# CONFIG_KSU_MULTI_MANAGER_SUPPORT is not set')
if [[ "${ENABLE_SUSFS:-false}" == true ]]; then
    required+=(CONFIG_KSU_SUSFS=y)
else
    required+=(CONFIG_KSU_TRACEPOINT_HOOK=y CONFIG_HAVE_SYSCALL_TRACEPOINTS=y CONFIG_TRACEPOINTS=y
        '# CONFIG_KSU_SUSFS is not set' '# CONFIG_KSU_MANUAL_HOOK is not set')
fi
for option in "${required[@]}"; do
    if ! grep -Fxq "$option" "$resolved"; then
        echo "::error::Android 17 resolved config is missing: $option"
        exit 1
    fi
done

image="bazel-bin/common/kernel_aarch64/Image"
test -s "$image"
mkdir -p common/out/arch/arm64/boot
cp "$image" common/out/arch/arm64/boot/Image
