// Standalone Win32/UIA fixture. Build with the Windows SDK and MSVC, x64 or ARM64.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <windowsx.h>
#include <commctrl.h>
// WIN32_LEAN_AND_MEAN excludes the COM/OLE declarations UIAutomation.h needs.
#include <ole2.h>
#include <UIAutomation.h>
#include <sddl.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cwchar>
#include <cwctype>
#include <deque>
#include <filesystem>
#include <iomanip>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifndef FIXTURE_BUILD_SHA256
#define FIXTURE_BUILD_SHA256 "unversioned"
#endif

namespace {
constexpr size_t EventLimit = 4096;
constexpr int TextID = 100, IncrementID = 101;
constexpr int MoveID = 110, ResizeID = 111, MinimizeID = 112, ModalID = 113, RecreateID = 114;
constexpr int ReopenA1 = 201, ReopenA2 = 202, ReopenB = 203, QuitID = 204;
constexpr wchar_t ClassName[] = L"ScapkitWindowFixtureV1";
constexpr wchar_t CanvasClass[] = L"ScapkitWindowFixtureCanvasV1";
constexpr wchar_t ProviderProperty[] = L"ScapkitFixtureProviderV1";

void require(bool condition, const char *message) {
    if (!condition) { throw std::runtime_error(message); }
}
uint64_t nowNS() {
    LARGE_INTEGER counter{}, frequency{};
    QueryPerformanceCounter(&counter);
    QueryPerformanceFrequency(&frequency);
    const auto ticks = static_cast<uint64_t>(counter.QuadPart);
    const auto rate = static_cast<uint64_t>(frequency.QuadPart);
    return (ticks / rate) * 1000000000 + (ticks % rate) * 1000000000 / rate;
}
std::wstring token() {
    GUID guid{};
    require(SUCCEEDED(CoCreateGuid(&guid)), "UUID generation failed");
    wchar_t buffer[40]{};
    require(StringFromGUID2(guid, buffer, 40) != 0, "UUID formatting failed");
    std::wstring result(buffer + 1, 36);
    for (auto &ch : result) { ch = static_cast<wchar_t>(towlower(ch)); }
    return result;
}
std::string utf8(const std::wstring &text) {
    if (text.empty()) { return {}; }
    const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(),
        static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    require(size > 0, "invalid UTF-16 text");
    std::string result(static_cast<size_t>(size), '\0');
    require(WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(),
        static_cast<int>(text.size()), result.data(), size, nullptr, nullptr) == size, "UTF-8 conversion failed");
    return result;
}
std::string quote(const std::wstring &text) {
    std::ostringstream out;
    out << '"';
    for (unsigned char ch : utf8(text)) {
        if (ch == '"' || ch == '\\') { out << '\\' << static_cast<char>(ch); }
        else if (ch < 32) { out << "\\u00" << std::hex << std::setw(2) << std::setfill('0') << unsigned(ch); }
        else { out << static_cast<char>(ch); }
    }
    out << '"';
    return out.str();
}
std::wstring hwndString(HWND hwnd) {
    std::wostringstream out;
    out << L"0x" << std::hex << reinterpret_cast<uintptr_t>(hwnd);
    return out.str();
}
std::wstring windowText(HWND hwnd) {
    const int size = GetWindowTextLengthW(hwnd);
    std::wstring result(static_cast<size_t>(size) + 1, L'\0');
    const int copied = GetWindowTextW(hwnd, result.data(), size + 1);
    result.resize(static_cast<size_t>(copied));
    return result;
}
struct Record {
    std::wstring slot, instance = token(), title, text, nativeID = L"0";
    HWND hwnd = nullptr, edit = nullptr, counter = nullptr;
    uint64_t count = 0;
    bool closed = false, modal = false;
    COLORREF color = RGB(165, 212, 255);
    HBRUSH brush = nullptr;
    std::vector<POINT> points;
    std::map<std::wstring, uint64_t> totals;
    std::wstring id(const wchar_t *part) const {
        return L"scapkit.fixture." + slot + L"." + instance + L"." + part;
    }
    ~Record() { if (brush) { DeleteObject(brush); } }
};
struct Event {
    uint64_t sequence, ns;
    std::wstring instance, type;
    std::string data;
};

