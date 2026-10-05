# SPDX-License-Identifier: GPL-2.0-or-later

"""
Verifies driver mode while a mouse is active.

Some wireless mice (e.g. the Naga V3 Pro) stop responding to USB control
transfers while idle, so any hardware write attempted during that time (like
setting "driver mode") raises a TimeoutError.

Right after waking up, the receiver can also answer commands with an empty
"busy" response for a while. The driver reports those as success, so reads
return empty data (e.g. an empty serial number) and writes are silently
dropped.

Activity is read from the driver's "device_last_activity" file, which reports
how long ago the kernel driver last saw an input report from the mouse.
This module provides a one-shot readiness check for asynchronous device
discovery and verifies "driver mode" during recent input activity. Some devices
silently drop back to "device mode" while idle, and receiver status reports can
hide the idle transition from the activity timestamp.
"""
import logging
import os
import re
import threading
import time

POLL_INTERVAL = 0.2
IDLE_TIME_REFRESH = 30.0
MODE_CHECK_INTERVAL = 2.0
ACTIVE_WINDOW = 5000
NO_ACTIVITY = -1


def _read_last_activity(last_activity_path):
    """
    Read the driver's "device_last_activity" file.

    :param last_activity_path: Path of the driver's device_last_activity file
    :type last_activity_path: str

    :return: Milliseconds since the device's last input report, NO_ACTIVITY if
             it hasn't reported anything yet or the file is unreadable
    :rtype: int
    """
    try:
        with open(last_activity_path, 'r') as driver_file:
            return int(driver_file.read().strip())
    except (OSError, ValueError):
        return NO_ACTIVITY


def is_device_serial_ready(device_path):
    """
    Check whether the device answers control transfers with real data.

    A "busy" response from the receiver carries no data, so reading the serial
    number tells it apart from a real answer.

    :param device_path: Device path
    :type device_path: str

    :return: True if the device returned a valid serial number
    :rtype: bool
    """
    try:
        with open(os.path.join(device_path, 'device_serial'), 'r') as driver_file:
            serial = driver_file.read().strip()
    except (OSError, UnicodeDecodeError):
        return False

    return re.fullmatch(r"[\dA-Z]+", serial) is not None


class MouseMonitor(threading.Thread):
    """
    Thread that verifies the requested driver mode during recent activity.

    Mode checks are rate-limited and do not depend on observing an idle
    transition. An explicit firmware-mode request disables recovery.
    """

    def __init__(self, device_id, parent):
        super().__init__()

        self._logger = logging.getLogger('razer.device{0}.mousemonitor'.format(device_id))
        self._parent = parent
        self._shutdown = threading.Event()

        self._idle = False
        self._next_mode_check = 0.0
        self._idle_time = 0
        self._idle_time_read = -IDLE_TIME_REFRESH

    @property
    def shutdown(self):
        """
        Thread shutdown condition
        """
        return self._shutdown.is_set()

    @shutdown.setter
    def shutdown(self, value):
        # Serialize shutdown with mode I/O, but never hold this lock while
        # joining the monitor thread.
        with self._parent._device_mode_lock:
            if value:
                self._shutdown.set()
            else:
                self._shutdown.clear()

    def _get_idle_time(self):
        """
        Read the device's configured idle time, in seconds.

        Reading it costs a USB control transfer, so the value is cached and
        only refreshed every IDLE_TIME_REFRESH seconds.
        """
        now = time.monotonic()
        if now - self._idle_time_read < IDLE_TIME_REFRESH:
            return self._idle_time

        try:
            with open(self._parent.get_driver_path('device_idle_time'), 'r') as driver_file:
                idle_time = int(driver_file.read().strip())
            # A busy receiver can return zero. Keep the last valid timeout.
            if idle_time > 0:
                self._idle_time = idle_time
        except (OSError, ValueError) as error:
            self._logger.debug('Could not refresh idle timeout: %s', error)

        self._idle_time_read = now
        return self._idle_time

    def _apply_driver_mode(self):
        """
        Verify "driver mode", retrying on a later active check if necessary.

        A successful write can be silently dropped, so only a matching
        readback confirms recovery. The shared lock protects explicit mode
        requests from being overwritten by a stale recovery attempt.
        """
        with self._parent._device_mode_lock:
            if self.shutdown or not self._parent.DRIVER_MODE:
                return

            # A slow resume or explicit request may have delayed the lock.
            sample = _read_last_activity(self._parent.get_driver_path('device_last_activity'))
            if sample == NO_ACTIVITY or sample > ACTIVE_WINDOW:
                return
            if not is_device_serial_ready(self._parent._device_path):
                return

            self._get_idle_time()
            try:
                if self._parent.get_device_mode() == '3:0':
                    return
                self._parent.set_device_mode(0x03, 0x00)
                if self._parent.get_device_mode() != '3:0':
                    self._logger.debug('"Driver mode" write did not verify; will retry during activity')
                    return
            except (OSError, IndexError, ValueError) as error:
                self._logger.debug('Device not ready for "driver mode" yet: %s', error)
                return

            self._logger.info('Verified "driver mode" restoration during activity')

    def run(self):
        device_name = self._parent.__class__.__name__

        while not self._shutdown.wait(POLL_INTERVAL):
            sample = _read_last_activity(self._parent.get_driver_path('device_last_activity'))
            if sample == NO_ACTIVITY:
                continue

            if sample > ACTIVE_WINDOW:
                if not self._idle and self._idle_time and sample > self._idle_time * 1000:
                    self._idle = True
                    self._logger.info("%s in idle state", device_name)
                continue

            self._idle = False
            now = time.monotonic()
            if now >= self._next_mode_check:
                self._next_mode_check = now + MODE_CHECK_INTERVAL
                self._apply_driver_mode()
