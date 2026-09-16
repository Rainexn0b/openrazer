# SPDX-License-Identifier: GPL-2.0-or-later

"""
Watches a mouse's input event files for activity/idle transitions.

Some wireless mice (e.g. the Naga V3 Pro) stop responding to USB control
transfers while idle, so any hardware write attempted during that time (like
setting "driver mode") raises a TimeoutError. This module is used to:

* Block device initialisation until the mouse produces its first input event,
  so the driver doesn't try to talk to a sleeping device.
* Watch for the mouse waking up from its own idle state at runtime and
  re-apply "driver mode", since some devices silently drop back to "device
  mode" while idle.
"""
import fcntl
import logging
import os
import select
import threading
import time

POLL_INTERVAL = 0.5


def find_event_files(device_path, event_file_regex, testing=False):
    """
    Find event files matching event_file_regex

    :param device_path: Device path
    :type device_path: str

    :param event_file_regex: Compiled regex to match event file names against
    :type event_file_regex: re.Pattern

    :param testing: If true, look in device_path/input instead of /dev/input/by-id/
    :type testing: bool

    :return: List of matching event file paths
    :rtype: list
    """
    if testing:
        search_dir = os.path.join(device_path, 'input')
    else:
        search_dir = '/dev/input/by-id/'

    event_files = []
    if event_file_regex is not None and os.path.exists(search_dir):
        for event_file in os.listdir(search_dir):
            if event_file_regex.match(event_file) is not None:
                event_files.append(os.path.join(search_dir, event_file))

    return event_files


def _open_nonblocking(event_files):
    open_files = []
    for event_file in event_files:
        try:
            event_fh = open(event_file, 'rb')
        except OSError:
            continue

        flags = fcntl.fcntl(event_fh.fileno(), fcntl.F_GETFL)
        fcntl.fcntl(event_fh.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
        open_files.append(event_fh)

    return open_files


def wait_for_activity(event_files, device_name):
    """
    Block until activity is seen on any of event_files.

    Logs once that the device is idle, and returns as soon as an event arrives.
    Returns immediately if none of event_files could be opened.

    :param event_files: List of event file paths to watch
    :type event_files: list

    :param device_name: Device name, used for the idle log message
    :type device_name: str
    """
    logger = logging.getLogger('razer.misc.mousemonitor')

    open_files = _open_nonblocking(event_files)
    if not open_files:
        return

    logged_idle = False
    try:
        while True:
            ready, _, _ = select.select(open_files, [], [], POLL_INTERVAL)
            if ready:
                break

            if not logged_idle:
                logger.info("%s in idle state", device_name)
                logged_idle = True
    finally:
        for event_fh in open_files:
            event_fh.close()


class MouseMonitor(threading.Thread):
    """
    Thread that watches a mouse's input event files, and once the device
    wakes up after being idle for longer than its configured idle time,
    re-applies "driver mode" if the device is meant to be in it.
    """

    def __init__(self, device_id, event_files, parent):
        super().__init__()

        self._logger = logging.getLogger('razer.device{0}.mousemonitor'.format(device_id))
        self._parent = parent
        self._shutdown = False

        self._open_event_files = _open_nonblocking(event_files)
        self._last_activity = time.time()
        self._idle = False

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
        try:
            with open(self._parent.get_driver_path('device_idle_time'), 'r') as driver_file:
                return int(driver_file.read().strip())
        except (OSError, ValueError):
            return 0

    def run(self):
        if not self._open_event_files:
            return

        device_name = self._parent.__class__.__name__

        while not self._shutdown:
            ready, _, _ = select.select(self._open_event_files, [], [], POLL_INTERVAL)

            if ready: 
                for event_fh in ready:
                    try:
                        # drain buffered event data
                        while event_fh.read(4096):
                            pass
                    except OSError:
                        pass

                self._last_activity = time.time()

                if self._idle:
                    self._idle = False
                    self._logger.info("%s woke up from idle state", device_name)

                    if self._parent.DRIVER_MODE:
                        try:
                            self._logger.info('Re-applying "driver mode" after idle')
                            self._parent.set_device_mode(0x03, 0x00)
                        except OSError:
                            self._logger.exception('Failed to re-apply "driver mode" after idle')
                continue

            if self._idle:
                continue

            idle_time = self._get_idle_time()
            if idle_time and (time.time() - self._last_activity) > idle_time:
                self._idle = True
                self._logger.info("%s in idle state", device_name)

        for event_fh in self._open_event_files:
            event_fh.close()
