#include <Python.h>

static PyMethodDef ScapkitMethods[] = {
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef scapkit_module = {
    PyModuleDef_HEAD_INIT,
    "_scapkit",
    "ScreenCaptureKit native bindings",
    -1,
    ScapkitMethods
};

PyMODINIT_FUNC
PyInit__scapkit(void)
{
    return PyModule_Create(&scapkit_module);
}
