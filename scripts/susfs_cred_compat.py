"""Mark SUSFS credential helpers that are optional with older KSU Kconfigs."""
from pathlib import Path
import re
import sys


DECLARATIONS = (
    b"static struct cred *susfs_switch_to_ksu_domain(void) {",
    b"static void susfs_restore_creds(struct cred *orig_cred) {",
)


def repair(root):
    path = root / "fs/susfs.c"
    if not path.is_file():
        return 0
    original = path.read_bytes()
    result = original
    changed = 0
    for declaration in DECLARATIONS:
        annotated = declaration.replace(b"static ", b"static __maybe_unused ", 1)
        old_count, new_count = original.count(declaration), original.count(annotated)
        if old_count + new_count > 1:
            raise ValueError(f"Duplicate SUSFS credential helper in {path}")
        if old_count:
            result = result.replace(declaration, annotated, 1)
            changed += 1
        elif not new_count:
            name = declaration.split(b"(")[0].split()[-1].lstrip(b"*")
            if re.search(rb"\b" + re.escape(name) + rb"\s*\(", original):
                raise ValueError(f"Unexpected signature for {name.decode()} in {path}")
    # Validate both definitions before writing so source drift cannot leave a half-fix.
    if changed:
        path.write_bytes(result)
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: susfs_cred_compat.py KERNEL_SOURCE_DIR")
    try:
        changed = repair(Path(sys.argv[1]))
        print(f"SUSFS optional credential helpers annotated: {changed}")
    except (OSError, ValueError) as error:
        sys.exit(f"::error::{error}")
