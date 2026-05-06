#include <Python.h>
#include "display.h"

static PyMethodDef ScapkitMethods[] = {
    {"list_displays", scapkit_list_displays, METH_NOARGS, NULL},
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
