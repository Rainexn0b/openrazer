# SPDX-License-Identifier: GPL-2.0-or-later

"""Compile the real keyboard probe/teardown with fakes, never kernel or USB I/O."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


STUBS = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define GFP_KERNEL 0
#define HID_CONNECT_DEFAULT 0
#define USB_INTERFACE_PROTOCOL_KEYBOARD 1
#define USB_INTERFACE_PROTOCOL_MOUSE 2
#define fallthrough __attribute__((__fallthrough__))
#define DECLARE_BITMAP(name, size) unsigned long name[1]
#define hid_err(...) ((void)0)
#define hid_info(...) ((void)0)
#define mutex_init(lock) ((lock)->initialized = 1)
#define kzalloc_obj(value) fake_kzalloc(sizeof(value))
#define devm_kzalloc(dev, size, flags) fake_devm_kzalloc(size)
#define to_usb_interface(dev) ((struct usb_interface *)(dev))
#define interface_to_usbdev(intf) ((intf)->usb)
#define hid_to_usb_dev(hdev) interface_to_usbdev(to_usb_interface((hdev)->dev.parent))

struct mutex { int initialized; };
struct device { struct device *parent; void *driver_data; };
struct hid_device { struct device dev; };
struct hid_device_id { int unused; };
struct usb_device {
    struct device dev;
    struct { unsigned short idVendor, idProduct; } descriptor;
};
struct usb_host_interface { struct { int bInterfaceProtocol; } desc; };
struct usb_interface {
    struct device dev;
    struct usb_host_interface *cur_altsetting;
    struct usb_device *usb;
};
struct device_attribute { int index; };
"""

FAKES = r"""
static struct hid_device *current_hdev;
static void *private_data;
static bool present[ATTR_COUNT], started, allow_missing;
static int alloc_calls, create_calls, mode_calls, parse_calls, start_calls;
static int stop_calls, autosuspend_calls, cases;
static int fail_alloc, fail_create, create_error, mode_error, parse_error, start_error;

static void *dev_get_drvdata(struct device *dev) { return dev->driver_data; }
static void dev_set_drvdata(struct device *dev, void *data) { dev->driver_data = data; }
static void *hid_get_drvdata(struct hid_device *hdev) { return dev_get_drvdata(&hdev->dev); }

static void check_no_attributes(void)
{
    for (int i = 0; i < ATTR_COUNT; ++i) {
        if (present[i])
            fprintf(stderr, "pid=%04x protocol=%d attr=%s still present\n",
                    hid_to_usb_dev(current_hdev)->descriptor.idProduct,
                    to_usb_interface(current_hdev->dev.parent)->cur_altsetting->desc.bInterfaceProtocol,
                    attr_names[i]);
        assert(!present[i]);
    }
}

static void check_callback_data(struct device *device)
{
    struct razer_kbd_device *dev = dev_get_drvdata(device);
    struct usb_device *usb = hid_to_usb_dev(current_hdev);
    struct usb_interface *intf = to_usb_interface(current_hdev->dev.parent);
    assert(dev && dev == private_data && dev->lock.initialized);
    assert(dev->hdev == current_hdev);
    assert(dev->usb_vid == usb->descriptor.idVendor);
    assert(dev->usb_pid == usb->descriptor.idProduct);
    assert(dev->usb_interface_protocol == intf->cur_altsetting->desc.bInterfaceProtocol);
    assert(dev_get_drvdata(&usb->dev));
}

static void hid_set_drvdata(struct hid_device *hdev, void *data)
{
    if (!data) {
        check_no_attributes();
        assert(!started);
    }
    dev_set_drvdata(&hdev->dev, data);
}

static void *fake_kzalloc(size_t size)
{
    if (++alloc_calls == fail_alloc)
        return NULL;
    assert(!private_data);
    private_data = calloc(1, size);
    assert(private_data);
    return private_data;
}

static void *fake_devm_kzalloc(size_t size)
{
    if (++alloc_calls == fail_alloc)
        return NULL;
    void *data = calloc(1, size);
    assert(data);
    return data;
}

static void kfree(void *data)
{
    check_no_attributes();
    assert(!started && !hid_get_drvdata(current_hdev));
    assert(data && data == private_data);
    free(data);
    private_data = NULL;
}

static int device_create_file(struct device *dev, const struct device_attribute *attr)
{
    /* Exercise callback access as soon as an attribute can become visible. */
    check_callback_data(dev);
    if (++create_calls == fail_create)
        return create_error;
    assert(!present[attr->index]);
    present[attr->index] = true;
    check_callback_data(dev);
    return 0;
}

static void device_remove_file(struct device *dev, const struct device_attribute *attr)
{
    check_callback_data(dev);
    assert(allow_missing || present[attr->index]);
    present[attr->index] = false;
}

static int razer_set_device_mode(struct razer_kbd_device *dev, int mode, int param)
{
    check_callback_data(&dev->hdev->dev);
    assert(mode == 0 && param == 0);
    ++mode_calls;
    return mode_error;
}

static int hid_parse(struct hid_device *hdev)
{
    check_callback_data(&hdev->dev);
    ++parse_calls;
    return parse_error;
}

static int hid_hw_start(struct hid_device *hdev, int flags)
{
    check_callback_data(&hdev->dev);
    assert(flags == HID_CONNECT_DEFAULT && parse_calls == 1);
    ++start_calls;
    /* A failed hid_hw_start unwinds its own low-level start/connect. */
    started = !start_error;
    return start_error;
}

static void hid_hw_stop(struct hid_device *hdev)
{
    check_no_attributes();
    check_callback_data(&hdev->dev);
    assert(started);
    started = false;
    ++stop_calls;
}

static void usb_disable_autosuspend(struct usb_device *usb)
{
    assert(usb == hid_to_usb_dev(current_hdev) && started);
    ++autosuspend_calls;
}
"""