// ACLs contain only the current token's user SID. Snapshots are read-only to clients
// by convention; this is a same-user fixture, not an adversarial security boundary.
class StateDirectory {
    std::wstring path_;
    PSECURITY_DESCRIPTOR descriptor_ = nullptr;
    HANDLE directory_ = INVALID_HANDLE_VALUE;
public:
    explicit StateDirectory(const std::wstring &path) : path_(path) {
        require(std::filesystem::path(path).is_absolute(), "state directory must be absolute");
        HANDLE processToken = nullptr;
        require(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &processToken) != FALSE, "token query failed");
        DWORD needed = 0;
        GetTokenInformation(processToken, TokenUser, nullptr, 0, &needed);
        std::vector<BYTE> bytes(needed);
        const BOOL gotUser = GetTokenInformation(processToken, TokenUser, bytes.data(), needed, &needed);
        CloseHandle(processToken);
        require(gotUser != FALSE, "user SID query failed");
        LPWSTR sid = nullptr;
        require(ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER *>(bytes.data())->User.Sid, &sid) != FALSE,
                "SID conversion failed");
        const std::wstring sddl = L"O:" + std::wstring(sid) + L"D:P(A;OICI;FA;;;" + std::wstring(sid) + L")";
        LocalFree(sid);
        require(ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.c_str(), SDDL_REVISION_1,
                &descriptor_, nullptr) != FALSE, "ACL construction failed");
        SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), descriptor_, FALSE};
        if (!CreateDirectoryW(path_.c_str(), &attributes)) {
            LocalFree(descriptor_);
            descriptor_ = nullptr;
            throw std::runtime_error("state directory must not exist and its parent must exist");
        }
        directory_ = CreateFileW(path_.c_str(), FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE,
            nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        BY_HANDLE_FILE_INFORMATION info{};
        require(directory_ != INVALID_HANDLE_VALUE && GetFileInformationByHandle(directory_, &info) &&
                !(info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT), "unsafe state directory");
    }
    ~StateDirectory() {
        if (directory_ != INVALID_HANDLE_VALUE) { CloseHandle(directory_); }
        if (descriptor_) { LocalFree(descriptor_); }
    }
    void write(const std::string &data) const {
        SECURITY_ATTRIBUTES attributes{sizeof(SECURITY_ATTRIBUTES), descriptor_, FALSE};
        const std::wstring temporary = path_ + L"\\snapshot-" + token();
        HANDLE file = CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, &attributes, CREATE_NEW,
                                 FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        require(file != INVALID_HANDLE_VALUE, "cannot create private snapshot");
        DWORD written = 0;
        const BOOL ok = WriteFile(file, data.data(), static_cast<DWORD>(data.size()), &written, nullptr);
        const BOOL flushed = FlushFileBuffers(file);
        CloseHandle(file);
        require(ok && written == data.size() && flushed, "snapshot write failed");
        require(MoveFileExW(temporary.c_str(), (path_ + L"\\state.json").c_str(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != FALSE, "snapshot publish failed");
    }
};

struct Fixture {
    std::vector<std::shared_ptr<Record>> records;
    std::deque<Event> events;
    std::unique_ptr<StateDirectory> directory;
    std::wstring role = L"a", processToken = token(), processStart;
    bool offline = false, background = false, running = true, recreating = false;
    uint64_t sequence = 0, dropped = 0, snapshotSequence = 0;
    Fixture() {
        FILETIME start{}, exit{}, kernel{}, user{};
        require(GetProcessTimes(GetCurrentProcess(), &start, &exit, &kernel, &user) != FALSE,
                "cannot read own process start time");
        processStart = std::to_wstring((static_cast<uint64_t>(start.dwHighDateTime) << 32) | start.dwLowDateTime);
    }
    std::shared_ptr<Record> newRecord(const std::wstring &slot) {
        require(records.size() < 64, "64 window instances reached; start a new fixture run");
        auto record = std::make_shared<Record>();
        record->slot = slot;
        record->title = L"SCAPKIT Fixture " + slot;
        record->text = slot + L" initial - synthetic only";
        record->color = slot == L"A2" ? RGB(255, 209, 148) :
                        (slot == L"B" ? RGB(184, 255, 189) : RGB(165, 212, 255));
        records.push_back(record);
        return record;
    }
    void event(const std::wstring &type, Record *record = nullptr, const std::string &data = "{}") {
        if (record) { ++record->totals[type]; }
        events.push_back({++sequence, nowNS(), record ? record->instance : L"", type, data});
        if (events.size() > EventLimit) { events.pop_front(); ++dropped; }
    }
    void publish() {
        if (!directory) { return; }
        std::ostringstream out;
        out << "{\"schema_version\":1,\"fixture_version\":\"window-fixture-v1\",\"build_sha256\":\""
            << FIXTURE_BUILD_SHA256 << "\",\"platform\":\"windows\",\"pid\":" << GetCurrentProcessId()
            << ",\"process_token\":" << quote(processToken) << ",\"process_start\":" << quote(processStart)
            << ",\"role\":" << quote(role) << ",\"background\":" << (background ? "true" : "false")
            << ",\"running\":" << (running ? "true" : "false")
            << ",\"execution\":\"" << (offline ? "offline_probe" : "desktop")
            << "\",\"desktop_validation\":\"not_run\",\"snapshot_sequence\":" << ++snapshotSequence
            << ",\"observed_monotonic_ns\":" << nowNS()
            << ",\"active\":" << (!offline && GetActiveWindow() ? "true" : "false") << ",\"windows\":[";
        bool first = true;
        for (const auto &record : records) {
            if (!first) { out << ','; } first = false;
            RECT rect{};
            if (record->hwnd) { GetWindowRect(record->hwnd, &rect); }
            out << "{\"slot\":" << quote(record->slot) << ",\"instance_token\":" << quote(record->instance)
                << ",\"title\":" << quote(record->title) << ",\"native_id\":" << quote(record->nativeID)
                << ",\"text\":" << quote(record->text) << ",\"count\":" << record->count
                << ",\"closed\":" << (record->closed ? "true" : "false")
                << ",\"modal\":" << (record->modal ? "true" : "false")
                << ",\"key\":" << (record->hwnd && GetActiveWindow() == record->hwnd ? "true" : "false")
                << ",\"visible\":" << (record->hwnd && IsWindowVisible(record->hwnd) ? "true" : "false")
                << ",\"minimized\":" << (record->hwnd && IsIconic(record->hwnd) ? "true" : "false")
                << ",\"bounds\":[" << rect.left << ',' << rect.top << ',' << rect.right - rect.left
                << ',' << rect.bottom - rect.top << "],\"coordinate_unit\":\"physical_pixels\""
                << ",\"accessibility\":{\"api\":\"UIA\",\"window\":" << quote(record->id(L"window"))
                << ",\"text\":" << quote(record->id(L"text")) << ",\"increment\":"
                << quote(record->id(L"increment")) << "},\"event_totals\":{";
            bool firstTotal = true;
            for (const auto &[kind, count] : record->totals) {
                if (!firstTotal) { out << ','; } firstTotal = false;
                out << quote(kind) << ':' << count;
            }
            out << "}}";
        }
        out << "],\"event_limit\":" << EventLimit << ",\"events_dropped\":" << dropped
            << ",\"event_sequence\":" << sequence << ",\"events\":[";
        first = true;
        for (const auto &event : events) {
            if (!first) { out << ','; } first = false;
            out << "{\"seq\":" << event.sequence << ",\"monotonic_ns\":" << event.ns
                << ",\"window_token\":" << (event.instance.empty() ? "null" : quote(event.instance))
                << ",\"type\":" << quote(event.type) << ",\"data\":" << event.data << '}';
        }
        out << "]}";
        directory->write(out.str());
    }
};
Fixture *fixture = nullptr; // Main STA owns all records, HWNDs and UIA callbacks.

enum class Kind { Window, Text, Increment };
class Provider final : public IRawElementProviderSimple, public IValueProvider, public IInvokeProvider {
    std::atomic<ULONG> references_{1};
    HWND hwnd_;
    Kind kind_;
    std::wstring id_, name_;
    bool live() const { return hwnd_ && GetPropW(hwnd_, ProviderProperty) == this; }
public:
    Provider(HWND hwnd, Kind kind, std::wstring id, std::wstring name)
        : hwnd_(hwnd), kind_(kind), id_(std::move(id)), name_(std::move(name)) {}
    void detach() { hwnd_ = nullptr; UiaDisconnectProvider(this); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **value) override {
        if (!value) { return E_POINTER; }
        *value = nullptr;
        if (iid == __uuidof(IUnknown) || iid == __uuidof(IRawElementProviderSimple)) {
            *value = static_cast<IRawElementProviderSimple *>(this);
        } else if (iid == __uuidof(IValueProvider) && kind_ == Kind::Text) {
            *value = static_cast<IValueProvider *>(this);
        } else if (iid == __uuidof(IInvokeProvider) && kind_ == Kind::Increment) {
            *value = static_cast<IInvokeProvider *>(this);
        } else { return E_NOINTERFACE; }
        AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG count = --references_;
        if (!count) { delete this; }
        return count;
    }
    HRESULT STDMETHODCALLTYPE get_ProviderOptions(ProviderOptions *value) override {
        if (!value) { return E_POINTER; }
        *value = static_cast<ProviderOptions>(ProviderOptions_ServerSideProvider | ProviderOptions_UseComThreading);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetPatternProvider(PATTERNID pattern, IUnknown **value) override {
        if (!value) { return E_POINTER; }
        *value = nullptr;
        if (!live()) { return UIA_E_ELEMENTNOTAVAILABLE; }
        if (pattern == UIA_ValuePatternId && kind_ == Kind::Text) { *value = static_cast<IValueProvider *>(this); }
        if (pattern == UIA_InvokePatternId && kind_ == Kind::Increment) { *value = static_cast<IInvokeProvider *>(this); }
        if (*value) { AddRef(); }
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetPropertyValue(PROPERTYID property, VARIANT *value) override {
        if (!value) { return E_POINTER; }
        VariantInit(value);
        if (!live()) { return UIA_E_ELEMENTNOTAVAILABLE; }
        if (property == UIA_AutomationIdPropertyId || property == UIA_NamePropertyId) {
            value->vt = VT_BSTR;
            value->bstrVal = SysAllocString((property == UIA_AutomationIdPropertyId ? id_ : name_).c_str());
            if (!value->bstrVal) { return E_OUTOFMEMORY; }
        } else if (property == UIA_ControlTypePropertyId) {
            value->vt = VT_I4;
            value->lVal = kind_ == Kind::Text ? UIA_EditControlTypeId :
                            (kind_ == Kind::Increment ? UIA_ButtonControlTypeId : UIA_WindowControlTypeId);
        } else if (property == UIA_IsControlElementPropertyId || property == UIA_IsContentElementPropertyId) {
            value->vt = VT_BOOL; value->boolVal = VARIANT_TRUE;
        } else if (property == UIA_IsPasswordPropertyId) {
            value->vt = VT_BOOL; value->boolVal = VARIANT_FALSE;
        }
        return S_OK; // Other properties come from this HWND's host provider.
    }
    HRESULT STDMETHODCALLTYPE get_HostRawElementProvider(IRawElementProviderSimple **value) override {
        if (!value) { return E_POINTER; }
        *value = nullptr;
        if (!live()) { return UIA_E_ELEMENTNOTAVAILABLE; }
        return UiaHostProviderFromHwnd(hwnd_, value);
    }
    HRESULT STDMETHODCALLTYPE SetValue(LPCWSTR value) override {
        if (!live()) { return UIA_E_ELEMENTNOTAVAILABLE; }
        if (!value || wcslen(value) > 32767) { return E_INVALIDARG; }
        if (!IsWindowEnabled(hwnd_) || !IsWindowEnabled(GetParent(hwnd_))) { return UIA_E_ELEMENTNOTENABLED; }
        if (GetWindowLongPtrW(hwnd_, GWL_STYLE) & ES_READONLY) { return UIA_E_NOTSUPPORTED; }
        // This is the native EDIT's actual value, not a parallel test state setter.
        return SetWindowTextW(hwnd_, value) ? S_OK : E_FAIL;
    }
    HRESULT STDMETHODCALLTYPE get_Value(BSTR *value) override {
        if (!value) { return E_POINTER; }
        *value = nullptr;
        if (!live()) { return UIA_E_ELEMENTNOTAVAILABLE; }
        try { *value = SysAllocString(windowText(hwnd_).c_str()); }
        catch (...) { return E_OUTOFMEMORY; }
        return *value ? S_OK : E_OUTOFMEMORY;
    }
    HRESULT STDMETHODCALLTYPE get_IsReadOnly(BOOL *value) override {
        if (!value) { return E_POINTER; }
        if (!live()) { return UIA_E_ELEMENTNOTAVAILABLE; }
        *value = (GetWindowLongPtrW(hwnd_, GWL_STYLE) & ES_READONLY) ? TRUE : FALSE;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE Invoke() override {
        if (!live()) { return UIA_E_ELEMENTNOTAVAILABLE; }
        if (!IsWindowEnabled(hwnd_) || !IsWindowEnabled(GetParent(hwnd_))) { return UIA_E_ELEMENTNOTENABLED; }
        // Deliver this real button's semantic notification. No mouse/key messages or focus changes.
        SendMessageW(GetParent(hwnd_), WM_COMMAND, MAKEWPARAM(IncrementID, BN_CLICKED), reinterpret_cast<LPARAM>(hwnd_));
        return S_OK;
    }
};

Provider *provider(HWND hwnd) { return static_cast<Provider *>(GetPropW(hwnd, ProviderProperty)); }
void attachProvider(HWND hwnd, Kind kind, const std::wstring &id, const std::wstring &name) {
    auto *value = new Provider(hwnd, kind, id, name);
    if (!SetPropW(hwnd, ProviderProperty, value)) { value->Release(); throw std::runtime_error("provider attachment failed"); }
}
void detachProvider(HWND hwnd) {
    if (auto *value = provider(hwnd)) {
        RemovePropW(hwnd, ProviderProperty);
        value->detach();
        UiaReturnRawElementProvider(hwnd, 0, 0, nullptr);
        value->Release();
    }
}
void observeText(Record *record) {
    if (!record->edit || record->closed) { return; }
    const auto value = windowText(record->edit);
    if (value == record->text) { return; }
    VARIANT oldValue{}, newValue{};
    oldValue.vt = newValue.vt = VT_BSTR;
    oldValue.bstrVal = SysAllocString(record->text.c_str());
    newValue.bstrVal = SysAllocString(value.c_str());
    record->text = value;
    fixture->event(L"text_changed", record);
    if (auto *p = provider(record->edit); p && oldValue.bstrVal && newValue.bstrVal) {
        UiaRaiseAutomationPropertyChangedEvent(p, UIA_ValueValuePropertyId, oldValue, newValue);
    }
    VariantClear(&oldValue); VariantClear(&newValue);
}
void createWindow(const std::wstring &slot);
LRESULT CALLBACK childProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam, UINT_PTR id, DWORD_PTR data) {
    auto *record = reinterpret_cast<Record *>(data);
    try {
        if (message == WM_COPY || message == WM_CUT || message == WM_PASTE) {
            return 0; // No manual clipboard access, including the EDIT context menu.
        }
        if (message == WM_GETOBJECT && lparam == UiaRootObjectId && provider(hwnd)) {
            return UiaReturnRawElementProvider(hwnd, wparam, lparam, provider(hwnd));
        }
        if (message == WM_SETFOCUS || message == WM_KILLFOCUS) {
            fixture->event(message == WM_SETFOCUS ? L"focus_gained" : L"focus_lost", record);
        }
        if (message == WM_NCDESTROY) { detachProvider(hwnd); RemoveWindowSubclass(hwnd, childProc, id); }
        const LRESULT result = DefSubclassProc(hwnd, message, wparam, lparam);
        if (message == WM_SETTEXT && hwnd == record->edit) { observeText(record); }
        return result;
    } catch (...) { std::fputs("window-fixture: control callback failed\n", stderr); ExitProcess(1); }
}
LRESULT CALLBACK canvasProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    auto *record = reinterpret_cast<Record *>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        record = static_cast<Record *>(reinterpret_cast<CREATESTRUCTW *>(lparam)->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(record));
    }
    try {
        if (record && (message == WM_LBUTTONDOWN || message == WM_LBUTTONUP ||
                       (message == WM_MOUSEMOVE && (wparam & MK_LBUTTON)))) {
            POINT point{GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)};
            if (message == WM_LBUTTONDOWN) { record->points.clear(); }
            if (record->points.size() == 512) { record->points.erase(record->points.begin()); }
            record->points.push_back(point);
            fixture->event(message == WM_LBUTTONDOWN ? L"canvas_down" :
                           (message == WM_LBUTTONUP ? L"canvas_up" : L"canvas_drag"), record,
                           "{\"x\":" + std::to_string(point.x) + ",\"y\":" + std::to_string(point.y) + "}");
            InvalidateRect(hwnd, nullptr, TRUE);
        }
        if (message == WM_PAINT) {
            PAINTSTRUCT paint{};
            HDC dc = BeginPaint(hwnd, &paint);
            if (record && record->points.size() > 1) { Polyline(dc, record->points.data(), static_cast<int>(record->points.size())); }
            EndPaint(hwnd, &paint); return 0;
        }
        return DefWindowProcW(hwnd, message, wparam, lparam);
    } catch (...) { std::fputs("window-fixture: canvas callback failed\n", stderr); ExitProcess(1); }
}
LRESULT CALLBACK windowProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    auto *record = reinterpret_cast<Record *>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        record = static_cast<Record *>(reinterpret_cast<CREATESTRUCTW *>(lparam)->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(record));
        record->hwnd = hwnd;
    }
    try {
        if (!record) { return DefWindowProcW(hwnd, message, wparam, lparam); }
        if (message == WM_GETOBJECT && lparam == UiaRootObjectId && provider(hwnd)) {
            return UiaReturnRawElementProvider(hwnd, wparam, lparam, provider(hwnd));
        }
        if (message == WM_ACTIVATE) { fixture->event(LOWORD(wparam) == WA_INACTIVE ? L"key_lost" : L"key_gained", record); }
        if (message == WM_ACTIVATEAPP) { fixture->event(wparam ? L"app_activated" : L"app_deactivated"); }
        if (message == WM_SETFOCUS || message == WM_KILLFOCUS) {
            fixture->event(message == WM_SETFOCUS ? L"focus_gained" : L"focus_lost", record);
        }
        if (message == WM_MOVE) { fixture->event(L"moved", record); }
        if (message == WM_SIZE) { fixture->event(wparam == SIZE_MINIMIZED ? L"minimized" : L"resized", record); }
        if (message == WM_ERASEBKGND && record->brush) {
            RECT rect{}; GetClientRect(hwnd, &rect); FillRect(reinterpret_cast<HDC>(wparam), &rect, record->brush); return 1;
        }
        if (message == WM_CTLCOLORSTATIC && record->brush) {
            SetBkColor(reinterpret_cast<HDC>(wparam), record->color); return reinterpret_cast<LRESULT>(record->brush);
        }
        if (message == WM_TIMER) { observeText(record); fixture->publish(); return 0; }
        if (message == WM_COMMAND) {
            const int command = LOWORD(wparam);
            if (command == TextID && HIWORD(wparam) == EN_CHANGE) { observeText(record); }
            if (command == IncrementID && HIWORD(wparam) == BN_CLICKED &&
                reinterpret_cast<HWND>(lparam) == GetDlgItem(hwnd, IncrementID) && !record->modal) {
                ++record->count;
                SetWindowTextW(record->counter, (L"Count: " + std::to_wstring(record->count)).c_str());
                fixture->event(L"increment", record);
                if (auto *p = provider(GetDlgItem(hwnd, IncrementID))) { UiaRaiseAutomationEvent(p, UIA_Invoke_InvokedEventId); }
            }
            RECT rect{}; GetWindowRect(hwnd, &rect);
            if (command == MoveID) { SetWindowPos(hwnd, nullptr, rect.left + 30, rect.top + 20, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE); }
            if (command == ResizeID) { SetWindowPos(hwnd, nullptr, 0, 0, rect.right - rect.left > 600 ? 580 : 650, 440, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE); }
            if (command == MinimizeID) { ShowWindow(hwnd, SW_MINIMIZE); }
            if (command == ModalID && !record->modal) {
                record->modal = true; fixture->event(L"modal_opened", record); fixture->publish();
                MessageBoxW(hwnd, L"Close this manual modal before background action acceptance.", record->title.c_str(), MB_OK | MB_APPLMODAL);
                record->modal = false; fixture->event(L"modal_closed", record);
            }
            if (command == RecreateID && !record->modal) {
                fixture->recreating = true; DestroyWindow(hwnd); createWindow(record->slot); fixture->recreating = false;
            }
            if (command == ReopenA1) { createWindow(L"A1"); }
            if (command == ReopenA2) { createWindow(L"A2"); }
            if (command == ReopenB) { createWindow(L"B"); }
            if (command == QuitID) {
                for (const auto &item : fixture->records) { if (item->hwnd) { DestroyWindow(item->hwnd); } }
            }
            fixture->publish(); return 0;
        }
        if (message == WM_DESTROY) {
            KillTimer(hwnd, 1); observeText(record); record->closed = true; fixture->event(L"closed", record);
            bool anyLive = false;
            for (const auto &item : fixture->records) { anyLive = anyLive || !item->closed; }
            if (!anyLive && !fixture->recreating) { PostQuitMessage(0); }
        }
        if (message == WM_NCDESTROY) {
            detachProvider(hwnd); record->hwnd = record->edit = record->counter = nullptr;
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        }
        return DefWindowProcW(hwnd, message, wparam, lparam);
    } catch (...) { std::fputs("window-fixture: window callback failed\n", stderr); ExitProcess(1); }
}
HWND child(Record *record, const wchar_t *className, const wchar_t *text, int id, DWORD style,
           int x, int y, int width, int height) {
    HWND hwnd = CreateWindowExW(0, className, text, WS_CHILD | WS_VISIBLE | style, x, y, width, height,
        record->hwnd, reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), GetModuleHandleW(nullptr), nullptr);
    require(hwnd != nullptr, "control creation failed");
    require(SetWindowSubclass(hwnd, childProc, 1, reinterpret_cast<DWORD_PTR>(record)) != FALSE, "control subclass failed");
    return hwnd;
}
void createWindow(const std::wstring &slot) {
    for (const auto &existing : fixture->records) { if (existing->slot == slot && !existing->closed) { return; } }
    auto record = fixture->newRecord(slot);
    record->brush = CreateSolidBrush(record->color);
    require(record->brush != nullptr, "brush allocation failed");
    HMENU menu = CreateMenu();
    require(menu != nullptr, "menu creation failed");
    if (fixture->role == L"a") {
        AppendMenuW(menu, MF_STRING, ReopenA1, L"Reopen A1"); AppendMenuW(menu, MF_STRING, ReopenA2, L"Reopen A2");
    } else { AppendMenuW(menu, MF_STRING, ReopenB, L"Reopen B"); }
    AppendMenuW(menu, MF_STRING, QuitID, L"Quit fixture");
    HWND hwnd = CreateWindowExW(0, ClassName, record->title.c_str(), WS_OVERLAPPEDWINDOW,
        slot == L"A2" ? 680 : 80, 160, 580, 440, nullptr, menu, GetModuleHandleW(nullptr), record.get());
    if (!hwnd) { DestroyMenu(menu); throw std::runtime_error("window creation failed"); }
    record->nativeID = hwndString(hwnd);
    attachProvider(hwnd, Kind::Window, record->id(L"window"), record->title);
    child(record.get(), L"STATIC", (slot + L" | " + record->instance + L"\r\nSynthetic text only. Click manually to focus.").c_str(),
          0, 0, 16, 12, 540, 48);
    record->edit = child(record.get(), L"EDIT", record->text.c_str(), TextID,
                         WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL, 16, 65, 530, 30);
    SendMessageW(record->edit, EM_SETLIMITTEXT, 32767, 0);
    attachProvider(record->edit, Kind::Text, record->id(L"text"), slot + L" test text");
    HWND button = child(record.get(), L"BUTTON", L"Increment this window", IncrementID, WS_TABSTOP, 16, 108, 210, 32);
    attachProvider(button, Kind::Increment, record->id(L"increment"), slot + L" increment");
    record->counter = child(record.get(), L"STATIC", L"Count: 0", 0, 0, 250, 112, 200, 28);
    const wchar_t *titles[] = {L"Move", L"Resize", L"Minimize", L"Modal", L"Recreate"};
    for (int i = 0; i < 5; ++i) { child(record.get(), L"BUTTON", titles[i], MoveID + i, WS_TABSTOP, 16 + 106 * i, 154, 100, 32); }
    require(CreateWindowExW(WS_EX_CLIENTEDGE, CanvasClass, L"Synthetic mouse drawing area", WS_CHILD | WS_VISIBLE,
        16, 205, 530, 145, hwnd, nullptr, GetModuleHandleW(nullptr), record.get()) != nullptr, "canvas creation failed");
    require(SetTimer(hwnd, 1, 100, nullptr) != 0, "snapshot timer creation failed");
    ShowWindow(hwnd, SW_SHOWNOACTIVATE);
    SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
    fixture->event(L"created", record.get()); fixture->publish();
}
} // namespace

