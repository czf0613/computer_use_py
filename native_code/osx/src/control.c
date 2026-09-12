#include "control.h"
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <pthread.h>
#include <time.h>
#include <CoreGraphics/CoreGraphics.h>
#include <ApplicationServices/ApplicationServices.h>

PyObject *scapkit_get_mouse_position(PyObject *self, PyObject *args)
{
    CGEventRef event = CGEventCreate(NULL);
    if (event == NULL)
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreate failed");
        return NULL;
    }
    CGPoint loc = CGEventGetLocation(event);
    CFRelease(event);

    if (!isfinite(loc.x) || !isfinite(loc.y) ||
        loc.x < INT_MIN || loc.x > INT_MAX ||
        loc.y < INT_MIN || loc.y > INT_MAX)
    {
        PyErr_SetString(PyExc_OSError, "CGEventGetLocation returned invalid coordinates");
        return NULL;
    }

    return Py_BuildValue("{s:i, s:i}", "x", (int)loc.x, "y", (int)loc.y);
}

PyObject *scapkit_move_mouse(PyObject *self, PyObject *args)
{
    int x, y;
    if (!PyArg_ParseTuple(args, "ii:move_mouse", &x, &y))
    {
        return NULL;
    }

    CGPoint dest = CGPointMake((CGFloat)x, (CGFloat)y);
    CGError err = CGWarpMouseCursorPosition(dest);
    if (err != kCGErrorSuccess)
    {
        PyErr_Format(PyExc_OSError, "CGWarpMouseCursorPosition failed with error %d", (int)err);
        return NULL;
    }

    Py_RETURN_NONE;
}

PyObject *scapkit_move_mouse_relative(PyObject *self, PyObject *args)
{
    int dx, dy;
    if (!PyArg_ParseTuple(args, "ii:move_mouse_relative", &dx, &dy))
    {
        return NULL;
    }

    CGEventRef pos_event = CGEventCreate(NULL);
    if (pos_event == NULL)
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreate failed");
        return NULL;
    }
    CGPoint loc = CGEventGetLocation(pos_event);
    CFRelease(pos_event);

    CGPoint new_loc = CGPointMake(loc.x + dx, loc.y + dy);
    CGEventRef event = CGEventCreateMouseEvent(NULL, kCGEventMouseMoved, new_loc, 0);
    if (event == NULL)
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreateMouseEvent failed");
        return NULL;
    }
    CGEventSetIntegerValueField(event, kCGMouseEventDeltaX, dx);
    CGEventSetIntegerValueField(event, kCGMouseEventDeltaY, dy);
    CGEventPost(kCGHIDEventTap, event);
    CFRelease(event);

    Py_RETURN_NONE;
}

PyObject *scapkit_drag_mouse(PyObject *self, PyObject *args)
{
    int x, y;
    if (!PyArg_ParseTuple(args, "ii:_drag_mouse", &x, &y)) { return NULL; }
    CGEventRef position = CGEventCreate(NULL);
    if (!position)
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreate failed");
        return NULL;
    }
    CGPoint start = CGEventGetLocation(position);
    CFRelease(position);
    CGPoint dest = CGPointMake(x, y);
    CGEventRef event = CGEventCreateMouseEvent(NULL, kCGEventLeftMouseDragged, dest, kCGMouseButtonLeft);
    if (!event)
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreateMouseEvent failed");
        return NULL;
    }
    CGEventSetDoubleValueField(event, kCGMouseEventDeltaX, dest.x - start.x);
    CGEventSetDoubleValueField(event, kCGMouseEventDeltaY, dest.y - start.y);
    CGEventPost(kCGHIDEventTap, event);
    CFRelease(event);
    Py_RETURN_NONE;
}

PyObject *scapkit_mouse_click(PyObject *self, PyObject *args)
{
    const char *key, *action;
    if (!PyArg_ParseTuple(args, "ss:mouse_click", &key, &action))
    {
        return NULL;
    }

    CGMouseButton button;
    if (strcmp(key, "left") == 0)
    {
        button = kCGMouseButtonLeft;
    }
    else if (strcmp(key, "right") == 0)
    {
        button = kCGMouseButtonRight;
    }
    else
    {
        PyErr_Format(PyExc_ValueError, "key must be 'left' or 'right', got '%s'", key);
        return NULL;
    }

    CGEventType event_type;
    if (strcmp(action, "down") == 0)
    {
        event_type = (button == kCGMouseButtonLeft) ? kCGEventLeftMouseDown : kCGEventRightMouseDown;
    }
    else if (strcmp(action, "up") == 0)
    {
        event_type = (button == kCGMouseButtonLeft) ? kCGEventLeftMouseUp : kCGEventRightMouseUp;
    }
    else
    {
        PyErr_Format(PyExc_ValueError, "action must be 'down' or 'up', got '%s'", action);
        return NULL;
    }

    CGEventRef pos_event = CGEventCreate(NULL);
    if (pos_event == NULL)
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreate failed");
        return NULL;
    }
    CGPoint loc = CGEventGetLocation(pos_event);
    CFRelease(pos_event);

    CGEventRef mouse_event = CGEventCreateMouseEvent(NULL, event_type, loc, button);
    if (mouse_event == NULL)
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreateMouseEvent failed");
        return NULL;
    }
    CGEventPost(kCGHIDEventTap, mouse_event);
    CFRelease(mouse_event);

    Py_RETURN_NONE;
}

