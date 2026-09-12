#pragma once
#include <Python.h>

PyObject *scapkit_start_recording(PyObject *self, PyObject *args);
PyObject *scapkit_stop_recording(PyObject *self, PyObject *args);
PyObject *scapkit_abort_recording(PyObject *self, PyObject *args);

#ifdef SCAPKIT_TESTING
PyObject *scapkit_test_recording(PyObject *self, PyObject *args);
PyObject *scapkit_test_recording_tick(PyObject *self, PyObject *args);
PyObject *scapkit_test_stop_recording_at(PyObject *self, PyObject *args);
PyObject *scapkit_test_recording_owners(PyObject *self, PyObject *unused);
PyObject *scapkit_test_recording_state(PyObject *self, PyObject *args);
PyObject *scapkit_test_recording_audio(PyObject *self, PyObject *args);
#endif
