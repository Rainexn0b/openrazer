# UPGRADE fix2 to fix3

Scope: the fix3 packaging candidate only — the reviewed Naga serial-collision
source fix plus the `packaging/arch-canonical/PKGBUILD` recipe change in one
combined commit. Python daemon/library changes only; no driver (C) changes and
no new hardware IDs. Naga Control still treats dual attached `00E7`+`00E8`
transports as a conflict by design; this repair only prevents overwrite and
duplicate D-Bus registration with ordered handover.

## 1. Build gate (supervisor-owned)

From a clean canonical checkout with the combined source+recipe commit:

```bash
export OPENRAZER_COMMIT="$(git rev-parse HEAD)"
case "$OPENRAZER_COMMIT" in
  ???????????????????????????????????????? ) ;;
  * ) echo "OPENRAZER_COMMIT must be a full 40-hex SHA" >&2; exit 1 ;;
esac
makepkg --verifysource
makepkg --cleanbuild --clean --log
makepkg --packagelist
```

`OPENRAZER_COMMIT` is mandatory and must be the full commit SHA of the combined
commit. The recipe pins that exact SHA for all three packages; branch tips and
tags are not build inputs. `makepkg --cleanbuild` runs the package `check()`
gate: the 6 pre-existing suites plus
`daemon.tests.test_serial_identity`,
`daemon.tests.test_serial_handover`, and
`daemon.tests.test_serial_dbus` — 80 tests total, all of which must pass. The
check environment is private and fake-safe (no inherited D-Bus address,
isolated `HOME`/`XDG_*`/`TMPDIR` under the builder srcdir, pinned fixture
root); no live device, udev, daemon, GUI, or hardware test is involved.

## 2. Rollback first

Before installing anything, copy all three fix2 archives plus their hashes and
`.BUILDINFO` records to a safe location outside the build directory. The
expected fix2 filenames follow the recipe pattern
`<pkgname>-3.12.1.pr2904.fix2-1-any.pkg.tar.zst` for `openrazer-driver-dkms-local`,
`openrazer-daemon-local`, and `python-openrazer-local`. Confirm exact names
against the retained fix2 `makepkg --packagelist` output.

## 3. Single visible transaction

Install the three new archives together in one visible privileged terminal
using their exact filenames from the fix3 `makepkg --packagelist` output
(`<pkgname>-3.12.1.pr2904.fix3-1-any.pkg.tar.zst` for the same three package
names). Do not mix fix2 and fix3 archives in one transaction and do not use
globs that could match stale packages:

```bash
sudo pacman -U openrazer-driver-dkms-local-3.12.1.pr2904.fix3-1-any.pkg.tar.zst \
  openrazer-daemon-local-3.12.1.pr2904.fix3-1-any.pkg.tar.zst \
  python-openrazer-local-3.12.1.pr2904.fix3-1-any.pkg.tar.zst
```

## 4. Safety preconditions (attended only)

- A backup keyboard is available.
- The Naga Control GUI is quit and other remappers are stopped.
- Exactly one transport is attached (wired cable or HyperSpeed receiver, never
  both); no buttons are held.
- Stop the Naga Control user service **before** touching OpenRazer, so no
  D-Bus reactivation surprises occur during daemon/driver work.

## 5. Cautious activation

- Do not reload kernel modules unattended. A reboot is the conservative
  activation option.
- Honest stamp note: the kernel module sources are unchanged by fix3, but the
  DKMS `PACKAGE_VERSION` stamp moves from fix2 to fix3, so a DKMS rebuild of
  an otherwise identical module baseline is expected. Reloading udev rules
  alone does not necessarily restore permissions on existing sysfs attributes.

## 6. Verify the install

- `cat /usr/share/doc/<each-package-name>/source-commit` returns the exact
  `OPENRAZER_COMMIT` SHA for all three packages.
- Installed versions read `3.12.1.pr2904.fix3-1` for the single cohort; no
  mixed fix2/fix3 inventory remains.
- The daemon restarts cleanly and the mouse is usable on the single attached
  transport.

## 7. Explicitly not claimed

- No physical validation of fix3 is passed by the package gate. Natural
  idle/wake without daemon restarts, physical button delivery, receiver
  reconnect, and responsive pointer behavior remain pending the attended
  hardware gate.
- The `naga-v3-pro-compat-v1` tag and the Naga Control published pin stay on
  fix2 until that attended acceptance passes. Moving the tag or the pin is
  out of scope for this upgrade.
