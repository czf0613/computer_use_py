#include <Python.h>
#include "display.h"
#include "control.h"

static PyMethodDef ScapkitMethods[] = {
    {"list_displays", scapkit_list_displays, METH_NOARGS, NULL},
    {"get_mouse_position", scapkit_get_mouse_position, METH_NOARGS, NULL},
    {"move_mouse", scapkit_move_mouse, METH_VARARGS, NULL},
    {"mouse_click", scapkit_mouse_click, METH_NOARGS, NULL},
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
