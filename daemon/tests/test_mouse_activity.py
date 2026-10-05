# SPDX-License-Identifier: GPL-2.0-or-later

"""Exercise the real mouse activity/lifetime code with fake HID and pthreads."""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


STUBS = r"""
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

#define USB_INTERFACE_PROTOCOL_KEYBOARD 1
#define USB_INTERFACE_PROTOCOL_MOUSE 2
#define HID_CONNECT_DEFAULT 0
#define KERNEL_VERSION(a, b, c) (((a) << 16) | ((b) << 8) | (c))
#define LINUX_VERSION_CODE KERNEL_VERSION(6, 14, 0)
#define fallthrough __attribute__((__fallthrough__))
#define hid_err(...) ((void)0)
#define hid_info(...) ((void)0)
#define to_usb_interface(dev) ((struct usb_interface *)(dev))
#define interface_to_usbdev(intf) ((intf)->usb)
#define kzalloc_obj(value) fake_kzalloc(sizeof(value))
#define DEFINE_SPINLOCK(name) pthread_mutex_t name = PTHREAD_MUTEX_INITIALIZER
#define spin_lock_irqsave(lock, flags) do { (flags) = 0; fake_spin_lock(lock); } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(flags); fake_spin_unlock(lock); } while (0)
#define WRITE_ONCE(target, value) fake_write(&(target), value)
#define READ_ONCE(target) fake_read(&(target))
#define smp_store_release(target, value) fake_store(target, value)
#define smp_load_acquire(target) fake_load(target)
#define jiffies_to_msecs(value) ((unsigned int)(value))

typedef uint8_t u8;
typedef int32_t __s32;
struct mutex { int unused; };
struct hrtimer { int unused; };
struct input_dev { int unused; };
struct bus_type { int unused; };
struct device {
    struct device *parent, *child;
    const struct bus_type *bus;
    void *driver_data;
    atomic_int refs;
};
struct hid_device { struct device dev; };
struct hid_device_id { int unused; };
struct hid_report { int id; };
struct device_attribute { int index; };
struct usb_host_interface {
    struct { unsigned char bInterfaceProtocol, bInterfaceSubClass; } desc;
};
struct usb_interface {
    struct device dev;
    struct usb_host_interface *cur_altsetting;
    struct usb_device *usb;
};
struct usb_device {
    struct { unsigned short idVendor, idProduct; } descriptor;
    struct { struct { int bNumInterfaces; } desc; } *actconfig;
    struct usb_interface *interfaces[2];
};
"""

