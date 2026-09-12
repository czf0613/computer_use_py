#pragma once

#include <Python.h>

PyObject *scapkit_start_capture(PyObject *self, PyObject *args);

PyObject *scapkit_stop_capture(PyObject *self, PyObject *args);

PyObject *scapkit_current_frame_jpg(PyObject *self, PyObject *args);

PyObject *scapkit_current_frame_bgra(PyObject *self, PyObject *args);

#ifdef SCAPKIT_TESTING
PyObject *scapkit_test_capture(PyObject *self, PyObject *args);
PyObject *scapkit_test_update_frame(PyObject *self, PyObject *args);
PyObject *scapkit_test_live_frames(PyObject *self, PyObject *args);
PyObject *scapkit_test_lifecycle_stats(PyObject *self, PyObject *args);
#endif
