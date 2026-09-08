# Razer Naga V3 Pro Support Tracker

Last updated: 2026-09-08

This document tracks workstation setup, repository verification, hardware
discovery, and the remaining work needed to validate support for the Razer
Naga V3 Pro. Device serial numbers are intentionally omitted.

## Status

- [x] Clone `https://github.com/openrazer/openrazer`.
- [x] Create branch `add-razer-naga-v3-pro-support`.
- [x] Read `DEVELOPMENT.md` and the new-device issue template.
- [x] Inventory the CachyOS development environment.
- [x] Identify the wired and wireless USB IDs.
- [x] Search for existing issues and pull requests by name and USB ID.
- [x] Build the unmodified kernel modules against the installed CachyOS
  kernel headers.
- [x] Run the hex-casing check.
- [x] Install missing development and CI packages.
- [x] Authenticate GitHub CLI.
- [x] Reboot into the kernel matching the installed headers.
- [x] Create the `plugdev` group and add the development user.
- [x] Run all project CI checks, including fake-device integration.
- [x] Capture a local privileged HID descriptor dump.
- [x] Create the `Rainexn0b/openrazer` fork and add it as the `fork` remote.
- [x] Integrate PR #2904 and harden its scroll-mode and raw-event handling.
- [x] Regenerate metadata and pass the 269-device fake integration test.
- [ ] Install the fork through local pacman/DKMS packages.
- [ ] Test the existing support implementation on this mouse in wired and
  HyperSpeed modes.

## Repository

- Local path: `/home/cyril/Projects/openrazer`
- Upstream remote: `https://github.com/openrazer/openrazer`
- Fork remote: `https://github.com/Rainexn0b/openrazer`
- Initial revision: `6820f9da169d354bc7e6e93a0aa8683a6bb75792`
- Working branch: `add-razer-naga-v3-pro-support`

The repository is public, so cloning did not require authentication. GitHub
CLI was installed afterward and authentication as `Rainexn0b` was verified.

## Workstation Setup

The workstation runs CachyOS. At discovery time it was running kernel
`7.2.0-1-cachyos`, while the installed `linux-cachyos` and
`linux-cachyos-headers` packages were version `7.2.3-1`. It now runs
`7.2.3-1-cachyos`, matching the installed build headers.

Core tools and runtime dependencies already present include `base-devel`,
Git, Python, Clang/LLVM, the matching CachyOS headers, D-Bus Python bindings,
PyGObject, pyudev, daemonize, setproctitle, libnotify, xautomation, usbutils,
DKMS, and `usbhid-dump`.

The missing dependencies and CI tools were installed with:

```sh
sudo pacman -S --needed bc python-numpy github-cli astyle autopep8 \
  python-pylint mypy
```

Vermin is also used by CI but is packaged as `python-vermin` in the AUR on
this system:

```sh
paru -S --needed python-vermin
```

The upstream source daemon and udev rules use `plugdev`. Create that group when
running directly from source, then refresh the login session (a reboot also
satisfies this):

```sh
sudo groupadd -f plugdev
sudo gpasswd -a "$USER" plugdev
```

The Arch package patches this group name to `openrazer` and creates the group
with `systemd-sysusers`. The development user is already a member of that
group, so the packaged fork should preserve Arch's patch.

Authenticate GitHub after installing the CLI:

```sh
gh auth login --web --git-protocol https
gh auth status
```

If contributing changes, create a fork and replace or supplement the push
remote rather than pushing to upstream directly.

## Baseline Verification

The initial GCC build failed because CachyOS built its kernel with Clang and
the generated kernel flags are not accepted by GCC. The baseline modules build
successfully against the running `7.2.3-1-cachyos` kernel with LLVM:

```sh
make driver \
  KERNELDIR=/lib/modules/7.2.3-1-cachyos/build \
  LLVM=1 \
  KCFLAGS='-Wall -Werror'
```

Verification results so far:

