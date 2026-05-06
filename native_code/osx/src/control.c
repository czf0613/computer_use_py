#include "control.h"
#include <CoreGraphics/CoreGraphics.h>

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
    CGEventRef event = CGEventCreate(NULL);
    CGPoint loc = CGEventGetLocation(event);
    CFRelease(event);

    CGEventRef mouse_down = CGEventCreateMouseEvent(NULL, kCGEventLeftMouseDown, loc, kCGMouseButtonLeft);
    CGEventRef mouse_up = CGEventCreateMouseEvent(NULL, kCGEventLeftMouseUp, loc, kCGMouseButtonLeft);

    CGEventPost(kCGHIDEventTap, mouse_down);
    CGEventPost(kCGHIDEventTap, mouse_up);

    CFRelease(mouse_down);
    CFRelease(mouse_up);

    Py_RETURN_NONE;
}
