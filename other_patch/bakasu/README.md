# BakaSU 35226 integration

The ReSukiSU workflow option now builds BakaSU (the renamed upstream project)
at `59afa46b325126b12a48234063cd0c2a6b735b57`, the main branch head reviewed on
2026-10-11. This is a reproducible snapshot, not a floating branch. Both build
workflows and the 5.10.236 test use it. `config/bakasu.json` records the revision,
version and reviewed source hashes. Manager downloads use the same revision.

The SID synchronization, permission-check ordering and SELinux parsing timing
fixes are already upstream. The build no longer applies the 35188 backport.
The historical patch and its tests remain available for review or rollback.

Upstream commit `48fa4bb7` suspended SUSFS builds because its older SELinux
implementation had not incorporated these fixes. We retain the existing
integration which removes only the two reviewed SUSFS SELinux diff blocks in
`hooks.c` and `selinuxfs.c`, preserving all other SUSFS changes. The family
hashes and filter are shared with the historical 35188 integration.

`scripts/bakasu_selinux.py` checks the exact upstream revision and source
hashes. Only after validating the resulting kernel's original SELinux hook
targets, when SUSFS is enabled, does it remove the three-line suspension
notice from `kernel/tools/susfs_compat.mk`. All other hook/compatibility checks
remain intact. Changed source, unfamiliar SUSFS diff blocks, missing targets
and patch rejections fail the preflight rather than bypassing checks.
Without SUSFS, no BakaSU source is modified.

The workflows continue enabling KALLSYMS_ALL. SELinux enforcing mode and
root permission policy are unchanged by this integration. Use a matching
BakaSU manager and enable its SELinux Hide setting when testing that feature.

Validation: `python -m unittest discover -s scripts/tests -v`.
Optional real-source fixtures: `BAKASU_TEST_SOURCE`, `RESUKISU_TEST_SOURCE`,
`SUSFS_TEST_SOURCE`, and `AOSP_SELINUX_TEST_SOURCE` (five family subdirectories
containing Makefile and SELinux sources). These tests are not a complete
kernel build or an on-device compatibility guarantee.
