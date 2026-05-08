#include "control.h"
#include <string.h>
#include <CoreGraphics/CoreGraphics.h>
#include <ApplicationServices/ApplicationServices.h>

PyObject *scapkit_get_mouse_position(PyObject *self, PyObject *args)
{
    CGEventRef event = CGEventCreate(NULL);
    CGPoint loc = CGEventGetLocation(event);
    CFRelease(event);

    return Py_BuildValue("{s:i, s:i}", "x", (int)loc.x, "y", (int)loc.y);
}

PyObject *scapkit_move_mouse(PyObject *self, PyObject *args)
{
    int x, y;
    PyArg_ParseTuple(args, "ii", &x, &y);

    CGPoint dest = CGPointMake((CGFloat)x, (CGFloat)y);
    CGWarpMouseCursorPosition(dest);

    Py_RETURN_NONE;
}

PyObject *scapkit_mouse_click(PyObject *self, PyObject *args)
{
    const char *key, *action;
    PyArg_ParseTuple(args, "ss", &key, &action);

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
    CGPoint loc = CGEventGetLocation(pos_event);
    CFRelease(pos_event);

    CGEventRef mouse_event = CGEventCreateMouseEvent(NULL, event_type, loc, button);
    CGEventPost(kCGHIDEventTap, mouse_event);
    CFRelease(mouse_event);

    Py_RETURN_NONE;
}

PyObject *scapkit_check_permission(PyObject *self, PyObject *args)
{
    const char *permission_type;
    PyArg_ParseTuple(args, "s", &permission_type);

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
    unsigned long long flags = 0;
    PyArg_ParseTuple(args, "is|K", &key_code, &action, &flags);

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
    if (flags)
    {
        CGEventSetFlags(event, (CGEventFlags)flags);
    }
    CGEventPost(kCGHIDEventTap, event);
    CFRelease(event);

    Py_RETURN_NONE;
}