FAKES = r"""
static ssize_t razer_attr_read_device_last_activity(struct device *, struct device_attribute *, char *);
static void razer_note_activity(struct hid_device *, struct hid_report *, unsigned char, u8 *, int);
static struct hid_device *mouse_hdev, *keyboard_hdev;
static void *private_data;
static unsigned long jiffies = 1000;
static bool activity_present, started, probe_activity;
static int parse_error, start_error, minimum_writes;
static atomic_int writes, device_gets, device_puts;
static atomic_bool freed;
static _Thread_local bool locked, callback, unbinder;
static pthread_mutex_t schedule = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t changed = PTHREAD_COND_INITIALIZER;
enum { NO_PAUSE, HID_LOOKUP, PRIVATE_LOOKUP };
static int pause_at, sysfs_active;
static bool paused, resumed, retiring, draining;

static void pause_callback(int point)
{
    if (!callback || pause_at != point)
        return;
    pthread_mutex_lock(&schedule);
    paused = true;
    pthread_cond_broadcast(&changed);
    while (!resumed)
        pthread_cond_wait(&changed, &schedule);
    pthread_mutex_unlock(&schedule);
}

static void fake_spin_lock(pthread_mutex_t *lock)
{
    assert(!locked);
    if (unbinder) {
        pthread_mutex_lock(&schedule);
        retiring = true;
        pthread_cond_broadcast(&changed);
        pthread_mutex_unlock(&schedule);
    }
    pthread_mutex_lock(lock);
    locked = true;
}

static void fake_spin_unlock(pthread_mutex_t *lock)
{
    assert(locked);
    locked = false;
    pthread_mutex_unlock(lock);
}

static void *dev_get_drvdata(struct device *dev)
{
    if (callback) {
        assert(locked);
        pause_callback(PRIVATE_LOOKUP);
    }
    return dev->driver_data;
}

static void hid_set_drvdata(struct hid_device *hdev, void *data)
{
    assert(locked);
    if (!data)
        assert(atomic_load(&writes) >= minimum_writes);
    hdev->dev.driver_data = data;
}

static void *hid_get_drvdata(struct hid_device *hdev) { return dev_get_drvdata(&hdev->dev); }
static void fake_write(unsigned long *target, unsigned long value) { assert(locked); *target = value; }
static unsigned long fake_read(unsigned long *target) { assert(locked); return *target; }
static bool fake_load(bool *target) { assert(locked); return *target; }
static void fake_store(bool *target, bool value)
{
    assert(locked);
    *target = value;
    atomic_fetch_add(&writes, 1);
}

static struct usb_interface *usb_ifnum_to_if(struct usb_device *usb, int index)
{
    assert(!locked);
    return usb->interfaces[index];
}

static struct device *device_find_child(struct device *parent, void *data,
                                      int (*match)(struct device *, const void *))
{
    assert(!locked);
    struct device *dev = parent->child;
    if (!dev || !match(dev, data))
        return NULL;
    atomic_fetch_add(&dev->refs, 1);
    atomic_fetch_add(&device_gets, 1);
    pause_callback(HID_LOOKUP);
    return dev;
}

static void put_device(struct device *dev)
{
    assert(!locked && atomic_fetch_sub(&dev->refs, 1) > 1);
    atomic_fetch_add(&device_puts, 1);
}

static void *memchr_inv(const void *data, int value, size_t size)
{
    const u8 *bytes = data;
    for (size_t i = 0; i < size; ++i)
        if (bytes[i] != value)
            return (void *)(bytes + i);
    return NULL;
}

static ssize_t sysfs_emit(char *buf, const char *format, ...)
{
    assert(!locked);
    va_list args;
    va_start(args, format);
    int count = vsnprintf(buf, 64, format, args);
    va_end(args);
    return count;
}

static void *fake_kzalloc(size_t size)
{
    assert(!locked && !private_data);
    private_data = calloc(1, size);
    assert(private_data);
    return private_data;
}

static void kfree(void *data)
{
    assert(!locked && data == private_data && !activity_present && !sysfs_active && !started);
    assert(!mouse_hdev->dev.driver_data && atomic_load(&writes) >= minimum_writes);
    free(data);
    private_data = NULL;
    atomic_store(&freed, true);
}

static void razer_mouse_init(struct razer_mouse_device *dev, struct hid_device *hdev)
{
    struct usb_interface *intf = to_usb_interface(hdev->dev.parent);
    dev->hdev = hdev;
    dev->usb_pid = intf->usb->descriptor.idProduct;
    dev->usb_interface_protocol = intf->cur_altsetting->desc.bInterfaceProtocol;
    dev->usb_interface_subclass = intf->cur_altsetting->desc.bInterfaceSubClass;
}

static int device_create_file(struct device *dev, const struct device_attribute *attr)
{
    assert(!locked);
    if (attr == &dev_attr_device_last_activity) {
        char buf[64];
        activity_present = true;
        assert(razer_attr_read_device_last_activity(dev, NULL, buf) == -EAGAIN);
    }
    return 0;
}

static void device_remove_file(struct device *dev, const struct device_attribute *attr)
{
    assert(!locked && dev == &mouse_hdev->dev);
    if (attr != &dev_attr_device_last_activity)
        return;
    pthread_mutex_lock(&schedule);
    draining = true;
    pthread_cond_broadcast(&changed);
    while (sysfs_active)
        pthread_cond_wait(&changed, &schedule);
    activity_present = false;
    pthread_mutex_unlock(&schedule);
}

static int hid_parse(struct hid_device *hdev)
{
    assert(!locked && hdev->dev.driver_data);
    if (probe_activity) {
        struct hid_report report = {0};
        u8 data[] = {1};
        razer_note_activity(keyboard_hdev, &report, USB_INTERFACE_PROTOCOL_KEYBOARD, data, 1);
    }
    return parse_error;
}

static int hid_hw_start(struct hid_device *hdev, int flags)
{
    assert(!locked && hdev->dev.driver_data && flags == HID_CONNECT_DEFAULT);
    started = !start_error;
    return start_error;
}

static void hid_hw_stop(struct hid_device *hdev)
{
    assert(!locked && hdev->dev.driver_data && started);
    started = false;
}
static void hrtimer_cancel(struct hrtimer *timer) { assert(!locked && timer); }
"""

