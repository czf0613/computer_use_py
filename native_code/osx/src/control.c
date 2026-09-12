#include "control.h"
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <string.h>
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
