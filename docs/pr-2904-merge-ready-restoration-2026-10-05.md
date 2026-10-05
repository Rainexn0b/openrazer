# PR 2904 merge-ready restoration, 2026-10-05

This document records the earlier restoration round on the now-redundant local
branch. Its unique fixes and regression coverage have been consolidated onto
the canonical `test-pr-2904-edualb` working tree; the source-state descriptions
below are historical, not instructions to resume work on `pr-2904-merge-ready`.

## Canonical Consolidation

The sole local branch and checkout are now `test-pr-2904-edualb` in
`/home/cyril/Projects/openrazer`. The branch tracks
`fork/test-pr-2904-edualb`; at consolidation its committed head was `5466ee9d`. The new
keyboard-probe and activity-lifetime fixes, four regression modules, and small
documentation/README cleanups were applied locally and initially left uncommitted.
Existing readiness/recovery, Viper SE, F18, and scroll v1/v2 support are retained.

The exact consolidated source passed all 55 focused tests, both strict kernel
builds, formatting, type checks, and reproducible generation of all 276 fixtures.
After moving the checkout, all 469 source/document files matched that validated
snapshot byte-for-byte and the focused tests passed again. Full daemon testing
retains only the previously recorded legacy error.

All three historical documents were preserved. The old linked checkout and
local `pr-2904-merge-ready`, `add-razer-naga-v3-pro-support`, and `master` branch
names were removed; remote branches were not changed. The two temporary
transfer stashes were dropped after verification, leaving an empty stash list.

Complete-history bundles and both pre-cleanup worktree archives are stored in
`.git/opencode-backups/canonical-consolidation-2026-10-05/`. Historical ignored
package archives and build artifacts in the primary checkout were left intact.
No commit, push, package installation, or service restart was performed during
consolidation. The following sections describe the earlier restoration round.

The subsequent deployment recipe is preserved in `packaging/arch-canonical/`.
It requires a full immutable canonical commit, builds all three local packages
together, runs the 55 focused tests, and installs a source-commit record with
each component. This replaces the old mutable-overlay package workflow.

## Source state

The changes are in `/home/cyril/Projects/openrazer` on
`pr-2904-merge-ready`, based on `01ebdefd`. They are uncommitted. No branches
were rebased, staged, committed, or pushed, and no packages, drivers, or services
were updated during this source-restoration round.

The reference for readiness/recovery was the linked derivative worktree at
`5466ee9d`. Its safety commits map as follows:

| Original | Rebased | Behavior |
| --- | --- | --- |
| `4f929a86` | `6604714b` | Remove the activity sysfs attribute on disconnect |
| `430529fa` | `406cc216` | Non-blocking readiness and timeout-safe shutdown |
| `7b22a814` | `9371b3c3` | Continue discovery after rapid retry exhaustion |
| `76c7abf2` | `5466ee9d` | Verified, mode-intent-aware wireless recovery |

Unrelated Viper SE support and scroll-protocol helper renames were not imported.
The branch remains one commit behind recorded `origin/master` (`477f4d6d`), whose
additional commit is Viper SE support, not a Naga recovery fix.

## Readiness and recovery

- Wireless Naga construction requires one live valid serial response before
  starting background resources or registering D-Bus state.
- `DeviceNotReadyError` allows independent asynchronous device discovery:
  fifteen retries two seconds apart, then quiet thirty-second retries.
- Removal and shutdown cancel pending callbacks; retry tokens prevent stale
  callbacks from consuming replacement state.
- Resume/DPI/mode failures during shutdown do not prevent resource cleanup.
- Driver mode is verified during recent input activity, not only after a
  perfectly recognized idle transition. Checks are limited by a two-second
  interval and a five-second recent-activity window.
- Mode checks require serial readiness. Restoration is logged only after a
  matching mode readback; dropped writes, busy responses, and malformed reads
  are retried during later activity.
- Explicit firmware-mode requests disable automatic restoration, including on
  resume. A failed firmware-mode request also disables recovery; the requesting
  client is responsible for retrying its requested write.