PyObject *scapkit_mouse_scroll(PyObject *self, PyObject *args)
{
    const char *direction;
    int distance;
    if (!PyArg_ParseTuple(args, "si:mouse_scroll", &direction, &distance))
    {
        return NULL;
    }

    // Negate in a wider type so INT_MIN cannot overflow before validation.
    int64_t delta_y = 0, delta_x = 0;
    if (strcmp(direction, "up") == 0)
    {
        delta_y = distance;
    }
    else if (strcmp(direction, "down") == 0)
    {
        delta_y = -(int64_t)distance;
    }
    else if (strcmp(direction, "left") == 0)
    {
        delta_x = distance;
    }
    else if (strcmp(direction, "right") == 0)
    {
        delta_x = -(int64_t)distance;
    }
    else
    {
        PyErr_Format(PyExc_ValueError, "direction must be 'up', 'down', 'left', or 'right', got '%s'", direction);
        return NULL;
    }

    if (delta_y < INT32_MIN || delta_y > INT32_MAX ||
        delta_x < INT32_MIN || delta_x > INT32_MAX)
    {
        PyErr_SetString(PyExc_OverflowError, "scroll distance exceeds the signed 32-bit event range");
        return NULL;
    }

    CGEventRef event = CGEventCreateScrollWheelEvent(NULL, kCGScrollEventUnitLine, 2,
                                                    (int32_t)delta_y, (int32_t)delta_x);
    if (event == NULL)
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreateScrollWheelEvent failed");
        return NULL;
    }
    CGEventPost(kCGHIDEventTap, event);
    CFRelease(event);

    Py_RETURN_NONE;
}

PyObject *scapkit_check_permission(PyObject *self, PyObject *args)
{
    const char *permission_type;
    if (!PyArg_ParseTuple(args, "s:check_permission", &permission_type))
    {
        return NULL;
    }

    if (strcmp(permission_type, "Accessibility") == 0)
    {
        return PyBool_FromLong(AXIsProcessTrusted());
    }
    else if (strcmp(permission_type, "ScreenCapture") == 0)
    {
        return PyBool_FromLong(CGPreflightScreenCaptureAccess());
    }

    PyErr_Format(PyExc_ValueError, "permission_type must be 'Accessibility' or 'ScreenCapture', got '%s'", permission_type);
    return NULL;
}

PyObject *scapkit_keyboard_click(PyObject *self, PyObject *args)
{
    int key_code;
    const char *action;
    PyObject *flags_obj = NULL;
    if (!PyArg_ParseTuple(args, "is|O:keyboard_click", &key_code, &action, &flags_obj))
    {
        return NULL;
    }

    unsigned long long flags = 0;
    if (flags_obj != NULL)
    {
        // The K parser masks out-of-range values instead of reporting overflow.
        PyObject *index = PyNumber_Index(flags_obj);
        if (index == NULL)
        {
            return NULL;
        }
        flags = PyLong_AsUnsignedLongLong(index);
        Py_DECREF(index);
        if (PyErr_Occurred())
        {
            return NULL;
        }
    }

    if (key_code < 0 || key_code > UINT16_MAX)
    {
        PyErr_SetString(PyExc_ValueError, "key_code must be between 0 and 65535");
        return NULL;
    }

    bool key_down;
    if (strcmp(action, "down") == 0)
    {
        key_down = true;
    }
    else if (strcmp(action, "up") == 0)
    {
        key_down = false;
    }
    else
    {
        PyErr_Format(PyExc_ValueError, "action must be 'down' or 'up', got '%s'", action);
        return NULL;
    }

    CGEventRef event = CGEventCreateKeyboardEvent(NULL, (CGKeyCode)key_code, key_down);
    if (event == NULL)
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreateKeyboardEvent failed");
        return NULL;
    }
    // A NULL event source inherits session flags, including a previous shortcut.
    // Zero is an explicit request for an unmodified key, not "leave defaults".
    CGEventSetFlags(event, (CGEventFlags)flags);
    CGEventPost(kCGHIDEventTap, event);
    CFRelease(event);

    Py_RETURN_NONE;
}

