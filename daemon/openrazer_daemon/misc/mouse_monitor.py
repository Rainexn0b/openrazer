# SPDX-License-Identifier: GPL-2.0-or-later

"""
Watches a mouse's activity/idle transitions.

Some wireless mice (e.g. the Naga V3 Pro) stop responding to USB control
transfers while idle, so any hardware write attempted during that time (like
setting "driver mode") raises a TimeoutError.

Activity is read from the driver's "device_last_activity" file, which reports
how long ago the kernel driver last saw an input report from the mouse.
This module is used for:

* Block device initialisation until the mouse produces its first input report,
  so the driver doesn't try to talk to a sleeping device.
* Watch for the mouse waking up from its own idle state at runtime and
  re-apply "driver mode", since some devices silently drop back to "device
  mode" while idle.
"""
import logging
import os
import threading
import time

POLL_INTERVAL = 0.2
IDLE_TIME_REFRESH = 30.0
NO_ACTIVITY = -1


def _read_last_activity(last_activity_path):
    """
    Read the driver's "device_last_activity" file.

    :param last_activity_path: Path of the driver's device_last_activity file
    :type last_activity_path: str

    :return: Milliseconds since the device's last input report, NO_ACTIVITY if
             it hasn't reported anything yet, or None if the file is unreadable
    :rtype: int
    """
    try:
        with open(last_activity_path, 'r') as driver_file:
            return int(driver_file.read().strip())
    except (OSError, ValueError):
        return NO_ACTIVITY


def wait_for_activity(device_path, device_name):
    """
    Block until the mouse has reported at least one input event.

    Logs once that the device is idle, and returns as soon as the driver has
    seen an input report. Returns immediately if the driver doesn't expose
    "device_last_activity".

    :param device_path: Device path
    :type device_path: str

    :param device_name: Device name, used for the idle log message
    :type device_name: str
    """
    logger = logging.getLogger('razer.misc.mousemonitor')
    last_activity_path = os.path.join(device_path, 'device_last_activity')


    logged_idle = False
    while True:
        last_activity = _read_last_activity(last_activity_path)
        if last_activity != NO_ACTIVITY:
            return

        if not logged_idle:
            logger.info("%s in idle state", device_name)
            logged_idle = True

        time.sleep(POLL_INTERVAL)


class MouseMonitor(threading.Thread):
    """
    Thread that watches the driver's "device_last_activity" file, and once the
    device wakes up after being idle for longer than its configured idle time,
    re-applies "driver mode" if the device is meant to be in it.
    """

    def __init__(self, device_id, parent):
        super().__init__()

        self._logger = logging.getLogger('razer.device{0}.mousemonitor'.format(device_id))
        self._parent = parent
        self._shutdown = False

        self._last_sample = None
        self._idle = False
        self._pending_driver_mode = False
        self._idle_time = 0
        self._idle_time_read = 0.0

    @property
    def shutdown(self):
        """
        Thread shutdown condition
        """
        return self._shutdown

    @shutdown.setter
    def shutdown(self, value):
        self._shutdown = value

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
                self._idle_time = int(driver_file.read().strip())
        except (OSError, ValueError):
            self._idle_time = 0

        self._idle_time_read = now
        return self._idle_time

    def _apply_driver_mode(self):
        """
        Re-apply "driver mode".

        The device can still be asleep right after it starts reporting again,
        in which case the write times out. Keep it pending so the next poll
        tries again instead of leaving the device in "device mode".
        """
        try:
            self._parent.set_device_mode(0x03, 0x00)
        except OSError as error:
            self._logger.debug('Device not ready for "driver mode" yet: %s', error)
            return

        self._pending_driver_mode = False
        self._logger.info('Re-applied "driver mode" after idle')

    def run(self):
        device_name = self._parent.__class__.__name__

        while not self._shutdown:
            time.sleep(POLL_INTERVAL)

            sample = _read_last_activity(self._parent.get_driver_path('device_last_activity'))
            if sample == NO_ACTIVITY:
                continue

            activity = self._last_sample is None or sample < self._last_sample
            self._last_sample = sample

            if activity and self._idle:
                self._idle = False
                self._pending_driver_mode = self._parent.DRIVER_MODE
                self._logger.info("%s woke up from idle state", device_name)

            if self._pending_driver_mode:
                self._apply_driver_mode()

            if activity:
                continue

            if self._idle:
                continue

            idle_time = self._get_idle_time()
            if idle_time and sample > idle_time * 1000:
                self._idle = True
                self._logger.info("%s in idle state", device_name)