HARNESS = r"""
enum { KEY_ACTIVITY, MOUSE_ACTIVITY, SYSFS_READER };
struct job { int kind; ssize_t result; char buf[64]; };

static void *run_callback(void *argument)
{
    struct job *job = argument;
    callback = true;
    if (job->kind == SYSFS_READER) {
        pthread_mutex_lock(&schedule);
        ++sysfs_active;
        pthread_mutex_unlock(&schedule);
        job->result = razer_attr_read_device_last_activity(&mouse_hdev->dev, NULL, job->buf);
        pthread_mutex_lock(&schedule);
        --sysfs_active;
        pthread_cond_broadcast(&changed);
        pthread_mutex_unlock(&schedule);
    } else {
        struct hid_report report = {0};
        u8 data[] = {1};
        bool mouse = job->kind == MOUSE_ACTIVITY;
        razer_note_activity(mouse ? mouse_hdev : keyboard_hdev, &report,
                            mouse ? USB_INTERFACE_PROTOCOL_MOUSE : USB_INTERFACE_PROTOCOL_KEYBOARD,
                            data, 1);
    }
    return NULL;
}

static void *run_unbind(void *unused)
{
    (void)unused;
    unbinder = true;
    razer_mouse_disconnect(mouse_hdev);
    return NULL;
}

static void wait_for(bool *condition)
{
    pthread_mutex_lock(&schedule);
    while (!*condition)
        pthread_cond_wait(&changed, &schedule);
    pthread_mutex_unlock(&schedule);
}

static void check_read(const char *expected)
{
    char buf[64];
    assert(razer_attr_read_device_last_activity(&mouse_hdev->dev, NULL, buf) == (ssize_t)strlen(expected));
    assert(!strcmp(buf, expected));
}

static void run_case(unsigned short pid, int kind, int point, int failure)
{
    struct bus_type bus = {0};
    struct usb_device usb = { .descriptor = { .idProduct = pid } };
    __typeof__(*usb.actconfig) config = { .desc = { .bNumInterfaces = 2 } };
    usb.actconfig = &config;
    struct usb_host_interface mouse_alt = { .desc = { .bInterfaceProtocol = USB_INTERFACE_PROTOCOL_MOUSE } };
    struct usb_host_interface keyboard_alt = { .desc = { .bInterfaceProtocol = USB_INTERFACE_PROTOCOL_KEYBOARD } };
    struct usb_interface mouse_intf = { .cur_altsetting = &mouse_alt, .usb = &usb };
    struct usb_interface keyboard_intf = { .cur_altsetting = &keyboard_alt, .usb = &usb };
    struct hid_device mouse = { .dev = { .parent = &mouse_intf.dev, .bus = &bus, .refs = 1 } };
    struct hid_device keyboard = { .dev = { .parent = &keyboard_intf.dev, .bus = &bus, .refs = 1 } };
    mouse_intf.dev.child = &mouse.dev;
    keyboard_intf.dev.child = &keyboard.dev;
    usb.interfaces[0] = &mouse_intf;
    usb.interfaces[1] = &keyboard_intf;
    mouse_hdev = &mouse;
    keyboard_hdev = &keyboard;
    assert(!private_data && !started);
    atomic_store(&freed, false);
    atomic_store(&writes, 0);
    atomic_store(&device_gets, 0);
    atomic_store(&device_puts, 0);
    pause_at = point;
    paused = resumed = retiring = draining = false;
    sysfs_active = minimum_writes = 0;
    parse_error = failure == 1 ? -EINVAL : 0;
    start_error = failure == 2 ? -EIO : 0;
    probe_activity = failure != 0;
    if (failure)
        minimum_writes = 1;

    int result = razer_mouse_probe(&mouse, NULL);
    assert(result == (parse_error ? parse_error : start_error));
    if (!failure) {
        check_read("-1\n");
        struct hid_report numbered = { .id = 4 };
        u8 idle[] = {4, 0, 0};
        razer_note_activity(&keyboard, &numbered, USB_INTERFACE_PROTOCOL_KEYBOARD, idle, sizeof(idle));
        razer_note_activity(&keyboard, &numbered, USB_INTERFACE_PROTOCOL_KEYBOARD, idle, 1);
        razer_note_activity(&mouse, &numbered, USB_INTERFACE_PROTOCOL_MOUSE, idle, 0);
        razer_note_activity(&keyboard, &numbered, 0, idle, sizeof(idle));
        assert(!atomic_load(&writes));
        if (kind == SYSFS_READER) {
            u8 data[] = {4, 1};
            razer_note_activity(&keyboard, &numbered, USB_INTERFACE_PROTOCOL_KEYBOARD, data, sizeof(data));
            jiffies += 25;
            check_read("25\n");
        }

        struct job job = { .kind = kind };
        pthread_t reader, remover;
        assert(!pthread_create(&reader, NULL, run_callback, &job));
        wait_for(&paused);
        minimum_writes = point == PRIVATE_LOOKUP ? 1 : 0;
        assert(!pthread_create(&remover, NULL, run_unbind, NULL));
        if (kind == SYSFS_READER)
            wait_for(&draining);
        else
            wait_for(&retiring);
        if (point == HID_LOOKUP) {
            assert(!pthread_join(remover, NULL));
            assert(atomic_load(&freed) && !atomic_load(&writes));
        } else {
            assert(!atomic_load(&freed));
        }
        pthread_mutex_lock(&schedule);
        resumed = true;
        pthread_cond_broadcast(&changed);
        pthread_mutex_unlock(&schedule);
        assert(!pthread_join(reader, NULL));
        if (point != HID_LOOKUP)
            assert(!pthread_join(remover, NULL));
        if (kind == SYSFS_READER)
            assert(job.result == 3 && !strcmp(job.buf, "25\n"));
        assert(atomic_load(&writes) == (point == PRIVATE_LOOKUP ? 1 : 0));
    }

    assert(atomic_load(&freed) && !private_data && !mouse.dev.driver_data);
    int completed = atomic_load(&writes);
    struct hid_report report = {0};
    u8 data[] = {1};
    razer_note_activity(&keyboard, &report, USB_INTERFACE_PROTOCOL_KEYBOARD, data, 1);
    razer_note_activity(&mouse, &report, USB_INTERFACE_PROTOCOL_MOUSE, data, 1);
    char buf[64];
    assert(razer_attr_read_device_last_activity(&mouse.dev, NULL, buf) == -EAGAIN);
    assert(atomic_load(&writes) == completed);
    assert(atomic_load(&device_gets) == atomic_load(&device_puts) && atomic_load(&mouse.dev.refs) == 1);
}

int main(void)
{
    const unsigned short pids[] = { USB_DEVICE_ID_RAZER_NAGA_V3_PRO_WIRED, USB_DEVICE_ID_RAZER_NAGA_V3_PRO_WIRELESS };
    for (size_t i = 0; i < sizeof(pids) / sizeof(pids[0]); ++i) {
        run_case(pids[i], KEY_ACTIVITY, PRIVATE_LOOKUP, 0);
        run_case(pids[i], MOUSE_ACTIVITY, PRIVATE_LOOKUP, 0);
        run_case(pids[i], KEY_ACTIVITY, HID_LOOKUP, 0);
        run_case(pids[i], SYSFS_READER, PRIVATE_LOOKUP, 0);
        run_case(pids[i], KEY_ACTIVITY, NO_PAUSE, 1);
        run_case(pids[i], KEY_ACTIVITY, NO_PAUSE, 2);
    }
    printf("12 fake mouse activity lifetime cases passed\n");
    return 0;
}
"""