int wmain(int argc, wchar_t **argv) {
    try {
        std::wstring directoryPath, role = L"a";
        bool offline = false, background = false;
        for (int i = 1; i < argc; ++i) {
            const std::wstring arg = argv[i];
            if (arg == L"--help") {
                std::puts("window-fixture --state-dir NEW_ABSOLUTE_DIRECTORY [--role a|b] [--background] [--offline-probe]"); return 0;
            } else if (arg == L"--state-dir" && i + 1 < argc) { directoryPath = argv[++i]; }
            else if (arg == L"--role" && i + 1 < argc) { role = argv[++i]; }
            else if (arg == L"--background") { background = true; }
            else if (arg == L"--offline-probe") { offline = true; }
            else { throw std::runtime_error("invalid arguments; use --help"); }
        }
        require(!directoryPath.empty() && (role == L"a" || role == L"b"), "require --state-dir NEW_ABSOLUTE_DIRECTORY and --role a|b");
        Fixture application;
        fixture = &application;
        application.role = role; application.offline = offline; application.background = background;
        application.directory = std::make_unique<StateDirectory>(directoryPath);
        if (offline) {
            // No CoInitializeEx, HWNDs, UIA server/client, input, or clipboard in this branch.
            auto first = application.newRecord(role == L"a" ? L"A1" : L"B");
            first->nativeID = L"0xfedcba9876543210";
            first->text = L"Synthetic \"quotes\" \\ slash\n\tUnicode: 窗口 🧪";
            first->count = 2;
            application.event(L"created", first.get());
            if (role == L"a") {
                auto second = application.newRecord(L"A2"); second->nativeID = L"0xfedcba9876543200";
                application.event(L"created", second.get());
                first->closed = true; application.event(L"closed", first.get());
                auto replacement = application.newRecord(L"A1"); replacement->nativeID = first->nativeID;
                application.event(L"created", replacement.get());
            }
            for (size_t i = 0; i < EventLimit + 8; ++i) { application.event(L"probe_event", first.get()); }
            application.publish(); application.running = false; application.publish();
            std::puts("offline probe passed (no HWNDs, UIA, input, or clipboard)"); return 0;
        }
        require(SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED)), "STA initialization failed");
        require(SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) != FALSE, "DPI awareness setup failed");
        INITCOMMONCONTROLSEX controls{sizeof(INITCOMMONCONTROLSEX), ICC_STANDARD_CLASSES};
        require(InitCommonControlsEx(&controls) != FALSE, "common controls initialization failed");
        WNDCLASSW windowClass{};
        windowClass.hInstance = GetModuleHandleW(nullptr); windowClass.lpfnWndProc = windowProc;
        windowClass.lpszClassName = ClassName; windowClass.hCursor = LoadCursorW(nullptr, IDC_ARROW);
        require(RegisterClassW(&windowClass) != 0, "window class registration failed");
        windowClass.lpfnWndProc = canvasProc; windowClass.lpszClassName = CanvasClass;
        windowClass.hbrBackground = reinterpret_cast<HBRUSH>(static_cast<INT_PTR>(COLOR_WINDOW + 1));
        require(RegisterClassW(&windowClass) != 0, "canvas class registration failed");
        if (role == L"a") { createWindow(L"A1"); createWindow(L"A2"); } else { createWindow(L"B"); }
        MSG message{};
        BOOL result;
        while ((result = GetMessageW(&message, nullptr, 0, 0)) > 0) {
            if (message.message == WM_KEYDOWN || message.message == WM_KEYUP ||
                message.message == WM_SYSKEYDOWN || message.message == WM_SYSKEYUP) {
                HWND root = GetAncestor(message.hwnd, GA_ROOT);
                for (const auto &record : application.records) {
                    if (record->hwnd != root || record->closed) { continue; }
                    const bool down = message.message == WM_KEYDOWN || message.message == WM_SYSKEYDOWN;
                    const unsigned modifiers = (GetKeyState(VK_SHIFT) < 0 ? 1U : 0U) |
                        (GetKeyState(VK_CONTROL) < 0 ? 2U : 0U) | (GetKeyState(VK_MENU) < 0 ? 4U : 0U);
                    application.event(down ? L"key_down" : L"key_up", record.get(),
                        "{\"key_code\":" + std::to_string(message.wParam) + ",\"modifiers\":" + std::to_string(modifiers) +
                        ",\"repeat\":" + ((down && (message.lParam & (static_cast<LPARAM>(1) << 30))) ? "true}" : "false}"));
                }
            }
            TranslateMessage(&message); DispatchMessageW(&message);
        }
        require(result == 0, "message loop failed");
        application.running = false; application.event(L"process_stopping"); application.publish();
        UiaDisconnectAllProviders(); CoUninitialize(); fixture = nullptr;
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "window-fixture: %s\n", error.what()); return 1;
    }
}
