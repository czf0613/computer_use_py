#include "backend.h"
#include <shellscalingapi.h>
#include <algorithm>
#include <cstring>
#include <map>
#include <queue>

namespace scap
{
namespace
{
constexpr ULONG_PTR input_tag = 0x53434150;
std::mutex display_mutex;
std::map<std::wstring, uint32_t> display_ids;
uint32_t next_display_id = 1;
bool contains(const RECT &r, POINT p)
{
    return p.x >= r.left && p.x < r.right && p.y >= r.top && p.y < r.bottom;
}
struct Enumeration
{
    std::vector<Display> values;
    std::exception_ptr error;
};
BOOL CALLBACK enum_monitor(HMONITOR h, HDC, LPRECT, LPARAM parameter) noexcept
{
    auto &out = *reinterpret_cast<Enumeration *>(parameter);
    try
    {
        MONITORINFOEXW info{};
        info.cbSize = sizeof(info);
        wincheck(GetMonitorInfoW(h, &info));
        DEVICE_SCALE_FACTOR factor = SCALE_100_PERCENT;
        GetScaleFactorForMonitor(h, &factor);
        out.values.push_back({0, h, info.rcMonitor, (info.dwFlags & MONITORINFOF_PRIMARY) != 0,
                              info.szDevice, static_cast<double>(factor) / 100});
        return TRUE;
    }
    catch (...)
    {
        out.error = std::current_exception();
        return FALSE;
    }
}
RECT bounds(const std::vector<Display> &ds)
{
    if (ds.empty())
    {
        throw std::runtime_error("No active displays in this desktop session");
    }
    RECT r = ds.front().rect;
    for (const auto &d : ds)
    {
        r.left = std::min(r.left, d.rect.left);
        r.top = std::min(r.top, d.rect.top);
        r.right = std::max(r.right, d.rect.right);
        r.bottom = std::max(r.bottom, d.rect.bottom);
    }
    return r;
}
INPUT motion(POINT p, RECT r, bool relative)
{
    INPUT in{};
    in.type = INPUT_MOUSE;
    in.mi.dwExtraInfo = input_tag;
    in.mi.dwFlags = MOUSEEVENTF_MOVE;
    if (relative)
    {
        in.mi.dx = p.x;
        in.mi.dy = p.y;
    }
    else
    {
        int64_t width = static_cast<int64_t>(r.right) - r.left,
                height = static_cast<int64_t>(r.bottom) - r.top;
        if (width <= 0 || height <= 0)
        {
            throw std::invalid_argument("Invalid virtual desktop bounds");
        }
        in.mi.dx = static_cast<LONG>(std::clamp(
            ((static_cast<int64_t>(p.x) - r.left) * 65536 + 32768) / width, 0LL, 65535LL));
        in.mi.dy = static_cast<LONG>(std::clamp(
            ((static_cast<int64_t>(p.y) - r.top) * 65536 + 32768) / height, 0LL, 65535LL));
        in.mi.dwFlags |= MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK;
    }
    return in;
}
INPUT wheel(const std::string &direction, int distance)
{
    if (distance < 0 || distance > INT_MAX / WHEEL_DELTA)
    {
        throw std::invalid_argument(
            "distance must be nonnegative and representable as wheel deltas");
    }
    INPUT in{};
    in.type = INPUT_MOUSE;
    in.mi.dwExtraInfo = input_tag;
    // API names describe CONTENT motion. Win32 vertical wheel positive moves
    // content down; positive horizontal wheel moves content left.
    int sign;
    if (direction == "up" || direction == "down")
    {
        in.mi.dwFlags = MOUSEEVENTF_WHEEL;
        sign = direction == "up" ? -1 : 1;
    }
    else if (direction == "left" || direction == "right")
    {
        in.mi.dwFlags = MOUSEEVENTF_HWHEEL;
        sign = direction == "left" ? 1 : -1;
    }
    else
    {
        throw std::invalid_argument("Invalid scroll direction");
    }
    in.mi.mouseData = static_cast<DWORD>(sign * distance * WHEEL_DELTA);
    return in;
}
void send(INPUT &input)
{
    SetLastError(ERROR_SUCCESS);
    if (SendInput(1, &input, sizeof(INPUT)) != 1)
    {
        throw std::runtime_error(
            "SendInput did not insert the event (desktop access or UIPI may block input)");
    }
}
INPUT key_event(WORD key, bool up)
{
    INPUT in{};
    in.type = INPUT_KEYBOARD;
    in.ki.wVk = key;
    in.ki.dwExtraInfo = input_tag;
    if (up)
    {
        in.ki.dwFlags |= KEYEVENTF_KEYUP;
    }
    if (key == VK_RCONTROL || key == VK_RMENU || key == VK_LWIN || key == VK_RWIN ||
        (key >= VK_PRIOR && key <= VK_DOWN) || key == VK_INSERT || key == VK_DELETE ||
        key == VK_DIVIDE || key == VK_NUMLOCK)
    {
        in.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY;
    }
    return in;
}
constexpr uint32_t control = 1, shift = 2, alt = 4, win = 8;
const std::pair<uint32_t, WORD> modifiers[] = {{control, WORD(VK_CONTROL)},
                                               {shift, WORD(VK_SHIFT)},
                                               {alt, WORD(VK_MENU)},
                                               {win, WORD(VK_LWIN)}};
bool held(WORD key)
{
    return (GetAsyncKeyState(key) & 0x8000) != 0;
}
struct Stroke
{
    std::mutex mutex;
    std::vector<INPUT> releases;
    bool closed = false;
    void close()
    {
        std::lock_guard<std::mutex> lock(mutex);
        if (closed)
        {
            return;
        }
        std::exception_ptr first;
        // Remove only successfully delivered releases; failed releases remain
        // retryable by another end call/destruction.
        for (auto it = releases.begin(); it != releases.end();)
        {
            try
            {
                send(*it);
                it = releases.erase(it);
            }
            catch (...)
            {
                if (!first)
                {
                    first = std::current_exception();
                }
                ++it;
            }
        }
        closed = releases.empty();
        if (first)
        {
            std::rethrow_exception(first);
        }
    }
};
void stroke_destroy(PyObject *capsule) noexcept
{
    auto *stroke = static_cast<Stroke *>(PyCapsule_GetPointer(capsule, "scapkit.windows.stroke"));
    if (!stroke)
    {
        PyErr_Clear();
        return;
    }
    {
        NoPython detached;
        try
        {
            stroke->close();
        }
        catch (...)
        {
        }
        delete stroke;
    }
}
struct Clipboard
{
    HWND window = nullptr;
    bool opened = false;
    Clipboard()
    {
        window = CreateWindowExW(0, L"STATIC", L"scapkit clipboard", 0, 0, 0, 0, 0, HWND_MESSAGE,
                                 nullptr, GetModuleHandleW(nullptr), nullptr);
        if (!window)
        {
            winfail();
        }
        for (int i = 0; i < 25; ++i)
        {
            if (OpenClipboard(window))
            {
                opened = true;
                break;
            }
            Sleep(10);
        }
        if (!opened)
        {
            DestroyWindow(window);
            window = nullptr;
            throw std::runtime_error("Clipboard is busy");
        }
    }
    ~Clipboard()
    {
        if (opened)
        {
            CloseClipboard();
        }
        if (window)
        {
            DestroyWindow(window);
        }
    }
};
} // namespace

std::vector<Display> displays()
{
    DpiScope dpi;
    Enumeration e;
    BOOL ok = EnumDisplayMonitors(nullptr, nullptr, enum_monitor, reinterpret_cast<LPARAM>(&e));
    if (e.error)
    {
        std::rethrow_exception(e.error);
    }
    wincheck(ok);
    std::lock_guard<std::mutex> lock(display_mutex);
    std::map<std::wstring, uint32_t> active;
    for (auto &d : e.values)
    {
        auto found = display_ids.find(d.device);
        if (found != display_ids.end())
        {
            d.id = found->second;
        }
        else
        {
            if (!next_display_id)
            {
                throw std::runtime_error("Display ID space exhausted");
            }
            d.id = next_display_id++;
        }
        active[d.device] = d.id;
    }
    display_ids = std::move(active);
    return e.values;
}
Display display_by_id(uint32_t id)
{
    for (const auto &d : displays())
    {
        if (d.id == id)
        {
            return d;
        }
    }
    throw std::invalid_argument("Display ID is no longer active; call list_displays again");
}
std::vector<POINT> mouse_path(POINT start, POINT end, const std::vector<Display> &ds)
{
    int from = -1, to = -1;
    for (size_t i = 0; i < ds.size(); ++i)
    {
        if (contains(ds[i].rect, start))
        {
            from = static_cast<int>(i);
        }
        if (contains(ds[i].rect, end))
        {
            to = static_cast<int>(i);
        }
    }
    if (from < 0 || to < 0)
    {
        throw std::invalid_argument("Mouse point is outside all active displays");
    }
    if (from == to)
    {
        return {end};
    }
    std::vector<int> parent(ds.size(), -1);
    std::vector<POINT> enter(ds.size()), leave(ds.size());
    std::queue<int> queue;
    queue.push(from);
    parent[from] = from;
    while (!queue.empty() && parent[to] < 0)
    {
        int a = queue.front();
        queue.pop();
        const RECT &r = ds[a].rect;
        for (size_t b = 0; b < ds.size(); ++b)
        {
            if (parent[b] >= 0)
            {
                continue;
            }
            const RECT &s = ds[b].rect;
            LONG lowY = std::max(r.top, s.top), highY = std::min(r.bottom, s.bottom);
            LONG lowX = std::max(r.left, s.left), highX = std::min(r.right, s.right);
            POINT p{}, q{};
            bool adjacent = false;
            if (lowY < highY && (r.right == s.left || r.left == s.right))
            {
                LONG y = lowY + (highY - lowY) / 2;
                p = {r.right == s.left ? r.right - 1 : r.left, y};
                q = {r.right == s.left ? s.left : s.right - 1, y};
                adjacent = true;
            }
            else if (lowX < highX && (r.bottom == s.top || r.top == s.bottom))
            {
                LONG x = lowX + (highX - lowX) / 2;
                p = {x, r.bottom == s.top ? r.bottom - 1 : r.top};
                q = {x, r.bottom == s.top ? s.top : s.bottom - 1};
                adjacent = true;
            }
            else if (lowX < highX && lowY < highY)
            {
                p = q = {lowX, lowY};
                adjacent = true;
            }
            if (adjacent)
            {
                parent[b] = a;
                leave[b] = p;
                enter[b] = q;
                queue.push(static_cast<int>(b));
            }
        }
    }
    if (parent[to] < 0)
    {
        throw std::invalid_argument("No connected visible path between displays");
    }
    std::vector<int> chain;
    for (int i = to; i != from; i = parent[i])
    {
        chain.push_back(i);
    }
    std::reverse(chain.begin(), chain.end());
    std::vector<POINT> path;
    for (int i : chain)
    {
        path.push_back(leave[i]);
        path.push_back(enter[i]);
    }
    path.push_back(end);
    return path;
}
PyObject *py_list_displays(PyObject *, PyObject *)
{
    return boundary(
        [&]() -> PyObject *
        {
            std::vector<Display> ds;
            {
                NoPython detached;
                ds = displays();
            }
            PyObject *list = PyList_New(static_cast<Py_ssize_t>(ds.size()));
            if (!list)
            {
                return nullptr;
            }
            for (size_t i = 0; i < ds.size(); ++i)
            {
                const auto &d = ds[i];
                PyObject *item = Py_BuildValue(
                    "{s:I,s:i,s:i,s:i,s:i,s:d,s:O,s:s,s:d,s:i,s:i}", "id", d.id, "x", d.rect.left,
                    "y", d.rect.top, "width", d.rect.right - d.rect.left, "height",
                    d.rect.bottom - d.rect.top, "scale_factor", 1.0, "is_main",
                    d.primary ? Py_True : Py_False, "coordinate_unit", "physical_pixel",
                    "ui_scale_factor", d.ui_scale, "pixel_width", d.rect.right - d.rect.left,
                    "pixel_height", d.rect.bottom - d.rect.top);
                if (!item)
                {
                    Py_DECREF(list);
                    return nullptr;
                }
                PyList_SET_ITEM(list, static_cast<Py_ssize_t>(i), item);
            }
            return list;
        });
}
PyObject *py_get_mouse_position(PyObject *, PyObject *)
{
    return boundary(
        [&]() -> PyObject *
        {
            POINT p{};
            {
                NoPython detached;
                wincheck(GetPhysicalCursorPos(&p));
            }
            return Py_BuildValue("{s:i,s:i}", "x", p.x, "y", p.y);
        });
}
PyObject *py_move_mouse(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            int x, y;
            if (!PyArg_ParseTuple(args, "ii", &x, &y))
            {
                return nullptr;
            }
            {
                NoPython detached;
                DpiScope dpi;
                auto ds = displays();
                POINT p{x, y};
                if (std::none_of(ds.begin(), ds.end(),
                                 [&](const Display &d) { return contains(d.rect, p); }))
                {
                    throw std::invalid_argument("Mouse point is outside all active displays");
                }
                auto in = motion(p, bounds(ds), false);
                send(in);
            }
            Py_RETURN_NONE;
        });
}
PyObject *py_drag_mouse(PyObject *self, PyObject *args)
{
    return py_move_mouse(self, args);
}
PyObject *py_move_mouse_relative(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            int x, y;
            if (!PyArg_ParseTuple(args, "ii", &x, &y))
            {
                return nullptr;
            }
            {
                NoPython detached;
                auto in = motion({x, y}, {}, true);
                send(in);
            }
            Py_RETURN_NONE;
        });
}
PyObject *py_mouse_path(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            int x, y, a, b;
            if (!PyArg_ParseTuple(args, "iiii", &x, &y, &a, &b))
            {
                return nullptr;
            }
            std::vector<POINT> path;
            {
                NoPython detached;
                path = mouse_path({x, y}, {a, b}, displays());
            }
            PyObject *list = PyList_New(static_cast<Py_ssize_t>(path.size()));
            if (!list)
            {
                return nullptr;
            }
            for (size_t i = 0; i < path.size(); ++i)
            {
                auto *p = Py_BuildValue("{s:i,s:i}", "x", path[i].x, "y", path[i].y);
                if (!p)
                {
                    Py_DECREF(list);
                    return nullptr;
                }
                PyList_SET_ITEM(list, static_cast<Py_ssize_t>(i), p);
            }
            return list;
        });
}
PyObject *py_mouse_click(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            const char *key, *action;
            if (!PyArg_ParseTuple(args, "ss", &key, &action))
            {
                return nullptr;
            }
            bool up = std::strcmp(action, "up") == 0;
            if (!up && std::strcmp(action, "down"))
            {
                throw std::invalid_argument("Invalid mouse action");
            }
            INPUT in{};
            in.type = INPUT_MOUSE;
            in.mi.dwExtraInfo = input_tag;
            if (!std::strcmp(key, "left"))
            {
                in.mi.dwFlags = up ? MOUSEEVENTF_LEFTUP : MOUSEEVENTF_LEFTDOWN;
            }
            else if (!std::strcmp(key, "right"))
            {
                in.mi.dwFlags = up ? MOUSEEVENTF_RIGHTUP : MOUSEEVENTF_RIGHTDOWN;
            }
            else
            {
                throw std::invalid_argument("Invalid mouse button");
            }
            {
                NoPython detached;
                send(in);
            }
            Py_RETURN_NONE;
        });
}
PyObject *py_mouse_scroll(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            const char *direction;
            int distance;
            if (!PyArg_ParseTuple(args, "si", &direction, &distance))
            {
                return nullptr;
            }
            auto in = wheel(direction, distance);
            if (distance)
            {
                NoPython detached;
                send(in);
            }
            Py_RETURN_NONE;
        });
}
PyObject *py_keyboard_click(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            int code, flags = 0;
            const char *action;
            if (!PyArg_ParseTuple(args, "is|i", &code, &action, &flags))
            {
                return nullptr;
            }
            if (code < 1 || code > 254 || flags < 0 || (flags & ~15))
            {
                throw std::invalid_argument("Invalid key_code or modifier flags");
            }
            bool up = std::strcmp(action, "up") == 0;
            if (!up && std::strcmp(action, "down"))
            {
                throw std::invalid_argument("Invalid keyboard action");
            }
            {
                NoPython detached;
                for (auto m : modifiers)
                {
                    if ((flags & m.first) && code != m.second && !held(m.second))
                    {
                        throw std::invalid_argument("Raw modifiers must already be held; use "
                                                    "key_combo for balanced shortcuts");
                    }
                }
                auto in = key_event(static_cast<WORD>(code), up);
                send(in);
            }
            Py_RETURN_NONE;
        });
}
PyObject *py_keyboard_begin(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            int code, flags;
            if (!PyArg_ParseTuple(args, "ii", &code, &flags))
            {
                return nullptr;
            }
            if (code < 1 || code > 254 || flags < 0 || (flags & ~15))
            {
                throw std::invalid_argument("Invalid key_code or modifier flags");
            }
            auto owned = std::make_unique<Stroke>();
            owned->releases.reserve(5);
            auto *capsule = PyCapsule_New(owned.get(), "scapkit.windows.stroke", stroke_destroy);
            if (!capsule)
            {
                return nullptr;
            }
            Stroke *stroke = owned.release();
            try
            {
                NoPython detached;
                if (held(static_cast<WORD>(code)))
                {
                    throw std::runtime_error("Requested key is already held");
                }
                // Preallocate everything before posting the first down.
                for (auto m : modifiers)
                {
                    if ((flags & m.first) && code != m.second && !held(m.second))
                    {
                        stroke->releases.push_back(key_event(m.second, true));
                    }
                }
                stroke->releases.push_back(key_event(static_cast<WORD>(code), true));
                size_t sent = 0;
                try
                {
                    for (auto release : stroke->releases)
                    {
                        auto down = release;
                        down.ki.dwFlags &= ~KEYEVENTF_KEYUP;
                        send(down);
                        ++sent;
                    }
                }
                catch (...)
                {
                    stroke->releases.resize(sent);
                    std::reverse(stroke->releases.begin(), stroke->releases.end());
                    throw;
                }
                std::reverse(stroke->releases.begin(), stroke->releases.end());
            }
            catch (...)
            {
                Py_DECREF(capsule);
                throw;
            }
            return capsule;
        });
}
PyObject *py_keyboard_end(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *capsule;
            if (!PyArg_ParseTuple(args, "O", &capsule))
            {
                return nullptr;
            }
            auto *stroke =
                static_cast<Stroke *>(PyCapsule_GetPointer(capsule, "scapkit.windows.stroke"));
            if (!stroke)
            {
                return nullptr;
            }
            {
                NoPython detached;
                stroke->close();
            }
            Py_RETURN_NONE;
        });
}
PyObject *py_check_permission(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            const char *name;
            if (!PyArg_ParseTuple(args, "s", &name))
            {
                return nullptr;
            }
            if (std::strcmp(name, "Accessibility") && std::strcmp(name, "ScreenCapture"))
            {
                throw std::invalid_argument("Invalid permission type");
            }
            Py_RETURN_TRUE;
        });
}
PyObject *py_set_clipboard(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *obj;
            if (!PyArg_ParseTuple(args, "U", &obj))
            {
                return nullptr;
            }
            Py_ssize_t length;
            wchar_t *raw = PyUnicode_AsWideCharString(obj, &length);
            if (!raw)
            {
                return nullptr;
            }
            std::wstring text;
            try
            {
                text.assign(raw, static_cast<size_t>(length));
            }
            catch (...)
            {
                PyMem_Free(raw);
                throw;
            }
            PyMem_Free(raw);
            if (text.find(L'\0') != std::wstring::npos)
            {
                throw std::invalid_argument("Clipboard text cannot contain NUL");
            }
            {
                NoPython detached;
                std::exception_ptr error;
                std::thread worker(
                    [&]
                    {
                        try
                        {
                            Clipboard clip;
                            size_t bytes = (text.size() + 1) * sizeof(wchar_t);
                            HGLOBAL memory = GlobalAlloc(GMEM_MOVEABLE, bytes);
                            if (!memory)
                            {
                                throw std::bad_alloc();
                            }
                            try
                            {
                                void *p = GlobalLock(memory);
                                if (!p)
                                {
                                    winfail();
                                }
                                std::memcpy(p, text.c_str(), bytes);
                                GlobalUnlock(memory);
                                wincheck(EmptyClipboard());
                                if (!SetClipboardData(CF_UNICODETEXT, memory))
                                {
                                    winfail();
                                }
                                memory = nullptr;
                            }
                            catch (...)
                            {
                                if (memory)
                                {
                                    GlobalFree(memory);
                                }
                                throw;
                            }
                        }
                        catch (...)
                        {
                            error = std::current_exception();
                        }
                    });
                worker.join();
                if (error)
                {
                    std::rethrow_exception(error);
                }
            }
            Py_RETURN_NONE;
        });
}
PyObject *py_get_clipboard(PyObject *, PyObject *)
{
    return boundary(
        [&]() -> PyObject *
        {
            std::wstring text;
            {
                NoPython detached;
                std::exception_ptr error;
                std::thread worker(
                    [&]
                    {
                        try
                        {
                            Clipboard clip;
                            if (!IsClipboardFormatAvailable(CF_UNICODETEXT))
                            {
                                return;
                            }
                            HANDLE h = GetClipboardData(CF_UNICODETEXT);
                            if (!h)
                            {
                                winfail();
                            }
                            auto *p = static_cast<const wchar_t *>(GlobalLock(h));
                            if (!p)
                            {
                                winfail();
                            }
                            size_t max = GlobalSize(h) / sizeof(wchar_t), n = 0;
                            while (n < max && p[n])
                            {
                                ++n;
                            }
                            try
                            {
                                text.assign(p, n);
                            }
                            catch (...)
                            {
                                GlobalUnlock(h);
                                throw;
                            }
                            GlobalUnlock(h);
                        }
                        catch (...)
                        {
                            error = std::current_exception();
                        }
                    });
                worker.join();
                if (error)
                {
                    std::rethrow_exception(error);
                }
            }
            return PyUnicode_FromWideChar(text.data(), static_cast<Py_ssize_t>(text.size()));
        });
}
#ifdef SCAPKIT_TESTING
PyObject *py_test_input(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            const char *kind;
            PyObject *values;
            if (!PyArg_ParseTuple(args, "sO!", &kind, &PyTuple_Type, &values))
            {
                return nullptr;
            }
            INPUT in{};
            if (!std::strcmp(kind, "wheel"))
            {
                const char *dir;
                int n;
                if (!PyArg_ParseTuple(values, "si", &dir, &n))
                {
                    return nullptr;
                }
                in = wheel(dir, n);
            }
            else if (!std::strcmp(kind, "move"))
            {
                int x, y, l, t, r, b;
                if (!PyArg_ParseTuple(values, "iiiiii", &x, &y, &l, &t, &r, &b))
                {
                    return nullptr;
                }
                in = motion({x, y}, {l, t, r, b}, false);
            }
            else if (!std::strcmp(kind, "path"))
            {
                int x, y, a, b;
                PyObject *rectangles;
                if (!PyArg_ParseTuple(values, "iiiiO!", &x, &y, &a, &b, &PyTuple_Type, &rectangles))
                {
                    return nullptr;
                }
                std::vector<Display> ds;
                for (Py_ssize_t i = 0; i < PyTuple_Size(rectangles); ++i)
                {
                    int left, top, right, bottom;
                    if (!PyArg_ParseTuple(PyTuple_GetItem(rectangles, i), "iiii", &left, &top,
                                          &right, &bottom))
                    {
                        return nullptr;
                    }
                    if (right <= left || bottom <= top)
                    {
                        throw std::invalid_argument("Invalid synthetic monitor rectangle");
                    }
                    ds.push_back({0, nullptr, {left, top, right, bottom}, false, L""});
                }
                auto path = mouse_path({x, y}, {a, b}, ds);
                auto *list = PyList_New(static_cast<Py_ssize_t>(path.size()));
                if (!list)
                {
                    return nullptr;
                }
                for (size_t i = 0; i < path.size(); ++i)
                {
                    auto *point = Py_BuildValue("ii", path[i].x, path[i].y);
                    if (!point)
                    {
                        Py_DECREF(list);
                        return nullptr;
                    }
                    PyList_SET_ITEM(list, static_cast<Py_ssize_t>(i), point);
                }
                return list;
            }
            else
            {
                throw std::invalid_argument("Invalid offline input test");
            }
            return Py_BuildValue("{s:i,s:i,s:k,s:i}", "x", in.mi.dx, "y", in.mi.dy, "flags",
                                 in.mi.dwFlags, "wheel", static_cast<int32_t>(in.mi.mouseData));
        });
}
#endif
} // namespace scap