class MouseActivityTest(unittest.TestCase):
    def test_activity_lifetime(self):
        compiler = shutil.which('clang')
        if compiler is None:
            self.skipTest('Clang is required for the fake mouse activity regression')

        driver = Path(__file__).resolve().parents[2] / 'driver'
        source = (driver / 'razermouse_driver.c').read_text()
        header = (driver / 'razermouse_driver.h').read_text()
        common = (driver / 'razercommon.h').read_text()
        publication = re.search(r'static DEFINE_SPINLOCK\(.*?\n\}', source, re.S).group()
        read = re.search(r'static ssize_t razer_attr_read_device_last_activity\([^\n]*\n\{.*?\n\}', source, re.S).group()
        begin = source.index('#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 14, 0)')
        lookup = source[begin:source.index('/**\n * Test if a bit is cleared', begin)]
        begin = source.index('static int razer_mouse_probe(')
        lifecycle = source[begin:source.index('/**\n * Device ID mapping table', begin)]
        private = re.search(r'struct razer_mouse_device \{.*?\n\};', header, re.S).group()
        attrs = sorted(set(re.findall(r'\bdev_attr_\w+', lifecycle)))
        definitions = '\n'.join(re.findall(r'^#define USB_DEVICE_ID_RAZER_[^\n]+', header, re.M))
        definitions += '\n' + '\n'.join(
            f'static const struct device_attribute {name} = {{ {i} }};' for i, name in enumerate(attrs))
        macro = re.search(r'^#define CREATE_DEVICE_FILE[^\n]*\n(?:[^\n]*\\\n)*[^\n]*', common, re.M).group()
        translation_unit = '\n'.join((STUBS, private, definitions, FAKES, publication,
                                      read, lookup, macro, lifecycle, HARNESS))

        with tempfile.TemporaryDirectory(prefix='openrazer-mouse-activity-') as temporary:
            executable = Path(temporary) / 'mouse_activity_fake'
            compiled = subprocess.run(
                [compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                 '-Wno-unused-parameter', '-Wno-unused-function', '-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all', '-pthread', '-x', 'c', '-', '-o', str(executable)],
                input=translation_unit, capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('12 fake mouse activity lifetime cases passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