HARNESS = r"""
static int run_case(unsigned short pid, int protocol, bool shared,
                    int allocation_failure, int attribute_failure, int attribute_error,
                    int mode_failure, int parse_failure, int start_failure, int expected)
{
    struct usb_device usb = { .descriptor = { .idVendor = 0x1532, .idProduct = pid } };
    struct usb_host_interface alt = { .desc = { .bInterfaceProtocol = protocol } };
    struct usb_interface intf = { .cur_altsetting = &alt, .usb = &usb };
    struct hid_device hdev = { .dev = { .parent = &intf.dev } };
    void *shared_data = shared ? calloc(1, sizeof(struct razer_kbd_usb_device_data)) : NULL;
    usb.dev.driver_data = shared_data;
    current_hdev = &hdev;
    memset(present, 0, sizeof(present));
    assert(!private_data && !started);
    alloc_calls = create_calls = mode_calls = parse_calls = start_calls = 0;
    stop_calls = autosuspend_calls = 0;
    fail_alloc = allocation_failure;
    fail_create = attribute_failure;
    create_error = attribute_error;
    mode_error = mode_failure;
    parse_error = parse_failure;
    start_error = start_failure;
    allow_missing = expected != 0;

    int result = razer_kbd_probe(&hdev, NULL);
    if (result != expected) {
        fprintf(stderr, "pid=%04x protocol=%d shared=%d alloc=%d attr=%d: got %d, expected %d\n",
                pid, protocol, shared, fail_alloc, fail_create, result, expected);
        abort();
    }
    if (!result) {
        check_callback_data(&hdev.dev);
        assert(parse_calls == 1 && start_calls == 1 && stop_calls == 0);
        assert(autosuspend_calls == !is_blade_laptop(hid_get_drvdata(&hdev)));
        assert(mode_calls == (protocol == USB_INTERFACE_PROTOCOL_MOUSE &&
                              pid != USB_DEVICE_ID_RAZER_TARTARUS_PRO));
        razer_kbd_disconnect(&hdev);
        assert(stop_calls == 1);
    } else {
        assert(stop_calls == 0 && autosuspend_calls == 0);
        if (allocation_failure || attribute_failure)
            assert(mode_calls == 0 && parse_calls == 0 && start_calls == 0);
        else if (mode_failure)
            assert(mode_calls == 1 && parse_calls == 0 && start_calls == 0);
        else if (parse_failure)
            assert(parse_calls == 1 && start_calls == 0);
        else if (start_failure)
            assert(parse_calls == 1 && start_calls == 1);
    }
    check_no_attributes();
    assert(!private_data && !hid_get_drvdata(&hdev) && !started);
    if (shared)
        assert(usb.dev.driver_data == shared_data);
    else if (allocation_failure)
        assert(!usb.dev.driver_data);
    else
        assert(usb.dev.driver_data);
    free(usb.dev.driver_data);  /* Only the fake USB-device owner releases shared data. */
    ++cases;
    return create_calls;
}

int main(void)
{
    const int errors[] = { -EACCES, -ENOSPC, -EEXIST };
    for (unsigned int p = 0; p < sizeof(pids) / sizeof(pids[0]); ++p) {
        for (int protocol = 0; protocol <= USB_INTERFACE_PROTOCOL_MOUSE; ++protocol) {
            for (int shared = 0; shared <= 1; ++shared) {
                int count = run_case(pids[p], protocol, shared, 0, 0, 0, 0, 0, 0, 0);
                for (int attr = 1; attr <= count; ++attr) {
                    int error = errors[attr % 3];
                    assert(run_case(pids[p], protocol, shared, 0, attr, error,
                                    0, 0, 0, error) == attr);
                }
                run_case(pids[p], protocol, shared, 1, 0, 0, 0, 0, 0, -ENOMEM);
                if (!shared)
                    run_case(pids[p], protocol, false, 2, 0, 0, 0, 0, 0, -ENOMEM);
                if (protocol == USB_INTERFACE_PROTOCOL_MOUSE &&
                    pids[p] != USB_DEVICE_ID_RAZER_TARTARUS_PRO)
                    run_case(pids[p], protocol, shared, 0, 0, 0, -ETIMEDOUT, 0, 0, -ETIMEDOUT);
                run_case(pids[p], protocol, shared, 0, 0, 0, 0, -EINVAL, 0, -EINVAL);
                run_case(pids[p], protocol, shared, 0, 0, 0, 0, 0, -EIO, -EIO);
            }
        }
    }
    printf("%d fake keyboard probe/teardown cases passed\n", cases);
    return 0;
}
"""