- The monitor is stopped and joined before device teardown, including safe
  cleanup when a monitor never started.
- The kernel activity interface and its matching disconnect removal are
  restored. Wireless receiver delay is again device-specific at 99900 us.

## Additional safety fixes

The upstream keyboard probe regression is corrected locally:

- Attribute-creation failures return the actual error rather than uninitialized
  data or false success.
- Driver data is initialized and published before attributes are exposed.
- Probe failures reuse the attribute-removal helper before clearing private
  data and freeing it. Disconnect follows the same callback-safe ordering.

Review of the restored Naga activity interface also found a sibling-interface
lifetime race. Activity now holds an owned HID device reference, performs
private-data lookup and timestamp access under a driver-local IRQ-safe lock,
and releases the device reference outside that lock. Publication and retirement
use the same lock. Disconnect and probe failure clear private data before free;
early activity sysfs reads return `-EAGAIN` instead of dereferencing null data.

The legacy generic Report 4 sibling lookup remains inherited and unprotected;
Naga activity does not use that path. Complete generic mouse probe/sysfs
unwinding was not refactored in this round.

## Capability and metadata fixes

- The DPI report builder preserves values through 50000 on both axes.
- Naga scroll mode version 2 exposes tactile, free-spin, and precision-tactile
  modes. Existing Basilisk version 1 devices retain their two modes.
- D-Bus and the Python client expose ordered scroll-mode options with matching
  capability discovery.
- Naga wired/wireless fake fixtures are regenerated, including the activity
  attribute and all existing scroll/lighting attributes.
- AppStream includes wireless PID `00E8`.
- Duplicate Naga README entries are removed and the two transports appear in
  PID order.
- Obsolete commented sysfs-creation statements and formatting defects are
  removed, allowing the normal generators and formatting checks to pass.

## Validation

All tests below use fake devices or source-derived userspace C harnesses. No
physical input nodes or USB operations are used.

| Check | Result |
| --- | --- |
| Readiness/hotplug tests | 18 passed |
| Wireless recovery/lifecycle tests | 20 passed |
| Keyboard probe harness | 8791 failure/teardown cases passed |
| Mouse activity harness | 12 lifetime/interleaving cases passed |
| Daemon scroll/DPI tests | 8 passed |
| Python client scroll tests | 7 passed |
| Combined focused Python tests | 55 passed |
| Full daemon suite | 71 of 72 passed; known `test_notify_run_effect_edge_case_3` error |
| Strict client mypy and targeted daemon mypy | Passed |
| Python compilation and whitespace checks | Passed |
| Project C and Python formatting | Passed |
| Generated-file reproducibility | AppStream and all 274 fixture hashes unchanged |

The unchanged branch HEAD reproduces the same legacy `effect_sync` test error.
Both C harnesses run with AddressSanitizer and UndefinedBehaviorSanitizer.

All four kernel modules build with `KCFLAGS='-Wall -Werror'` on:

- `7.2.4-1-cachyos`, Clang/LLVM 22.1.8 with `LLVM=1`
- `6.18.50-1-cachyos-lts`, GCC 16.2.1

Both final builds completed without warnings or errors. Validation was performed
in a disposable working-tree snapshot under
`/tmp/opencode/openrazer-restored-validation`; final logs and generated-file
manifests are in `.validation/final-refresh/` there.

## Deployment and hardware gates

The running installation remains `openrazer-daemon-local
3.12.1.pr2904.fix1-7` with driver/client packages at `fix1-1`. It is not a runtime
test of the restored merge-ready source.

After committing the chosen complete source revision, build all packages from
that immutable revision. Do not reuse the old mutable-overlay recipe as
evidence of a build of this branch. Package installation and any required
driver reload need a separate coordinated operation.

Repeat physical idle/wake cycles without daemon restarts, including immediate
wake after LED shutdown, F17 down/up delivery, firmware/software handoff, and
receiver reconnect. Confirm input latency and dock responsiveness with the
restored receiver delay and active-only mode checks. Fake mutexes and sanitizers
do not establish real IRQ/lockdep or hardware behavior.
