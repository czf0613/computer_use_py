#ifndef SCAPKIT_WINDOW_H
#define SCAPKIT_WINDOW_H
#include <Python.h>

PyObject *scapkit_window_enumerate(PyObject *, PyObject *);
PyObject *scapkit_window_open(PyObject *, PyObject *);
PyObject *scapkit_window_close(PyObject *, PyObject *);
PyObject *scapkit_window_attach(PyObject *, PyObject *);
PyObject *scapkit_window_screenshot(PyObject *, PyObject *);
PyObject *scapkit_window_inspect(PyObject *, PyObject *);
PyObject *scapkit_window_action(PyObject *, PyObject *);
#ifdef SCAPKIT_TESTING
PyObject *scapkit_test_window(PyObject *, PyObject *);
PyObject *scapkit_test_window_mutate(PyObject *, PyObject *);
PyObject *scapkit_test_window_candidate_action(PyObject *, PyObject *);
PyObject *scapkit_test_window_mapping(PyObject *, PyObject *);
PyObject *scapkit_test_window_frame(PyObject *, PyObject *);
#endif
#endif