/* A shortcut owns a PRIVATE state table, not the shared default source used by
 * the raw single-event API. Its complete down/up sequence is allocated before
 * posting anything, so cleanup never needs to allocate an event after key-down.
 * Private sources do not isolate HID/session flags on posting. Post shortcuts
 * at the SESSION tap, preserving current HID flags on every event; the hardware
 * table then remains an independent source for physical/explicit raw holds. */
#define KEYBOARD_STROKE_CAPSULE "scapkit.KeyboardStroke"
#define STROKE_MAX_KEYS 6
typedef struct {
    pthread_mutex_t mutex;
    CGEventSourceRef source;
    CGEventRef down[STROKE_MAX_KEYS];
    CGEventRef up[STROKE_MAX_KEYS];
    size_t count;
    bool active;
    bool delivered;
    CGEventSourceStateID source_id;
    uint32_t initial_count[3], expected_count[3];
} KeyboardStroke;

static const CGEventType stroke_types[] = {kCGEventKeyDown, kCGEventKeyUp, kCGEventFlagsChanged};

static const struct {
    CGKeyCode key;
    CGEventFlags flag;
} shortcut_modifiers[] = {
    {55, kCGEventFlagMaskCommand},
    {56, kCGEventFlagMaskShift},
    {58, kCGEventFlagMaskAlternate},
    {59, kCGEventFlagMaskControl},
    {63, kCGEventFlagMaskSecondaryFn},
};

static CGEventFlags key_modifier(CGKeyCode key)
{
    switch (key) {
        case 54: case 55: return kCGEventFlagMaskCommand;
        case 56: case 60: return kCGEventFlagMaskShift;
        case 58: case 61: return kCGEventFlagMaskAlternate;
        case 59: case 62: return kCGEventFlagMaskControl;
        case 63: return kCGEventFlagMaskSecondaryFn;
        default: return 0;
    }
}

static void stroke_clear_events(KeyboardStroke *stroke)
{
    for (size_t i = 0; i < STROKE_MAX_KEYS; i++)
    {
        if (stroke->down[i]) { CFRelease(stroke->down[i]); stroke->down[i] = NULL; }
        if (stroke->up[i]) { CFRelease(stroke->up[i]); stroke->up[i] = NULL; }
    }
    if (stroke->source) { CFRelease(stroke->source); stroke->source = NULL; }
}

static void stroke_post(CGEventRef event)
{
    // Re-read at each event, including cleanup, rather than restoring a stale
    // pre-shortcut snapshot. HID contains physical keys and the raw HID API;
    // our session-level events cannot feed their own flags back into it.
    CGEventSetFlags(event, CGEventGetFlags(event) |
                    CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState));
    CGEventPost(kCGSessionEventTap, event);
}

static bool stroke_acknowledged(KeyboardStroke *stroke)
{
    for (size_t i = 0; i < 3; i++)
    {
        if (stroke->expected_count[i] > 0 &&
            (uint32_t)(CGEventSourceCounterForEventType(stroke->source_id, stroke_types[i]) -
                       stroke->initial_count[i]) < stroke->expected_count[i])
        {
            return false;
        }
    }
    return true;
}

static bool stroke_finish(KeyboardStroke *stroke)
{
    pthread_mutex_lock(&stroke->mutex);
    if (stroke->active)
    {
        for (size_t i = stroke->count; i > 0; i--)
        {
            stroke_post(stroke->up[i - 1]);
        }
        stroke->active = false;
        // CGEventPost only queues input. Wait for THIS source's keyboard counts,
        // not global flags (which may legitimately stay held). Counting only the
        // final flagsChanged would overlook a missing ordinary key-up.
        uint64_t deadline = clock_gettime_nsec_np(CLOCK_UPTIME_RAW) + 1000000000ULL;
        while (!stroke_acknowledged(stroke) &&
               clock_gettime_nsec_np(CLOCK_UPTIME_RAW) < deadline)
        {
            struct timespec delay = {0, 1000000};
            nanosleep(&delay, NULL);
        }
        stroke->delivered = stroke_acknowledged(stroke);
    }
    stroke_clear_events(stroke);
    bool delivered = stroke->delivered;
    pthread_mutex_unlock(&stroke->mutex);
    return delivered;
}

static void stroke_free(KeyboardStroke *stroke)
{
    stroke_clear_events(stroke);
    pthread_mutex_destroy(&stroke->mutex);
    free(stroke);
}

static void stroke_destructor(PyObject *capsule)
{
    KeyboardStroke *stroke = PyCapsule_GetPointer(capsule, KEYBOARD_STROKE_CAPSULE);
    if (stroke == NULL)
    {
        PyErr_WriteUnraisable(capsule);
        return;
    }
    Py_BEGIN_ALLOW_THREADS
    stroke_finish(stroke);
    stroke_free(stroke);
    Py_END_ALLOW_THREADS
}

