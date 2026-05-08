#pragma once

#include <Python.h>

PyObject *scapkit_get_mouse_position(PyObject *self, PyObject *args);

PyObject *scapkit_move_mouse(PyObject *self, PyObject *args);

PyObject *scapkit_mouse_click(PyObject *self, PyObject *args);

PyObject *scapkit_mouse_scroll(PyObject *self, PyObject *args);

PyObject *scapkit_check_permission(PyObject *self, PyObject *args);

PyObject *scapkit_keyboard_click(PyObject *self, PyObject *args);