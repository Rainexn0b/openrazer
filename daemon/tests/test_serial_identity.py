# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit coverage for serial_identity guard and DeviceCollection."""

import errno
import unittest
import unittest.mock

from openrazer_daemon.device import DeviceCollection
from openrazer_daemon.hardware.device_base import DeviceNotReadyError
from openrazer_daemon.misc import serial_identity
from openrazer_daemon.misc.serial_identity import DuplicateDeviceError


FAKE_SERIAL_A = "SN000001"
FAKE_SERIAL_B = "SN000002"
FAKE_ID_A = "0003:1532:00E7.0001"
FAKE_ID_B = "0003:1532:00E8.0001"
FAKE_ID_C = "0003:1532:00E8.0002"


class FakeDbus:
    def __init__(self):
        self.registered = None

    def register_parent(self, parent):
        self.registered = parent


class FakeNagaClass:
    USB_VID = 0x1532
    USB_PID = 0x00E7


class FakeNagaWireless:
    USB_VID = 0x1532
    USB_PID = 0x00E8


class FakeOtherClass:
    USB_VID = 0x1532
    USB_PID = 0x00A7


class TrustworthyTest(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(serial_identity.is_trustworthy_naga_serial(FAKE_SERIAL_A))
        self.assertTrue(serial_identity.is_trustworthy_naga_serial("A"))
        self.assertTrue(serial_identity.is_trustworthy_naga_serial("A" * 22))

    def test_rejects_untrustworthy(self):
        self.assertFalse(serial_identity.is_trustworthy_naga_serial(""))
        self.assertFalse(serial_identity.is_trustworthy_naga_serial(None))
        self.assertFalse(serial_identity.is_trustworthy_naga_serial(123))
        self.assertFalse(serial_identity.is_trustworthy_naga_serial("UNKNOWN_153200E8_0000"))
        self.assertFalse(serial_identity.is_trustworthy_naga_serial("sn000001"))
        self.assertFalse(serial_identity.is_trustworthy_naga_serial("SN-0001"))
        self.assertFalse(serial_identity.is_trustworthy_naga_serial("A" * 23))


class ClassScopeTest(unittest.TestCase):
    def test_exact_naga(self):
        self.assertTrue(serial_identity.is_naga_v3_pro_class(FakeNagaClass))
        self.assertTrue(serial_identity.is_naga_v3_pro_class(FakeNagaWireless))
        self.assertFalse(serial_identity.is_naga_v3_pro_class(FakeOtherClass))
        self.assertFalse(serial_identity.is_naga_v3_pro_class(object()))

    def test_no_int_coercion(self):
        class StringPid:
            USB_VID = 0x1532
            USB_PID = "00E7"
        self.assertFalse(serial_identity.is_naga_v3_pro_class(StringPid))
        self.assertFalse(serial_identity.is_naga_v3_pro_class(unittest.mock.MagicMock()))

    def test_kwargs_only_for_naga(self):
        collection = DeviceCollection()
        kwargs = serial_identity.serial_guard_kwargs(FakeNagaClass, collection)
        self.assertIn("serial_guard", kwargs)
        self.assertEqual(serial_identity.serial_guard_kwargs(FakeOtherClass, collection), {})
        self.assertEqual(
            serial_identity.serial_guard_kwargs(unittest.mock.MagicMock(), collection), {}
        )
        self.assertEqual(serial_identity.serial_guard_kwargs(FakeNagaClass, None), {})


class GuardCallbackTest(unittest.TestCase):
    def test_rejects_untrustworthy_with_eagain(self):
        collection = DeviceCollection()
        guard = serial_identity.serial_guard_kwargs(FakeNagaClass, collection)["serial_guard"]
        for bad in ("UNKNOWN_153200E7_0000", "bad-serial", "sn000001"):
            with self.assertRaises(DeviceNotReadyError) as ctx:
                guard(bad)
            self.assertEqual(ctx.exception.errno, errno.EAGAIN)
            self.assertNotIn(bad, str(ctx.exception))
        with self.assertRaises(DeviceNotReadyError) as ctx:
            guard("")
        self.assertEqual(ctx.exception.errno, errno.EAGAIN)

    def test_rejects_duplicate_with_ebusy(self):
        collection = DeviceCollection()
        collection.add(FAKE_ID_A, FAKE_SERIAL_A, FakeDbus())
        guard = serial_identity.serial_guard_kwargs(FakeNagaWireless, collection)["serial_guard"]
        with self.assertRaises(DeviceNotReadyError) as ctx:
            guard(FAKE_SERIAL_A)
        self.assertEqual(ctx.exception.errno, errno.EBUSY)
        self.assertNotIn(FAKE_SERIAL_A, str(ctx.exception))

    def test_allows_distinct(self):
        collection = DeviceCollection()
        collection.add(FAKE_ID_A, FAKE_SERIAL_A, FakeDbus())
        guard = serial_identity.serial_guard_kwargs(FakeNagaWireless, collection)["serial_guard"]
        guard(FAKE_SERIAL_B)


class CollectionGuardTest(unittest.TestCase):
    def setUp(self):
        self.collection = DeviceCollection()

    def test_distinct_allowed(self):
        self.collection.add(FAKE_ID_A, FAKE_SERIAL_A, FakeDbus())
        self.collection.add(FAKE_ID_B, FAKE_SERIAL_B, FakeDbus())
        self.assertEqual(len(self.collection), 2)

    def test_duplicate_id_before_maps(self):
        self.collection.add(FAKE_ID_A, FAKE_SERIAL_A, FakeDbus())
        dbus = FakeDbus()
        with self.assertRaises(DuplicateDeviceError):
            self.collection.add(FAKE_ID_A, FAKE_SERIAL_B, dbus)
        self.assertIsNone(dbus.registered)
        self.assertEqual(len(self.collection), 1)

    def test_duplicate_serial_before_maps(self):
        self.collection.add(FAKE_ID_A, FAKE_SERIAL_A, FakeDbus())
        dbus = FakeDbus()
        with self.assertRaises(DuplicateDeviceError) as ctx:
            self.collection.add(FAKE_ID_B, FAKE_SERIAL_A, dbus)
        self.assertNotIn(FAKE_SERIAL_A, str(ctx.exception))
        self.assertIsNone(dbus.registered)
        self.assertIs(self.collection[FAKE_ID_A], self.collection[FAKE_SERIAL_A])

    def test_remove_readd_no_orphan(self):
        self.collection.add(FAKE_ID_A, FAKE_SERIAL_A, FakeDbus())
        with self.assertRaises(DuplicateDeviceError):
            self.collection.add(FAKE_ID_B, FAKE_SERIAL_A, FakeDbus())
        self.collection.remove(FAKE_ID_A)
        self.assertEqual(len(self.collection), 0)
        self.collection.add(FAKE_ID_B, FAKE_SERIAL_A, FakeDbus())
        self.assertIs(self.collection[FAKE_ID_B], self.collection[FAKE_SERIAL_A])

    def test_message_has_no_sensitive(self):
        self.collection.add(FAKE_ID_A, FAKE_SERIAL_A, FakeDbus())
        with self.assertRaises(DuplicateDeviceError) as ctx:
            self.collection.add(FAKE_ID_C, FAKE_SERIAL_A, FakeDbus())
        message = str(ctx.exception)
        self.assertNotIn(FAKE_SERIAL_A, message)


if __name__ == "__main__":
    unittest.main()
