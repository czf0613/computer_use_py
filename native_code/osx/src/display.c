#include "display.h"
#include <CoreGraphics/CoreGraphics.h>

PyObject *scapkit_list_displays(PyObject *self, PyObject *args)
{
    CGDirectDisplayID displays[MAX_DISPLAYS];
    uint32_t count = 0;

    CGError err = CGGetActiveDisplayList(MAX_DISPLAYS, displays, &count);
    if (err != kCGErrorSuccess)
    {
        PyErr_Format(PyExc_OSError, "CGGetActiveDisplayList failed with error %d", (int)err);
        return NULL;
    }

    CGDirectDisplayID main_id = CGMainDisplayID();
    PyObject *result = PyList_New(count);

    for (uint32_t i = 0; i < count; i++)
    {
        CGRect bounds = CGDisplayBounds(displays[i]);
        CGDisplayModeRef mode = CGDisplayCopyDisplayMode(displays[i]);
        double scale = (double)CGDisplayModeGetPixelWidth(mode) / bounds.size.width;
        CGDisplayModeRelease(mode);
        PyList_SET_ITEM(result, i, Py_BuildValue("{s:k, s:d, s:d, s:d, s:d, s:d, s:O}", "id", (unsigned long)displays[i], "x", bounds.origin.x, "y", bounds.origin.y, "width", bounds.size.width, "height", bounds.size.height, "scale_factor", scale, "is_main", displays[i] == main_id ? Py_True : Py_False));
    }

    return result;
}