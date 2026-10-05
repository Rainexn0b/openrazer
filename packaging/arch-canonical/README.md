# Canonical Arch Packages

These local test packages build the driver, daemon, and Python client from the
same full commit on `test-pr-2904-edualb`. No working-tree overlays are used.

Commit and push the intended source revision to the fork before building. From
this directory, with a clean canonical checkout:

```bash
export OPENRAZER_COMMIT="$(git rev-parse HEAD)"
makepkg --verifysource
makepkg --cleanbuild --clean --log
makepkg --packagelist
```

`OPENRAZER_COMMIT` is mandatory and must be a full commit SHA. The Git source
pin is checked before the documented Arch `plugdev` to `openrazer` substitutions.
Each package installs its source SHA in
`/usr/share/doc/<package-name>/source-commit`. Preserve the recipe, build logs,
package archives and hashes, and `.BUILDINFO` for deployment provenance.

The DKMS wrapper selects LLVM for Clang-built kernels and the default compiler
otherwise. Matching kernel headers, DKMS, the relevant compilers, base-devel,
Git, python-setuptools, and the daemon test dependencies must be installed.
All 55 focused regression tests run during the package build.

Install all three packages together using their exact filenames through a
visible privileged terminal. Do not use the older mutable-overlay recovery
recipe or historical package globs. Coordinate the daemon restart and driver
reload, and verify loaded module source versions against the installed modules.
A reboot is the conservative activation option. Reloading udev rules alone
does not necessarily restore permissions on existing sysfs attributes.

Long-term hardware acceptance requires real idle/wake cycles without daemon
restarts, physical F17 down/up delivery, receiver reconnect, and responsive
pointer/dock behavior. Fake tests and a fresh daemon startup do not prove those
conditions.
