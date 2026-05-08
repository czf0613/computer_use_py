#pragma once

#include <Python.h>

PyObject *scapkit_start_capture(PyObject *self, PyObject *args);

PyObject *scapkit_stop_capture(PyObject *self, PyObject *args);

PyObject *scapkit_current_frame_jpg(PyObject *self, PyObject *args);

PyObject *scapkit_current_frame_bgra(PyObject *self, PyObject *args);