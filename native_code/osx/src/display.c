#include "display.h"
#include <limits.h>
#include <math.h>
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
    if (result == NULL)
    {
        return NULL;
    }

    for (uint32_t i = 0; i < count; i++)
    {
        CGRect bounds = CGDisplayBounds(displays[i]);
        if (!isfinite(bounds.origin.x) || !isfinite(bounds.origin.y) ||
            !isfinite(bounds.size.width) || !isfinite(bounds.size.height) ||
            bounds.origin.x < INT_MIN || bounds.origin.x > INT_MAX ||
            bounds.origin.y < INT_MIN || bounds.origin.y > INT_MAX ||
            bounds.size.width < 1 || bounds.size.width > INT_MAX ||
            bounds.size.height < 1 || bounds.size.height > INT_MAX)
        {
            PyErr_Format(PyExc_OSError, "CGDisplayBounds returned invalid bounds for display %lu",
                         (unsigned long)displays[i]);
            Py_DECREF(result);
            return NULL;
        }

        CGDisplayModeRef mode = CGDisplayCopyDisplayMode(displays[i]);
        if (mode == NULL)
        {
            PyErr_Format(PyExc_OSError, "CGDisplayCopyDisplayMode failed for display %lu",
                         (unsigned long)displays[i]);
            Py_DECREF(result);
            return NULL;
        }
        double scale = (double)CGDisplayModeGetPixelWidth(mode) / bounds.size.width;
        CGDisplayModeRelease(mode);
        if (!isfinite(scale) || scale <= 0)
        {
            PyErr_Format(PyExc_OSError, "invalid scale factor for display %lu",
                         (unsigned long)displays[i]);
            Py_DECREF(result);
            return NULL;
        }

        PyObject *item = Py_BuildValue("{s:k, s:i, s:i, s:i, s:i, s:d, s:O}", "id", (unsigned long)displays[i], "x", (int)bounds.origin.x, "y", (int)bounds.origin.y, "width", (int)bounds.size.width, "height", (int)bounds.size.height, "scale_factor", scale, "is_main", displays[i] == main_id ? Py_True : Py_False);
        if (item == NULL)
        {
            Py_DECREF(result);
            return NULL;
        }
        // The list is still private to this call; SET_ITEM steals item's reference.
        PyList_SET_ITEM(result, i, item);
    }

    return result;
}
