#include <Python.h>
#include "display.h"
#include "control.h"
#include "capture.h"

static PyMethodDef ScapkitMethods[] = {
    {"list_displays", scapkit_list_displays, METH_NOARGS, NULL},
    {"get_mouse_position", scapkit_get_mouse_position, METH_NOARGS, NULL},
    {"move_mouse", scapkit_move_mouse, METH_VARARGS, NULL},
    {"move_mouse_relative", scapkit_move_mouse_relative, METH_VARARGS, NULL},
    {"mouse_click", scapkit_mouse_click, METH_VARARGS, NULL},
    {"mouse_scroll", scapkit_mouse_scroll, METH_VARARGS, NULL},
    {"check_permission", scapkit_check_permission, METH_VARARGS, NULL},
    {"keyboard_click", scapkit_keyboard_click, METH_VARARGS, NULL},
    {"start_capture", scapkit_start_capture, METH_VARARGS, NULL},
    {"stop_capture", scapkit_stop_capture, METH_VARARGS, NULL},
    {"current_frame_jpg", scapkit_current_frame_jpg, METH_VARARGS, NULL},
    {"current_frame_bgra", scapkit_current_frame_bgra, METH_VARARGS, NULL},
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef scapkit_module = {
    PyModuleDef_HEAD_INIT,
    "_scapkit",
    NULL,
    -1,
    ScapkitMethods};

PyMODINIT_FUNC
PyInit__scapkit(void)
{
    return PyModule_Create(&scapkit_module);
}
