#pragma once
#include <Python.h>
#include <windows.h>
#include <d3d11.h>
#include <wrl/client.h>
#include <winrt/base.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace scap
{
static_assert(sizeof(void *) == 8, "Windows backend requires x64 or ARM64");
using Microsoft::WRL::ComPtr;
inline void check(HRESULT hr)
{
    winrt::check_hresult(hr);
}
[[noreturn]] inline void winfail()
{
    DWORD error = GetLastError();
    throw winrt::hresult_error(HRESULT_FROM_WIN32(error ? error : ERROR_GEN_FAILURE));
}
inline void wincheck(BOOL ok)
{
    if (!ok)
    {
        winfail();
    }
}
struct NoPython
{
    PyThreadState *state = PyEval_SaveThread();
    ~NoPython()
    {
        PyEval_RestoreThread(state);
    }
    NoPython(const NoPython &) = delete;
    NoPython() = default;
};
struct Apartment
{
    bool initialized = false;
    Apartment()
    {
        HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        // WIC can use a caller's existing STA. Capture/recording/audio have
        // dedicated threads, which initialize as MTA normally.
        if (hr != RPC_E_CHANGED_MODE)
        {
            check(hr);
            initialized = true;
        }
    }
    ~Apartment()
    {
        if (initialized)
        {
            CoUninitialize();
        }
    }
};
struct DpiScope
{
    DPI_AWARENESS_CONTEXT old =
        SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    ~DpiScope()
    {
        if (old)
        {
            SetThreadDpiAwarenessContext(old);
        }
    }
};
struct Handle
{
    HANDLE value = nullptr;
    explicit Handle(HANDLE h = nullptr) : value(h) {}
    ~Handle()
    {
        if (value && value != INVALID_HANDLE_VALUE)
        {
            CloseHandle(value);
        }
    }
    Handle(const Handle &) = delete;
    Handle &operator=(const Handle &) = delete;
};
struct Timeout : std::runtime_error
{
    using std::runtime_error::runtime_error;
};
template <class F> PyObject *boundary(F &&f) noexcept
{
    try
    {
        return f();
    }
    catch (const std::bad_alloc &)
    {
        return PyErr_NoMemory();
    }
    catch (const std::invalid_argument &e)
    {
        PyErr_SetString(PyExc_ValueError, e.what());
    }
    catch (const Timeout &e)
    {
        PyErr_SetString(PyExc_TimeoutError, e.what());
    }
    catch (const winrt::hresult_error &e)
    {
        char code[32];
        std::snprintf(code, sizeof(code), "Windows error 0x%08lx",
                      static_cast<unsigned long>(e.code().value));
        auto message = e.message();
        PyObject *detail =
            PyUnicode_FromWideChar(message.c_str(), static_cast<Py_ssize_t>(message.size()));
        if (!detail)
        {
            return nullptr;
        }
        PyErr_Format(PyExc_OSError, "%s: %U", code, detail);
        Py_DECREF(detail);
    }
    catch (const std::exception &e)
    {
        PyErr_SetString(PyExc_OSError, e.what());
    }
    catch (...)
    {
        PyErr_SetString(PyExc_OSError, "Unexpected Windows backend error");
    }
    return nullptr;
}
inline int64_t clock100()
{
    LARGE_INTEGER n, frequency;
    QueryPerformanceCounter(&n);
    QueryPerformanceFrequency(&frequency);
    return (n.QuadPart / frequency.QuadPart) * 10000000 +
           (n.QuadPart % frequency.QuadPart) * 10000000 / frequency.QuadPart;
}
inline bool positive_u32(PyObject *obj, uint32_t &out, const char *name)
{
    if (PyBool_Check(obj) || !PyLong_Check(obj))
    {
        PyErr_Format(PyExc_TypeError, "%s must be an integer", name);
        return false;
    }
    unsigned long v = PyLong_AsUnsignedLong(obj);
    if (PyErr_Occurred())
    {
        return false;
    }
    if (!v)
    {
        PyErr_Format(PyExc_ValueError, "%s must be positive", name);
        return false;
    }
    out = v;
    return true;
}
struct Display
{
    uint32_t id;
    HMONITOR monitor;
    RECT rect;
    bool primary;
    std::wstring device;
    double ui_scale = 1;
};
std::vector<Display> displays();
Display display_by_id(uint32_t id);
std::vector<POINT> mouse_path(POINT start, POINT end, const std::vector<Display> &monitors);

struct Frame
{
    ComPtr<ID3D11Texture2D> texture;
    uint32_t width = 0, height = 0;
    int64_t timestamp = 0;
    std::vector<uint8_t> synthetic;
};
struct Pixels
{
    uint32_t width = 0, height = 0, stride = 0;
    std::vector<uint8_t> data;
};
class Capture
{
  public:
    struct Impl;
    std::shared_ptr<Impl> impl;
    explicit Capture(uint32_t id);
    explicit Capture(Pixels pixels);
    ~Capture();
    void stop();
    std::shared_ptr<Frame> latest();
    std::shared_ptr<Frame> first();
    Pixels pixels(const std::shared_ptr<Frame> &frame);
    std::vector<uint8_t> nv12(const std::shared_ptr<Frame> &frame);
};

#define SCAP_FUNCTIONS(X)                                                                          \
    X(list_displays)                                                                               \
    X(get_mouse_position)                                                                          \
    X(move_mouse)                                                                                  \
    X(move_mouse_relative) X(drag_mouse) X(mouse_click) X(mouse_scroll) X(keyboard_click)          \
        X(keyboard_begin) X(keyboard_end) X(check_permission) X(set_clipboard) X(get_clipboard)    \
            X(mouse_path) X(start_capture) X(stop_capture) X(current_frame_bgra)                   \
                X(current_frame_jpg) X(start_recording) X(stop_recording) X(abort_recording)
#define DECLARE(name) PyObject *py_##name(PyObject *, PyObject *);
SCAP_FUNCTIONS(DECLARE)
#ifdef SCAPKIT_TESTING
DECLARE(test_input) DECLARE(test_capture) DECLARE(test_recording) DECLARE(test_decode)
#endif
#undef DECLARE
} // namespace scap