static bool stroke_add(KeyboardStroke *stroke, CGKeyCode key,
                       CGEventFlags down_flags, CGEventFlags up_flags)
{
    size_t i = stroke->count;
    stroke->down[i] = CGEventCreateKeyboardEvent(stroke->source, key, true);
    stroke->up[i] = CGEventCreateKeyboardEvent(stroke->source, key, false);
    if (!stroke->down[i] || !stroke->up[i])
    {
        PyErr_SetString(PyExc_OSError, "CGEventCreateKeyboardEvent failed before shortcut started");
        return false;
    }
    CGEventSetFlags(stroke->down[i], down_flags);
    CGEventSetFlags(stroke->up[i], up_flags);
    stroke->count++;
    return true;
}

PyObject *scapkit_keyboard_begin(PyObject *self, PyObject *args)
{
    int key;
    PyObject *flags_obj;
    if (!PyArg_ParseTuple(args, "iO:_keyboard_begin", &key, &flags_obj)) { return NULL; }
    PyObject *index = PyNumber_Index(flags_obj);
    if (!index) { return NULL; }
    unsigned long long flags = PyLong_AsUnsignedLongLong(index);
    Py_DECREF(index);
    if (PyErr_Occurred()) { return NULL; }
    CGEventFlags allowed = kCGEventFlagMaskCommand | kCGEventFlagMaskShift |
        kCGEventFlagMaskAlternate | kCGEventFlagMaskControl | kCGEventFlagMaskSecondaryFn;
    if (key < 0 || key > UINT16_MAX || (flags & ~allowed))
    {
        PyErr_SetString(PyExc_ValueError, "invalid shortcut key code or modifier flags");
        return NULL;
    }
    KeyboardStroke *stroke = calloc(1, sizeof *stroke);
    if (!stroke) { return PyErr_NoMemory(); }
    if (pthread_mutex_init(&stroke->mutex, NULL) != 0)
    {
        free(stroke);
        PyErr_SetString(PyExc_OSError, "failed to initialize shortcut mutex");
        return NULL;
    }
    stroke->source = CGEventSourceCreate(kCGEventSourceStatePrivate);
    if (!stroke->source)
    {
        stroke_free(stroke);
        PyErr_SetString(PyExc_OSError, "CGEventSourceCreate failed");
        return NULL;
    }
    CGEventFlags external = CGEventSourceFlagsState(kCGEventSourceStateHIDSystemState);
    CGEventFlags active = 0, main_modifier = key_modifier((CGKeyCode)key);
    for (size_t i = 0; i < sizeof shortcut_modifiers / sizeof shortcut_modifiers[0]; i++)
    {
        CGEventFlags flag = shortcut_modifiers[i].flag;
        if ((flags & flag) && !(external & flag) && flag != main_modifier)
        {
            if (!stroke_add(stroke, shortcut_modifiers[i].key, active | flag, active))
            {
                stroke_free(stroke);
                return NULL;
            }
            active |= flag;
        }
    }
    if (!(main_modifier & external) &&
        !stroke_add(stroke, (CGKeyCode)key, active | main_modifier | flags, active))
    {
        stroke_free(stroke);
        return NULL;
    }
    PyObject *capsule = PyCapsule_New(stroke, KEYBOARD_STROKE_CAPSULE, stroke_destructor);
    if (!capsule) { stroke_free(stroke); return NULL; }
    Py_BEGIN_ALLOW_THREADS
    if (stroke->count > 0)
    {
        stroke->source_id = CGEventSourceGetSourceStateID(stroke->source);
        for (size_t type = 0; type < 3; type++)
        {
            stroke->initial_count[type] = CGEventSourceCounterForEventType(stroke->source_id, stroke_types[type]);
            for (size_t i = 0; i < stroke->count; i++)
            {
                stroke->expected_count[type] += CGEventGetType(stroke->down[i]) == stroke_types[type];
                stroke->expected_count[type] += CGEventGetType(stroke->up[i]) == stroke_types[type];
            }
        }
    }
    for (size_t i = 0; i < stroke->count; i++)
    {
        stroke_post(stroke->down[i]);
    }
    stroke->active = true;
    Py_END_ALLOW_THREADS
    return capsule;
}

PyObject *scapkit_keyboard_end(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "O:_keyboard_end", &capsule)) { return NULL; }
    KeyboardStroke *stroke = PyCapsule_GetPointer(capsule, KEYBOARD_STROKE_CAPSULE);
    if (!stroke) { return NULL; }
    bool delivered;
    Py_BEGIN_ALLOW_THREADS
    delivered = stroke_finish(stroke);
    Py_END_ALLOW_THREADS
    if (!delivered)
    {
        PyErr_SetString(PyExc_TimeoutError, "Shortcut releases were posted but not acknowledged within one second; check Accessibility permission and device state before retrying");
        return NULL;
    }
    Py_RETURN_NONE;
}