class KeyboardProbeTest(unittest.TestCase):
    def test_probe_failure_unwind_and_disconnect(self):
        clang = shutil.which('clang')
        if clang is None:
            self.skipTest('Clang is required for the fake keyboard probe regression')

        driver = Path(__file__).resolve().parents[2] / 'driver'
        source = (driver / 'razerkbd_driver.c').read_text()
        header = (driver / 'razerkbd_driver.h').read_text()
        common = (driver / 'razercommon.h').read_text()
        begin = source.index('static void razer_kbd_init(')
        end = source.index('/**\n * Setup input device keybit mask', begin)
        probe = source[begin:end]
        laptop = re.search(r'static bool is_blade_laptop\([^\n]*\n\{.*?\n\}', source, re.S).group()
        structs = re.findall(r'struct razer_kbd_(?:device|usb_device_data) \{.*?\n\};', header, re.S)
        pids = re.findall(r'^#define (USB_DEVICE_ID_RAZER_\w+) (0x[0-9A-Fa-f]+)', header, re.M)
        attrs = sorted(set(re.findall(r'\bdev_attr_\w+', probe)))
        macro = re.search(r'^#define CREATE_DEVICE_FILE[^\n]*\n(?:[^\n]*\\\n)*[^\n]*', common, re.M).group()
        definitions = '\n'.join(f'#define {name} {value}' for name, value in pids)
        definitions += f'\n#define ATTR_COUNT {len(attrs)}\n'
        definitions += '\n'.join(f'static const struct device_attribute {name} = {{ {i} }};'
                                 for i, name in enumerate(attrs))
        definitions += '\nstatic const char *attr_names[] = { '
        definitions += ', '.join(f'"{name}"' for name in attrs) + ' };\n'
        definitions += '\nstatic const unsigned short pids[] = { '
        definitions += ', '.join(name for name, _ in pids) + ', 0xffff };\n'
        translation_unit = '\n'.join((STUBS, *structs, definitions, FAKES, laptop, macro,
                                      f'#line {source[:begin].count(chr(10)) + 1} "razerkbd_driver.c"',
                                      probe, '#line 1 "keyboard_probe_fake.c"', HARNESS))

        with tempfile.TemporaryDirectory(prefix='openrazer-keyboard-probe-') as temporary:
            c_file = Path(temporary) / 'keyboard_probe_fake.c'
            executable = Path(temporary) / 'keyboard_probe_fake'
            c_file.write_text(translation_unit)
            compile_result = subprocess.run(
                [clang, '-std=gnu11', '-O2', '-Wall', '-Werror', '-Wuninitialized',
                 '-Wsometimes-uninitialized', '-fsanitize=address,undefined',
                 str(c_file), '-o', str(executable)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r'\d+ fake keyboard probe/teardown cases passed')


if __name__ == '__main__':
    unittest.main()