| Check | Result | Notes |
| --- | --- | --- |
| `./scripts/ci/test-hex-casing.sh` | Pass | No issues found. |
| Kernel module build | Pass | All four modules built with LLVM and warnings as errors. |
| AStyle formatting | Pass | Run after `make driver_clean` to exclude generated `*.mod.c` files. |
| autopep8 formatting | Pass | No output. |
| Pylint errors | Pass | No output. |
| Python compatibility | Pass | Vermin reports a minimum version of Python 3.9. |
| Mypy strict subset | Pass | 13 source files checked. |
| Generated files | Pass | AppStream and all fake-driver files reproduce cleanly. |
| PID consistency | Pass | README, daemon, driver, and udev PIDs agree. |
| Fake-device integration | Pass | All 269 generated devices loaded and endpoint tests passed. |
| Privileged USB descriptors | Pass | Local wired HID dump completed with `sudo`. |
| Additional daemon unit tests | Baseline failure | 23 of 24 pass; see note below. |

The fake-device test was run in the project's `--as-root` test mode because
the development shell does not have supplementary `plugdev` membership.

The additional unit-test failure is
`EffectSyncTest.test_notify_run_effect_edge_case_3`: a `setBreathSingle`
request does not fall back to `setPulsate` for a non-Chroma BlackWidow. These
tests are not run by project CI, the failure is present on unmodified master,
and it is unrelated to this device effort.

After setup and reboot, run:

```sh
./scripts/ci/check-astyle-formatting.sh
./scripts/ci/check-autopep8-formatting.sh
./scripts/ci/check-pylint.sh
./scripts/ci/test-auto-generate.sh
./scripts/ci/test-hex-casing.sh
./scripts/ci/test-vermin.sh
./scripts/ci/test-mypy.sh
PYTHONPATH=pylib python3 ./scripts/ci/test-pids.py
```

Run the fake-device integration test in a disposable D-Bus session. Ensure no
normal OpenRazer daemon is running first.

```sh
eval "$(dbus-launch --sh-syntax)"
./scripts/ci/setup-fakedriver.sh
./scripts/ci/launch-daemon.sh
sleep 5
./scripts/ci/test-daemon.sh
```

The workstation already has packaged `openrazer-daemon` and
`openrazer-driver-dkms` version `3.12.4`. Do not mix packaged and source-built
modules during hardware tests; replace them with tracked local packages and
use pacman to roll back.

## Local Arch Packages

The split package recipe in `packaging/arch-local` is pinned to tested fork
commit `2ee8b759b19ebb8464a4ebf497bdc84a18a04f5c`. Build all three packages as an
unprivileged user:

```sh
cd packaging/arch-local
makepkg --cleanbuild --clean
```

Install them together so pacman can replace the official daemon and DKMS
packages while satisfying the local split-package dependencies:

```sh
sudo pacman -U \
  ./openrazer-driver-dkms-local-3.12.4.nagav3.1-4-any.pkg.tar.zst \
  ./openrazer-daemon-local-3.12.4.nagav3.1-4-any.pkg.tar.zst \
  ./python-openrazer-local-3.12.4.nagav3.1-4-any.pkg.tar.zst
```

Reboot after the DKMS transaction so no released module remains loaded. Leave
the receiver disconnected for the first wired test. Verify the installation
with:

```sh
pacman -Q openrazer-daemon-local openrazer-driver-dkms-local \
  python-openrazer-local
dkms status
systemctl --user status openrazer-daemon.service
```

To roll back, remove the optional local Python client first, replace the local
daemon and driver with repository packages, and reboot:

```sh
sudo pacman -R python-openrazer-local
sudo pacman -S openrazer-daemon openrazer-driver-dkms
```

## Device Discovery

The project calls the device **Razer Naga V3 Pro**, although it may be
described conversationally as "Naga Pro v3".

Known identity information:

| Property | Value | Source |
| --- | --- | --- |
| Vendor ID | `1532` | Local USB discovery |
| Wired PID | `00E7` | Existing upstream issue and PR |
| HyperSpeed wireless PID | `00E8` | Local USB discovery |
| Product number | `RZ01-05660100-R3U1` | Local retail box |
| Model number | `R201-0566` | Existing upstream issue; confirm local sticker |
| Device release | `1.00` | Local USB descriptor |

