#include "backend.h"
#include "window_backend.h"
using namespace scap;
static PyMethodDef methods[] = {
    {"_window_enumerate", py_window_enumerate, METH_VARARGS, nullptr},
    {"_window_open", py_window_open, METH_VARARGS, nullptr},
    {"_window_close", py_window_close, METH_VARARGS, nullptr},
    {"_window_attach", py_window_attach, METH_VARARGS, nullptr},
    {"_window_screenshot", py_window_screenshot, METH_VARARGS, nullptr},
    {"_window_inspect", py_window_inspect, METH_VARARGS, nullptr},
    {"_window_action", py_window_action, METH_VARARGS, nullptr},
    {"list_displays", py_list_displays, METH_NOARGS, nullptr},
    {"get_mouse_position", py_get_mouse_position, METH_NOARGS, nullptr},
    {"move_mouse", py_move_mouse, METH_VARARGS, nullptr},
    {"move_mouse_relative", py_move_mouse_relative, METH_VARARGS, nullptr},
    {"_drag_mouse", py_drag_mouse, METH_VARARGS, nullptr},
    {"_mouse_path", py_mouse_path, METH_VARARGS, nullptr},
    {"mouse_click", py_mouse_click, METH_VARARGS, nullptr},
    {"mouse_scroll", py_mouse_scroll, METH_VARARGS, nullptr},
    {"keyboard_click", py_keyboard_click, METH_VARARGS, nullptr},
    {"_keyboard_begin", py_keyboard_begin, METH_VARARGS, nullptr},
    {"_keyboard_end", py_keyboard_end, METH_VARARGS, nullptr},
    {"check_permission", py_check_permission, METH_VARARGS, nullptr},
    {"set_clipboard", py_set_clipboard, METH_VARARGS, nullptr},
    {"get_clipboard", py_get_clipboard, METH_NOARGS, nullptr},
    {"start_capture", py_start_capture, METH_VARARGS, nullptr},
    {"stop_capture", py_stop_capture, METH_VARARGS, nullptr},
    {"current_frame_bgra", py_current_frame_bgra, METH_VARARGS, nullptr},
    {"current_frame_jpg", py_current_frame_jpg, METH_VARARGS, nullptr},
    {"start_recording", py_start_recording, METH_VARARGS, nullptr},
    {"stop_recording", py_stop_recording, METH_VARARGS, nullptr},
    {"_abort_recording", py_abort_recording, METH_VARARGS, nullptr},
#ifdef SCAPKIT_TESTING
    {"_test_window", scap::window::py_test_window, METH_VARARGS, nullptr},
    {"_test_input", py_test_input, METH_VARARGS, nullptr},
    {"_test_capture", py_test_capture, METH_VARARGS, nullptr},
    {"_test_recording", py_test_recording, METH_VARARGS, nullptr},
    {"_test_decode", py_test_decode, METH_VARARGS, nullptr},
#endif
    {nullptr, nullptr, 0, nullptr}};
static PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_scapkit", nullptr, -1, methods, nullptr, nullptr, nullptr, nullptr};
PyMODINIT_FUNC PyInit__scapkit()
{
    PyObject *result = PyModule_Create(&module);
    if (!result)
    {
        return nullptr;
    }
#ifdef Py_GIL_DISABLED
    if (PyUnstable_Module_SetGIL(result, Py_MOD_GIL_NOT_USED) < 0)
    {
        Py_DECREF(result);
        return nullptr;
    }
#endif
    if (PyModule_AddIntConstant(result, "WINDOW_BACKEND_ABI", 1) < 0)
    {
        Py_DECREF(result);
        return nullptr;
    }
    return result;
}
