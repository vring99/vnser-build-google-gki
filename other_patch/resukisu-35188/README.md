# ReSukiSU 35188 SELinux backport

Base: `c4bdcaae023f8318e85b0782cf534fca4bc8aa62` (35188).
The workflow applies an uncommitted patch; it does not advance HEAD, change
the version calculation, or update the manager/Kconfig/Kbuild to BakaSU.
The full version string can include `c4bdcaae-dirty` to identify the backport.

`selinux-fixes.patch` combines these upstream ports, in order:

| ReSukiSU commit | KernelSU commit | Change |
| --- | --- | --- |
| `ba8cfa502ebe9d84c0fe79928f07b0738c5a2c55` | `a810677b847ba564c5f9d3fa0fbabe54794b8eef` | Synchronize contexts with the global SID table (PR 3873). |
| `969de3d0ce65d2d75e5a2f6c548d9e14817ef459` | `df03912f70d92ff2aa9762ef82d607033d37e1da` | Preserve permission-check ordering. |
| `fa12ffde8244008e9afdb7ac0c1bb1bc1b06b95a` | `a85dcbcfc3b09ae410f31aae124d04f3777fdf1c` | Fix the SELinux parsing timing side channel (PR 3877). |

Sources: <https://github.com/Baka-SU/BakaSU/commits/main/kernel/feature/selinux_hide.c>
and <https://github.com/tiann/KernelSU/pull/3877>.
The generated patch preserves these ports, with trailing whitespace on two lines cleaned up.
Unrelated changes between 35188 and the newer commits are excluded.

## SUSFS integration

Both build workflows enable this backport only for the ReSukiSU variant.
All five configured GKI families are supported: android12-5.10,
android13-5.15, android14-6.1, android15-6.6 and android16-6.12.

The current SUSFS patches install their own SELinux handlers in `hooks.c`
and `selinuxfs.c`. These disable ReSukiSU's automatic hooks and retain the
older implementation. The helper removes only those two reviewed diff blocks
before applying SUSFS. Every other SUSFS patch byte is preserved, including
the separate `avc.c` changes. SHA256 checks accept LF and CRLF copies but
reject unknown, missing or duplicate blocks, including unreviewed custom pins.
The reviewed revisions and hashes are in `config/resukisu-35188-selinux.json`.

The fixed ReSukiSU implementation then owns the SELinux entry points.
`CONFIG_KALLSYMS_ALL=y` supplies the static table symbols it resolves;
this does not change SELinux enforcing mode or grant root access.
Build preflight checks reject remaining legacy handlers, unexpected hook
targets, conflicting policy-query patches, and modified backport files.
The upstream SUSFS build stop introduced after 35188 is not imported.

## Validation and runtime

Run `python -m unittest discover -s scripts/tests -p 'test_resukisu_35188_selinux.py'`.
The optional integration test accepts `RESUKISU_TEST_SOURCE`, a pristine
35188 Git checkout, and `SUSFS_TEST_SOURCE`, a SUSFS Git checkout containing
the five reviewed commits. It tests real source without modifying either
fixture, compares the patched files to recorded hashes and validates the
remaining diff bytes for each family.

SELinux Hide must be enabled in the compatible 35188 manager, saved, and
the device rebooted so the policy backup is available. A kernel compile,
boot test and ordinary-app tests are still required on each target family.
Successful patch/preflight checks alone do not prove a complete kernel build
or guarantee that ChunQiu will stop reporting other detection points.
