#include "window_backend.h"
#include <dwmapi.h>
#include <bcrypt.h>
#include <winternl.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <winrt/Windows.Foundation.h>
#include <wincodec.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <sstream>

namespace scap::window
{
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
namespace
{
struct Monitor
{
    struct Watch { HWND hwnd; DWORD pid; std::weak_ptr<Life> life; };
    std::mutex mutex;
    std::vector<Watch> watches;
    std::atomic<int64_t> heartbeat{0};
    std::atomic<bool> ready{false}, failed{false};
    static Monitor *instance;
    static void CALLBACK event(HWINEVENTHOOK, DWORD event, HWND hwnd, LONG object,
                               LONG, DWORD, DWORD) noexcept
    {
        auto &self = *instance;
        std::lock_guard lock(self.mutex);
        DWORD pid = 0;
        GetWindowThreadProcessId(hwnd, &pid);
        for (auto &watch : self.watches)
        {
            if (auto life = watch.life.lock())
            {
                if (watch.hwnd == hwnd && event == EVENT_OBJECT_DESTROY && object == OBJID_WINDOW)
                {
                    life->invalidate();
                }
                else if (watch.pid == pid || watch.hwnd == hwnd)
                {
                    life->changed(event == EVENT_OBJECT_LOCATIONCHANGE && watch.hwnd == hwnd);
                }
            }
        }
    }
    Monitor()
    {
        instance = this;
        std::thread([this]
        {
            // The hook thread must keep pumping even if a UIA provider stalls the MTA actor.
            auto hook = SetWinEventHook(EVENT_OBJECT_CREATE, EVENT_OBJECT_LOCATIONCHANGE,
                                       nullptr, event, 0, 0, WINEVENT_OUTOFCONTEXT);
            if (!hook) { failed = true; ready = true; return; }
            ready = true;
            for (;;)
            {
                MSG message{};
                while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE))
                {
                    if (message.message == WM_QUIT)
                    {
                        failed = true;
                        UnhookWinEvent(hook);
                        std::lock_guard lock(mutex);
                        for (auto &watch : watches)
                        {
                            if (auto life = watch.life.lock()) { life->invalidate(); }
                        }
                        return;
                    }
                    TranslateMessage(&message);
                    DispatchMessageW(&message);
                }
                heartbeat = clock100();
                MsgWaitForMultipleObjectsEx(0, nullptr, 20, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
            }
        }).detach();
    }
    void watch(const std::shared_ptr<Target> &target)
    {
        auto deadline = Clock::now() + std::chrono::seconds(2);
        while (!ready && Clock::now() < deadline) { Sleep(1); }
        if (!ready || failed) { fail("identity_unverifiable"); }
        std::lock_guard lock(mutex);
        std::erase_if(watches, [](const Watch &w) { return w.life.expired(); });
        if (watches.size() >= 512) { fail("resource_limit"); }
        watches.push_back({target->hwnd, target->pid, target->life});
    }
    bool alive() const { return ready && !failed && clock100() - heartbeat.load() < 20000000; }
};
Monitor *Monitor::instance = nullptr;
Monitor &monitor()
{
    // Process-lifetime, one bounded monitor. It owns no Python or COM objects.
    static auto *value = new Monitor();
    return *value;
}
std::string start_mark(HANDLE process)
{
    FILETIME created{}, exited{}, kernel{}, user{};
    if (!GetProcessTimes(process, &created, &exited, &kernel, &user))
    {
        fail("identity_unverifiable");
    }
    return std::to_string((static_cast<uint64_t>(created.dwHighDateTime) << 32) |
                          created.dwLowDateTime);
}
struct WindowCapture final : CaptureTarget
{
    GraphicsCaptureItem item{nullptr};
    winrt::event_token closed{};
    std::shared_ptr<Life> life;
    explicit WindowCapture(HWND hwnd, std::shared_ptr<Life> life_) : life(std::move(life_))
    {
        if (!GraphicsCaptureSession::IsSupported()) { fail("capture_unavailable"); }
        auto factory = winrt::get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        check(factory->CreateForWindow(hwnd, winrt::guid_of<GraphicsCaptureItem>(), winrt::put_abi(item)));
        std::weak_ptr<Life> weak = life;
        closed = item.Closed([weak](const auto &, const auto &)
        {
            if (auto state = weak.lock()) { state->invalidate(); }
        });
    }
    ~WindowCapture() override
    {
        try { item.Closed(closed); } catch (...) { life->cleanup_failed = true; life->invalidate(); }
    }
    Pixels capture(const Geometry &, Clock::time_point deadline, double &captured_at) override
    {
        if (Clock::now() >= deadline) { fail("capture_timeout"); }
        auto size = item.Size();
        if (size.Width <= 0 || size.Height <= 0 || size.Width > 16384 || size.Height > 16384 ||
            static_cast<uint64_t>(size.Width) * size.Height * 4 > max_bytes)
        {
            fail("resource_limit");
        }
        ComPtr<ID3D11Device> device;
        ComPtr<ID3D11DeviceContext> context;
        D3D_FEATURE_LEVEL level;
        HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION, &device, &level, &context);
        if (FAILED(hr))
        {
            check(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION, &device, &level, &context));
        }
        ComPtr<IDXGIDevice> dxgi;
        check(device.As(&dxgi));
        winrt::com_ptr<IInspectable> inspectable;
        check(CreateDirect3D11DeviceFromDXGIDevice(dxgi.Get(), inspectable.put()));
        auto direct = inspectable.as<winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();
        if (Clock::now() >= deadline) { fail("capture_timeout"); }
        auto pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
            direct, DirectXPixelFormat::B8G8R8A8UIntNormalized, 1, size);
        GraphicsCaptureSession session{nullptr};
        Direct3D11CaptureFrame incoming{nullptr};
        // No frame callback, cached frame, audio, display crop, or cursor. Poll this fresh pool only.
        auto cleanup = [&]
        {
            std::exception_ptr error;
            try { if (incoming) { incoming.Close(); } } catch (...) { error = std::current_exception(); }
            try { if (session) { session.Close(); } } catch (...) { error = std::current_exception(); }
            try { pool.Close(); } catch (...) { error = std::current_exception(); }
            incoming = nullptr; session = nullptr; pool = nullptr;
            if (error) { std::rethrow_exception(error); }
        };
        try
        {
            if (Clock::now() >= deadline) { fail("capture_timeout"); }
            session = pool.CreateCaptureSession(item);
            session.IsCursorCaptureEnabled(false);
            if (Clock::now() >= deadline) { fail("capture_timeout"); }
            session.StartCapture();
            while (!incoming)
            {
                if (life->stale) { fail("stale_window"); }
                if (Clock::now() >= deadline) { fail("capture_timeout"); }
                check(device->GetDeviceRemovedReason());
                incoming = pool.TryGetNextFrame();
                if (!incoming) { Sleep(2); }
            }
            auto content = incoming.ContentSize();
            if (content.Width != size.Width || content.Height != size.Height)
            {
                fail("geometry_changed");
            }
            auto access = incoming.Surface().as<Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
            ComPtr<ID3D11Texture2D> texture;
            check(access->GetInterface(IID_PPV_ARGS(&texture)));
            D3D11_TEXTURE2D_DESC desc{};
            texture->GetDesc(&desc);
            if (desc.Width < static_cast<UINT>(size.Width) || desc.Height < static_cast<UINT>(size.Height) ||
                desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM)
            {
                fail("geometry_changed");
            }
            desc.Width = static_cast<UINT>(size.Width); desc.Height = static_cast<UINT>(size.Height);
            desc.Usage = D3D11_USAGE_STAGING; desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            desc.BindFlags = 0; desc.MiscFlags = 0; desc.MipLevels = 1; desc.ArraySize = 1;
            ComPtr<ID3D11Texture2D> staging;
            check(device->CreateTexture2D(&desc, nullptr, &staging));
            D3D11_BOX box{0, 0, 0, desc.Width, desc.Height, 1};
            context->CopySubresourceRegion(staging.Get(), 0, 0, 0, 0, texture.Get(), 0, &box);
            D3D11_MAPPED_SUBRESOURCE mapped{};
            check(context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped));
            Pixels pixels;
            try
            {
                pixels.width = desc.Width; pixels.height = desc.Height; pixels.stride = desc.Width * 4;
                if (mapped.RowPitch < pixels.stride) { fail("capture_unavailable"); }
                pixels.data.resize(static_cast<size_t>(pixels.stride) * pixels.height);
                for (size_t y = 0; y < pixels.height; ++y)
                {
                    std::memcpy(pixels.data.data() + y * pixels.stride,
                                static_cast<uint8_t *>(mapped.pData) + y * mapped.RowPitch, pixels.stride);
                }
            }
            catch (...) { context->Unmap(staging.Get(), 0); throw; }
            context->Unmap(staging.Get(), 0);
            captured_at = static_cast<double>(clock100()) / 10000000.0;
            cleanup();
            if (Clock::now() >= deadline) { fail("capture_timeout"); }
            return pixels;
        }
        catch (...)
        {
            auto error = std::current_exception();
            if (pool) { cleanup(); }
            std::rethrow_exception(error);
        }
    }
};
} // namespace
bool Rect::valid() const
{
    return std::isfinite(x) && std::isfinite(y) && std::isfinite(width) && std::isfinite(height) &&
           std::abs(x) <= 1e12 && std::abs(y) <= 1e12 && width > 0 && width <= 1e12 && height > 0 && height <= 1e12;
}
std::string utf8(const std::wstring &text, size_t limit)
{
    if (text.empty()) { return {}; }
    if (text.size() > limit || text.find(L'\0') != std::wstring::npos) { fail("resource_limit"); }
    int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()),
                                    nullptr, 0, nullptr, nullptr);
    if (count <= 0 || static_cast<size_t>(count) > limit) { fail("resource_limit"); }
    std::string out(count, '\0');
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()),
                            out.data(), count, nullptr, nullptr)) { fail("identity_unverifiable"); }
    return out;
}
std::wstring wide(const std::string &text)
{
    if (text.empty()) { return {}; }
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (count <= 0) { fail("identity_unverifiable"); }
    std::wstring out(count, L'\0');
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), out.data(), count))
    { fail("identity_unverifiable"); }
    return out;
}
Geometry read_geometry(HWND hwnd)
{
    DpiScope scope;
    if (!scope.old) { fail("mapping_unavailable"); }
    Geometry result;
    RECT bounds{};
    // DWM bounds exclude the invisible resize border; don't guess from GetWindowRect.
    if (FAILED(DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, &bounds, sizeof(bounds))))
    { fail("capture_unavailable"); }
    result.bounds = {double(bounds.left), double(bounds.top), double(bounds.right) - bounds.left,
                     double(bounds.bottom) - bounds.top};
    if (!result.bounds.valid()) { fail("capture_unavailable"); }
    RECT client{};
    POINT origin{};
    if (GetClientRect(hwnd, &client) && ClientToScreen(hwnd, &origin))
    {
        Rect local{double(origin.x) - bounds.left, double(origin.y) - bounds.top,
                   double(client.right) - client.left, double(client.bottom) - client.top};
        if (local.valid() && local.x >= 0 && local.y >= 0 &&
            local.x + local.width <= result.bounds.width && local.y + local.height <= result.bounds.height)
        { result.client = local; }
    }
    result.dpi = GetDpiForWindow(hwnd);
    if (!result.dpi) { fail("identity_unverifiable"); }
    result.minimized = IsIconic(hwnd) != FALSE;
    result.visible = IsWindowVisible(hwnd) != FALSE;
    return result;
}
void Target::validate()
{
    if (life->stale) { fail("stale_window"); }
    if (!synthetic)
    {
        DWORD current = 0;
        GetWindowThreadProcessId(hwnd, &current);
        if (!monitor().alive() || !IsWindow(hwnd) || current != pid ||
            WaitForSingleObject(process->value, 0) != WAIT_TIMEOUT || start_mark(process->value) != process_instance)
        {
            life->invalidate(); fail("stale_window");
        }
    }
    if (semantic) { semantic->validate(); }
    if (life->stale) { fail("stale_window"); }
}
Geometry Target::refresh()
{
    validate();
    auto current = synthetic ? geometry : read_geometry(hwnd);
    auto layout = life->layout.load();
    if (current != geometry || layout != last_layout)
    {
        ++revision; geometry = current; last_layout = layout;
        life->changed();
    }
    return geometry;
}
std::vector<std::shared_ptr<Target>> enumerate(bool accessible, DWORD pid, size_t limit)
{
    struct Enum { DWORD pid; size_t limit; std::vector<HWND> handles; bool overflow = false; } state{pid, limit};
    SetLastError(0);
    BOOL ok = EnumWindows([](HWND hwnd, LPARAM param) -> BOOL
    {
        auto &s = *reinterpret_cast<Enum *>(param);
        DWORD owner = 0;
        GetWindowThreadProcessId(hwnd, &owner);
        if ((!s.pid || owner == s.pid) && IsWindowVisible(hwnd) && GetAncestor(hwnd, GA_ROOT) == hwnd)
        {
            if (s.handles.size() == s.limit) { s.overflow = true; return FALSE; }
            // reserve outside the callback; no exception can cross the Win32 ABI.
            s.handles.push_back(hwnd);
        }
        return TRUE;
    }, [&state] { state.handles.reserve(state.limit); return reinterpret_cast<LPARAM>(&state); }());
    if (state.overflow) { fail("resource_limit"); }
    if (!ok) { throw Failure{"identity_unverifiable", "win32", GetLastError()}; }
    std::vector<std::shared_ptr<Target>> result;
    static uint64_t generation = 1; // Accessed exclusively by the one MTA actor.
    for (auto hwnd : state.handles)
    {
        auto target = std::make_shared<Target>();
        target->hwnd = hwnd; target->generation = generation++;
        GetWindowThreadProcessId(hwnd, &target->pid);
        target->process = std::make_shared<Handle>(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                                                            FALSE, target->pid));
        if (!target->process->value) { fail("identity_unverifiable"); }
        target->process_instance = start_mark(target->process->value);
        monitor().watch(target); // Arm before retaining WGC/UIA objects, never rebind later.
        target->geometry = read_geometry(hwnd);
        wchar_t title[4097]{};
        int count = GetWindowTextW(hwnd, title, 4097);
        if (count >= 4096) { fail("resource_limit"); }
        target->title = utf8(std::wstring(title, count));
        if (accessible) { target->semantic = make_semantic(target); }
        else { target->capture = std::make_unique<WindowCapture>(hwnd, target->life); }
        target->validate();
        result.push_back(std::move(target));
    }
    return result;
}
std::string executable_hash(HANDLE process, std::wstring &path)
{
    path.resize(32768);
    DWORD size = static_cast<DWORD>(path.size());
    if (!QueryFullProcessImageNameW(process, 0, path.data(), &size)) { fail("identity_unverifiable"); }
    path.resize(size);
    Handle file(CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr));
    if (file.value == INVALID_HANDLE_VALUE) { fail("identity_unverifiable"); }
    LARGE_INTEGER length{};
    if (!GetFileSizeEx(file.value, &length) || length.QuadPart <= 0 || length.QuadPart > 512 * 1024 * 1024)
    { fail("identity_unverifiable"); }
    struct Hash
    {
        BCRYPT_ALG_HANDLE algorithm = nullptr;
        BCRYPT_HASH_HANDLE hash = nullptr;
        ~Hash() { if (hash) { BCryptDestroyHash(hash); } if (algorithm) { BCryptCloseAlgorithmProvider(algorithm, 0); } }
    } state;
    auto checked = [](NTSTATUS status) { if (status < 0) { fail("identity_unverifiable"); } };
    checked(BCryptOpenAlgorithmProvider(&state.algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0));
    checked(BCryptCreateHash(state.algorithm, &state.hash, nullptr, 0, nullptr, 0, 0));
    std::array<uint8_t, 65536> buffer{};
    for (;;)
    {
        DWORD read = 0;
        if (!ReadFile(file.value, buffer.data(), static_cast<DWORD>(buffer.size()), &read, nullptr))
        { fail("identity_unverifiable"); }
        if (!read) { break; }
        checked(BCryptHashData(state.hash, buffer.data(), read, 0));
    }
    std::array<uint8_t, 32> digest{};
    checked(BCryptFinishHash(state.hash, digest.data(), static_cast<ULONG>(digest.size()), 0));
    constexpr char hex[] = "0123456789abcdef";
    std::string out;
    for (auto byte : digest) { out += hex[byte >> 4]; out += hex[byte & 15]; }
    return out;
}
bool fixture_image(const std::wstring &path)
{
    HMODULE image = LoadLibraryExW(path.c_str(), nullptr, LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE);
    if (!image) { return false; }
    bool matches = false;
    HRSRC resource = FindResourceW(image, L"SCAPKIT_WINDOW_FIXTURE_SOURCE", RT_RCDATA);
    if (resource)
    {
        auto size = SizeofResource(image, resource);
        auto bytes = LockResource(LoadResource(image, resource));
        matches = bytes && size == 64 && std::memcmp(bytes, SCAPKIT_WINDOW_FIXTURE_SHA256, 64) == 0;
    }
    FreeLibrary(image);
    return matches;
}
std::string environment_id()
{
    using Version = LONG (WINAPI *)(PRTL_OSVERSIONINFOW);
    auto version = reinterpret_cast<Version>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlGetVersion"));
    RTL_OSVERSIONINFOW info{};
    info.dwOSVersionInfoSize = sizeof(info);
    if (!version || version(&info) < 0) { fail("identity_unverifiable"); }
    SYSTEM_INFO system{};
    GetNativeSystemInfo(&system);
    return "windows:" + std::to_string(info.dwMajorVersion) + "." + std::to_string(info.dwMinorVersion) +
           "." + std::to_string(info.dwBuildNumber) + ":arch=" + std::to_string(system.wProcessorArchitecture);
}
std::vector<uint8_t> encode_window_jpeg(const Pixels &pixels, int quality)
{
    ComPtr<IWICImagingFactory> factory;
    check(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory)));
    ComPtr<IStream> stream;
    check(CreateStreamOnHGlobal(nullptr, TRUE, &stream));
    ComPtr<IWICBitmapEncoder> encoder;
    check(factory->CreateEncoder(GUID_ContainerFormatJpeg, nullptr, &encoder));
    check(encoder->Initialize(stream.Get(), WICBitmapEncoderNoCache));
    ComPtr<IWICBitmapFrameEncode> frame;
    ComPtr<IPropertyBag2> options;
    check(encoder->CreateNewFrame(&frame, &options));
    PROPBAG2 property{};
    property.pstrName = const_cast<LPOLESTR>(L"ImageQuality");
    VARIANT value{}; value.vt = VT_R4; value.fltVal = quality / 100.0f;
    check(options->Write(1, &property, &value));
    check(frame->Initialize(options.Get()));
    check(frame->SetSize(pixels.width, pixels.height));
    WICPixelFormatGUID format = GUID_WICPixelFormat24bppBGR;
    check(frame->SetPixelFormat(&format));
    ComPtr<IWICBitmap> bitmap;
    check(factory->CreateBitmapFromMemory(pixels.width, pixels.height, GUID_WICPixelFormat32bppBGRA,
        pixels.stride, static_cast<UINT>(pixels.data.size()), const_cast<BYTE *>(pixels.data.data()), &bitmap));
    ComPtr<IWICFormatConverter> converter;
    check(factory->CreateFormatConverter(&converter));
    check(converter->Initialize(bitmap.Get(), format, WICBitmapDitherTypeNone, nullptr, 0, WICBitmapPaletteTypeCustom));
    check(frame->WriteSource(converter.Get(), nullptr));
    check(frame->Commit()); check(encoder->Commit());
    STATSTG stat{};
    check(stream->Stat(&stat, STATFLAG_NONAME));
    if (!stat.cbSize.QuadPart || stat.cbSize.QuadPart > max_bytes) { fail("resource_limit"); }
    LARGE_INTEGER zero{};
    check(stream->Seek(zero, STREAM_SEEK_SET, nullptr));
    std::vector<uint8_t> result(static_cast<size_t>(stat.cbSize.QuadPart));
    ULONG read = 0;
    check(stream->Read(result.data(), static_cast<ULONG>(result.size()), &read));
    if (read != result.size()) { fail("capture_unavailable"); }
    return result;
}
} // namespace scap::window
