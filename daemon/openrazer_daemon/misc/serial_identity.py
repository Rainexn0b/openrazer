# SPDX-License-Identifier: GPL-2.0-or-later
"""
Collection-scoped single-owner guard for shared-serial Naga routes.

The wired (1532:00E7) and wireless (1532:00E8) transports of one Naga V3 Pro
report the same serial. OpenRazer uses the serial as the D-Bus object path
and lookup identity, so only one route may own a serial at a time. The daemon
passes a serial_guard callback into real Naga construction; the callback runs
immediately after the cached serial and before any mode write, background
service, or D-Bus registration. Untrustworthy Naga serials and duplicates
already present in the daemon's DeviceCollection are deferred as retryable
errors through the existing cancellable machinery.
"""

import errno
import re

NAGA_VID = 0x1532
NAGA_V3_PRO_WIRED_PID = 0x00E7
NAGA_V3_PRO_WIRELESS_PID = 0x00E8

UNKNOWN_PREFIX = "UNKNOWN_"
_SERIAL_RE = re.compile(r"[A-Z0-9]+")
_MAX_SERIAL_LEN = 22


class DuplicateDeviceError(ValueError):
    """Raised when a device ID or serial is already owned."""


def is_trustworthy_naga_serial(serial) -> bool:
    """Return True for a hardware-plausible Naga serial."""
    if not isinstance(serial, str):
        return False
    if len(serial) == 0 or len(serial) > _MAX_SERIAL_LEN:
        return False
    if serial.startswith(UNKNOWN_PREFIX):
        return False
    return _SERIAL_RE.fullmatch(serial) is not None


def is_naga_v3_pro_class(device_class) -> bool:
    """Return True only for the exact in-scope Naga V3 Pro classes."""
    vid = getattr(device_class, "USB_VID", None)
    pid = getattr(device_class, "USB_PID", None)
    if vid != NAGA_VID:
        return False
    return pid == NAGA_V3_PRO_WIRED_PID or pid == NAGA_V3_PRO_WIRELESS_PID


def serial_guard_kwargs(device_class, collection):
    """Return construction kwargs for real Naga classes, else empty."""
    if not is_naga_v3_pro_class(device_class):
        return {}
    if collection is None:
        return {}

    def serial_guard(serial):
        # Lazy import avoids a device_base -> serial_identity cycle.
        from openrazer_daemon.hardware.device_base import DeviceNotReadyError
        if not is_trustworthy_naga_serial(serial):
            raise DeviceNotReadyError(errno.EAGAIN, "Device serial not ready")
        if serial in collection:
            raise DeviceNotReadyError(errno.EBUSY, "Device serial already owned")

    return {"serial_guard": serial_guard}