Both connection paths were observed locally at the same time after connecting
the USB cable while leaving the receiver attached:

```text
ID 1532:00e7 Razer USA, Ltd Razer Naga V3 Pro
ID 1532:00e8 Razer USA, Ltd Razer Naga V3 Pro
```

The wireless USB configuration reports USB 2.0 at full speed, bus power with
remote wakeup, a 100 mA maximum, and four HID interfaces. Their report
descriptor lengths are 85, 314, 61, and 22 bytes.

The wired USB configuration reports USB 2.0 at full speed, self power with
remote wakeup, a 500 mA maximum, and four HID interfaces. Their report
descriptor lengths are 79, 61, 159, and 22 bytes. The expected input and
`hidraw` symlinks exist under `/dev/input/by-id/` for all four interfaces.

Until support rules are installed, descriptor dumps require elevation because
the released udev rules do not know PIDs `00E7` and `00E8`:

```sh
sudo usbhid-dump -m 1532:00e7 -ed
sudo usbhid-dump -m 1532:00e8 -ed
```

The wired command completed locally. The existing upstream issue contains HID
dumps for both PIDs, and the local USB descriptors match that report.

## Upstream Coordination

- Device request: [openrazer/openrazer#2885](https://github.com/openrazer/openrazer/issues/2885)
- Existing implementation: [openrazer/openrazer#2904](https://github.com/openrazer/openrazer/pull/2904)

Do not open a duplicate issue or independent implementation without first
coordinating with those contributors. The current pull request adds PIDs
`00E7` and `00E8`, daemon classes, driver attributes, udev rules, generated
metadata, RGB zones, DPI, polling, battery, and scroll-wheel controls.

A maintainer identified one concrete gap: precision-tactile scroll mode uses
value `2`, but the D-Bus method in PR #2904 accepts only `0` or `1`. The fork
now has device-aware validation and focused tests for values `0` through `2`.

As of 2026-09-08, PR #2904 is open, non-draft, and mergeable. Its Ubuntu build
passes, but its Alpine build stops at AStyle formatting for
`driver/razermouse_driver.c`; later Alpine checks therefore have not run. The
implementation needs formatting plus a fresh full CI run in addition to the
D-Bus adjustment above.

## Hardware Test Matrix

Test changes on a separate system or VM where practical; the project warns
that development kernel modules can cause kernel panics and data loss.

Do not connect the cable and HyperSpeed receiver at the same time during these
tests. Both transports report the same serial, while the daemon uses serials as
unique D-Bus object paths and lookup keys. Supporting both concurrently needs a
separate daemon identity design change.

| Area | Wired `00E7` | Wireless `00E8` | Notes |
| --- | --- | --- | --- |
| Device discovery and daemon restart | Pending | Pending | Verify mode switching without stale devices. |
| Serial and firmware reads | Pending | Pending | Serial must not become `UNKWN...`. |
| Battery and charge state | Pending | Pending | Compare with Synapse or another trusted reading. |
| DPI read/write and stages | Pending | Pending | Test boundaries up to reported 50,000 DPI. |
| Poll rate | Pending | Pending | Test every exposed rate. |
| Logo RGB | Pending | Pending | Static, spectrum, wave, reactive, breath, off, brightness. |
| Scroll-wheel RGB | Pending | Pending | Static, spectrum, wave, reactive, breath, off, brightness. |
| Side-button RGB | Pending | Pending | Test both six-button and twelve-button plates. |
| Scroll mode | Pending | Pending | Tactile `0`, free-spin `1`, precision tactile `2`. |
| Scroll acceleration | Pending | Pending | Read/write and persistence. |
| Smart Reel | Pending | Pending | Read/write and persistence. |
| Buttons and wheel tilt | Pending | Pending | Include side plates and Hypershift behavior. |
| Suspend/resume and reconnect | Pending | Pending | Include switching wired/wireless modes. |

For each failure, retain relevant `dmesg` output and note the connection mode,
firmware, side plate, command, expected behavior, and observed behavior. USB
packet captures and Synapse screenshots are still useful for unsupported or
ambiguous behavior; follow the project's reverse-engineering wiki before
capturing traffic.
