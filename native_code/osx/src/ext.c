#include <Python.h>
#include "display.h"
#include "control.h"
#include "capture.h"
#include "recording.h"
#include "window.h"

static PyMethodDef ScapkitMethods[] = {
    {"_window_enumerate", scapkit_window_enumerate, METH_VARARGS, NULL},
    {"_window_open", scapkit_window_open, METH_VARARGS, NULL},
    {"_window_close", scapkit_window_close, METH_VARARGS, NULL},
    {"_window_attach", scapkit_window_attach, METH_VARARGS, NULL},
    {"_window_screenshot", scapkit_window_screenshot, METH_VARARGS, NULL},
    {"_window_inspect", scapkit_window_inspect, METH_VARARGS, NULL},
    {"_window_action", scapkit_window_action, METH_VARARGS, NULL},
    {"list_displays", scapkit_list_displays, METH_NOARGS, NULL},
    {"get_mouse_position", scapkit_get_mouse_position, METH_NOARGS, NULL},
    {"move_mouse", scapkit_move_mouse, METH_VARARGS, NULL},
    {"move_mouse_relative", scapkit_move_mouse_relative, METH_VARARGS, NULL},
    {"_drag_mouse", scapkit_drag_mouse, METH_VARARGS, NULL},
    {"mouse_click", scapkit_mouse_click, METH_VARARGS, NULL},
    {"mouse_scroll", scapkit_mouse_scroll, METH_VARARGS, NULL},
    {"check_permission", scapkit_check_permission, METH_VARARGS, NULL},
    {"keyboard_click", scapkit_keyboard_click, METH_VARARGS, NULL},
    {"_keyboard_begin", scapkit_keyboard_begin, METH_VARARGS, NULL},
    {"_keyboard_end", scapkit_keyboard_end, METH_VARARGS, NULL},
    {"start_capture", scapkit_start_capture, METH_VARARGS, NULL},
    {"stop_capture", scapkit_stop_capture, METH_VARARGS, NULL},
    {"current_frame_jpg", scapkit_current_frame_jpg, METH_VARARGS, NULL},
    {"current_frame_bgra", scapkit_current_frame_bgra, METH_VARARGS, NULL},
    {"start_recording", scapkit_start_recording, METH_VARARGS, NULL},
    {"stop_recording", scapkit_stop_recording, METH_VARARGS, NULL},
    {"_abort_recording", scapkit_abort_recording, METH_VARARGS, NULL},
#ifdef SCAPKIT_TESTING
    {"_test_window", scapkit_test_window, METH_VARARGS, NULL},
    {"_test_window_mutate", scapkit_test_window_mutate, METH_VARARGS, NULL},
    {"_test_window_candidate_action", scapkit_test_window_candidate_action, METH_VARARGS, NULL},
    {"_test_window_mapping", scapkit_test_window_mapping, METH_VARARGS, NULL},
    {"_test_window_frame", scapkit_test_window_frame, METH_VARARGS, NULL},
    {"_test_recording", scapkit_test_recording, METH_VARARGS, NULL},
    {"_test_recording_tick", scapkit_test_recording_tick, METH_VARARGS, NULL},
    {"_test_stop_recording_at", scapkit_test_stop_recording_at, METH_VARARGS, NULL},
    {"_test_recording_owners", scapkit_test_recording_owners, METH_NOARGS, NULL},
    {"_test_recording_stop_gate", scapkit_test_recording_stop_gate, METH_VARARGS, NULL},
    {"_test_recording_state", scapkit_test_recording_state, METH_VARARGS, NULL},
    {"_test_recording_audio", scapkit_test_recording_audio, METH_VARARGS, NULL},
    {"_test_capture", scapkit_test_capture, METH_VARARGS, NULL},
    {"_test_update_frame", scapkit_test_update_frame, METH_VARARGS, NULL},
    {"_test_live_frames", scapkit_test_live_frames, METH_NOARGS, NULL},
    {"_test_lifecycle_stats", scapkit_test_lifecycle_stats, METH_NOARGS, NULL},
#endif
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
    PyObject *module = PyModule_Create(&scapkit_module);
    if (!module)
    {
        return NULL;
    }
    if (PyModule_AddIntConstant(module, "WINDOW_BACKEND_ABI", 1) < 0)
    {
        Py_DECREF(module);
        return NULL;
    }
#ifdef Py_GIL_DISABLED
    if (PyUnstable_Module_SetGIL(module, Py_MOD_GIL_NOT_USED) < 0)
    {
        Py_DECREF(module);
        return NULL;
    }
#endif
    return module;
}
