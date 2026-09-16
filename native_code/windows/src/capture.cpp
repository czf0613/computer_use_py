#include "backend.h"
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <winrt/Windows.Foundation.h>
#include <wincodec.h>
#include <algorithm>
#include <cstring>

namespace scap
{
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
struct Capture::Impl
{
    std::mutex mutex, gpu_mutex, stop_mutex;
    std::condition_variable changed;
    bool ready = false, stopping = false, stopped = false;
    std::exception_ptr error;
    std::shared_ptr<Frame> latest_frame;
    std::thread worker;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11VideoDevice> video_device;
    ComPtr<ID3D11VideoContext> video_context;
    ComPtr<ID3D11VideoProcessorEnumerator> video_enumerator;
    ComPtr<ID3D11VideoProcessor> video_processor;
    ComPtr<ID3D11Texture2D> nv12_target, nv12_staging;
    uint32_t video_width = 0, video_height = 0;
    bool video_unavailable = false;
    Direct3D11CaptureFramePool pool{nullptr};
    GraphicsCaptureSession session{nullptr};
    GraphicsCaptureItem item{nullptr};
    winrt::event_token frame_token{}, closed_token{};
    bool subscribed = false, subscribed_closed = false;

    std::vector<uint8_t> video_pixels(const Frame &frame)
    {
        std::lock_guard<std::mutex> gpu(gpu_mutex);
        if (video_unavailable)
        {
            return {};
        }
        try
        {
            UINT width = (frame.width + 1) & ~1U, height = (frame.height + 1) & ~1U;
            if (video_width != width || video_height != height)
            {
                check(device.As(&video_device));
                check(context.As(&video_context));
                D3D11_VIDEO_PROCESSOR_CONTENT_DESC content{};
                content.InputFrameFormat = D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
                content.InputWidth = frame.width;
                content.InputHeight = frame.height;
                content.OutputWidth = width;
                content.OutputHeight = height;
                content.Usage = D3D11_VIDEO_USAGE_PLAYBACK_NORMAL;
                video_enumerator.Reset();
                check(video_device->CreateVideoProcessorEnumerator(&content, &video_enumerator));
                UINT support;
                check(video_enumerator->CheckVideoProcessorFormat(DXGI_FORMAT_NV12, &support));
                if (!(support & D3D11_VIDEO_PROCESSOR_FORMAT_SUPPORT_OUTPUT))
                {
                    video_unavailable = true;
                    return {};
                }
                video_processor.Reset();
                check(video_device->CreateVideoProcessor(video_enumerator.Get(), 0,
                                                         &video_processor));
                D3D11_TEXTURE2D_DESC desc{};
                desc.Width = width;
                desc.Height = height;
                desc.MipLevels = 1;
                desc.ArraySize = 1;
                desc.Format = DXGI_FORMAT_NV12;
                desc.SampleDesc.Count = 1;
                desc.BindFlags = D3D11_BIND_RENDER_TARGET;
                nv12_target.Reset();
                check(device->CreateTexture2D(&desc, nullptr, &nv12_target));
                desc.BindFlags = 0;
                desc.Usage = D3D11_USAGE_STAGING;
                desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
                nv12_staging.Reset();
                check(device->CreateTexture2D(&desc, nullptr, &nv12_staging));
                video_width = width;
                video_height = height;
            }
            D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC input_desc{};
            input_desc.ViewDimension = D3D11_VPIV_DIMENSION_TEXTURE2D;
            ComPtr<ID3D11VideoProcessorInputView> input;
            check(video_device->CreateVideoProcessorInputView(
                frame.texture.Get(), video_enumerator.Get(), &input_desc, &input));
            D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC output_desc{};
            output_desc.ViewDimension = D3D11_VPOV_DIMENSION_TEXTURE2D;
            ComPtr<ID3D11VideoProcessorOutputView> output;
            check(video_device->CreateVideoProcessorOutputView(
                nv12_target.Get(), video_enumerator.Get(), &output_desc, &output));
            RECT source{0, 0, static_cast<LONG>(frame.width), static_cast<LONG>(frame.height)};
            RECT target{0, 0, static_cast<LONG>(width), static_cast<LONG>(height)};
            video_context->VideoProcessorSetStreamFrameFormat(video_processor.Get(), 0,
                                                              D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE);
            video_context->VideoProcessorSetStreamSourceRect(video_processor.Get(), 0, TRUE,
                                                             &source);
            video_context->VideoProcessorSetStreamDestRect(video_processor.Get(), 0, TRUE, &target);
            video_context->VideoProcessorSetOutputTargetRect(video_processor.Get(), TRUE, &target);
            D3D11_VIDEO_PROCESSOR_COLOR_SPACE rgb{};
            rgb.Nominal_Range = D3D11_VIDEO_PROCESSOR_NOMINAL_RANGE_0_255;
            D3D11_VIDEO_PROCESSOR_COLOR_SPACE yuv{};
            yuv.YCbCr_Matrix = 1;
            yuv.Nominal_Range = D3D11_VIDEO_PROCESSOR_NOMINAL_RANGE_16_235;
            video_context->VideoProcessorSetStreamColorSpace(video_processor.Get(), 0, &rgb);
            video_context->VideoProcessorSetOutputColorSpace(video_processor.Get(), &yuv);
            D3D11_VIDEO_PROCESSOR_STREAM stream{};
            stream.Enable = TRUE;
            stream.pInputSurface = input.Get();
            check(video_context->VideoProcessorBlt(video_processor.Get(), output.Get(), 0, 1,
                                                   &stream));
            context->CopyResource(nv12_staging.Get(), nv12_target.Get());
            D3D11_MAPPED_SUBRESOURCE mapped{};
            check(context->Map(nv12_staging.Get(), 0, D3D11_MAP_READ, 0, &mapped));
            std::vector<uint8_t> bytes;
            try
            {
                bytes.resize(static_cast<size_t>(width) * height * 3 / 2);
                for (UINT row = 0; row < height + height / 2; ++row)
                {
                    std::memcpy(bytes.data() + static_cast<size_t>(row) * width,
                                static_cast<uint8_t *>(mapped.pData) +
                                    static_cast<size_t>(row) * mapped.RowPitch,
                                width);
                }
            }
            catch (...)
            {
                context->Unmap(nv12_staging.Get(), 0);
                throw;
            }
            context->Unmap(nv12_staging.Get(), 0);
            return bytes;
        }
        catch (const winrt::hresult_error &)
        {
            check(device->GetDeviceRemovedReason());
            video_unavailable = true;
            return {};
        }
    }

    void arrive() noexcept
    {
        try
        {
            std::lock_guard<std::mutex> gpu(gpu_mutex);
            {
                std::lock_guard<std::mutex> lock(mutex);
                if (stopping)
                {
                    return;
                }
            }
            auto incoming = pool.TryGetNextFrame();
            if (!incoming)
            {
                return;
            }
            auto size = incoming.ContentSize();
            if (size.Width <= 0 || size.Height <= 0)
            {
                incoming.Close();
                return;
            }
            auto access =
                incoming.Surface()
                    .as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
            ComPtr<ID3D11Texture2D> source;
            check(access->GetInterface(IID_PPV_ARGS(&source)));
            D3D11_TEXTURE2D_DESC desc{};
            source->GetDesc(&desc);
            if (static_cast<UINT>(size.Width) > desc.Width ||
                static_cast<UINT>(size.Height) > desc.Height)
            {
                incoming.Close();
                throw std::runtime_error("Display size changed; restart capture");
            }
            auto value = std::make_shared<Frame>();
            value->width = size.Width;
            value->height = size.Height;
            value->timestamp = incoming.SystemRelativeTime().count();
            desc.Width = value->width;
            desc.Height = value->height;
            desc.BindFlags = 0;
            desc.MiscFlags = 0;
            desc.CPUAccessFlags = 0;
            desc.Usage = D3D11_USAGE_DEFAULT;
            desc.ArraySize = 1;
            desc.MipLevels = 1;
            check(device->CreateTexture2D(&desc, nullptr, &value->texture));
            D3D11_BOX box{0, 0, 0, value->width, value->height, 1};
            context->CopySubresourceRegion(value->texture.Get(), 0, 0, 0, 0, source.Get(), 0, &box);
            incoming.Close();
            {
                std::lock_guard<std::mutex> lock(mutex);
                if (!stopping)
                {
                    latest_frame = std::move(value);
                }
            }
            changed.notify_all();
        }
        catch (...)
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (!stopping)
            {
                error = std::current_exception();
            }
            changed.notify_all();
        }
    }
    void run(uint32_t id, const std::shared_ptr<Impl> &self) noexcept
    {
        try
        {
            Apartment apartment;
            DpiScope dpi;
            if (!GraphicsCaptureSession::IsSupported())
            {
                throw std::runtime_error("Windows Graphics Capture is unavailable in this session");
            }
            auto display = display_by_id(id);
            auto factory =
                winrt::get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
            check(factory->CreateForMonitor(display.monitor, winrt::guid_of<GraphicsCaptureItem>(),
                                            winrt::put_abi(item)));
            D3D_FEATURE_LEVEL level;
            HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
                                           D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
                                           D3D11_SDK_VERSION, &device, &level, &context);
            if (FAILED(hr))
            {
                check(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
                                        D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
                                        D3D11_SDK_VERSION, &device, &level, &context));
            }
            ComPtr<IDXGIDevice> dxgi;
            check(device.As(&dxgi));
            winrt::com_ptr<IInspectable> inspectable;
            check(CreateDirect3D11DeviceFromDXGIDevice(dxgi.Get(), inspectable.put()));
            auto direct =
                inspectable.as<winrt::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice>();
            pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
                direct, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, item.Size());
            session = pool.CreateCaptureSession(item);
            std::weak_ptr<Impl> weak = self;
            frame_token = pool.FrameArrived(
                [weak](const auto &, const auto &)
                {
                    if (auto state = weak.lock())
                    {
                        state->arrive();
                    }
                });
            subscribed = true;
            closed_token = item.Closed(
                [weak](const auto &, const auto &)
                {
                    if (auto state = weak.lock())
                    {
                        std::lock_guard<std::mutex> lock(state->mutex);
                        if (!state->stopping)
                        {
                            state->error = std::make_exception_ptr(
                                std::runtime_error("Capture display closed"));
                        }
                        state->changed.notify_all();
                    }
                });
            subscribed_closed = true;
            session.StartCapture();
            {
                std::lock_guard<std::mutex> lock(mutex);
                ready = true;
            }
            changed.notify_all();
            {
                std::unique_lock<std::mutex> lock(mutex);
                changed.wait(lock, [&] { return stopping; });
            }
            // Revoke before closing; callbacks retain Impl and synchronize GPU
            // access, so no late event can access released native state.
            if (subscribed)
            {
                pool.FrameArrived(frame_token);
                subscribed = false;
            }
            if (subscribed_closed)
            {
                item.Closed(closed_token);
                subscribed_closed = false;
            }
            {
                std::lock_guard<std::mutex> gpu(gpu_mutex);
                session.Close();
                pool.Close();
                session = nullptr;
                pool = nullptr;
                item = nullptr;
            }
        }
        catch (...)
        {
            {
                std::lock_guard<std::mutex> lock(mutex);
                error = std::current_exception();
                ready = true;
                stopping = true;
            }
            changed.notify_all();
            try
            {
                if (subscribed && pool)
                {
                    pool.FrameArrived(frame_token);
                }
            }
            catch (...)
            {
            }
            try
            {
                if (subscribed_closed && item)
                {
                    item.Closed(closed_token);
                }
            }
            catch (...)
            {
            }
            std::lock_guard<std::mutex> gpu(gpu_mutex);
            try
            {
                if (session)
                {
                    session.Close();
                }
            }
            catch (...)
            {
            }
            try
            {
                if (pool)
                {
                    pool.Close();
                }
            }
            catch (...)
            {
            }
            session = nullptr;
            pool = nullptr;
            item = nullptr;
        }
        {
            std::lock_guard<std::mutex> lock(mutex);
            stopped = true;
        }
        changed.notify_all();
    }
};
Capture::Capture(uint32_t id) : impl(std::make_shared<Impl>())
{
    auto state = impl;
    state->worker = std::thread([state, id] { state->run(id, state); });
    try
    {
        std::unique_lock<std::mutex> lock(state->mutex);
        if (!state->changed.wait_for(lock, std::chrono::seconds(10), [&] { return state->ready; }))
        {
            throw Timeout("Timed out starting Windows capture");
        }
        if (state->error)
        {
            std::rethrow_exception(state->error);
        }
    }
    catch (...)
    {
        stop();
        throw;
    }
}
Capture::Capture(Pixels pixels) : impl(std::make_shared<Impl>())
{
    auto f = std::make_shared<Frame>();
    f->width = pixels.width;
    f->height = pixels.height;
    f->synthetic = std::move(pixels.data);
    f->timestamp = clock100();
    impl->latest_frame = std::move(f);
    impl->ready = true;
}
Capture::~Capture()
{
    try
    {
        stop();
    }
    catch (...)
    {
    }
}
void Capture::stop()
{
    std::lock_guard<std::mutex> stopping(impl->stop_mutex);
    {
        std::lock_guard<std::mutex> lock(impl->mutex);
        impl->stopping = true;
        impl->latest_frame.reset();
    }
    impl->changed.notify_all();
    if (impl->worker.joinable())
    {
        impl->worker.join();
    }
}
std::shared_ptr<Frame> Capture::latest()
{
    std::lock_guard<std::mutex> lock(impl->mutex);
    if (impl->stopping)
    {
        return nullptr;
    }
    if (impl->error)
    {
        std::rethrow_exception(impl->error);
    }
    return impl->latest_frame;
}
std::shared_ptr<Frame> Capture::first()
{
    std::unique_lock<std::mutex> lock(impl->mutex);
    if (!impl->changed.wait_for(lock, std::chrono::seconds(5), [&]
                                { return impl->latest_frame || impl->error || impl->stopping; }))
    {
        throw Timeout("No complete capture frame arrived within five seconds");
    }
    if (impl->error)
    {
        std::rethrow_exception(impl->error);
    }
    if (!impl->latest_frame)
    {
        throw std::runtime_error("Capture stopped before first frame");
    }
    return impl->latest_frame;
}
Pixels Capture::pixels(const std::shared_ptr<Frame> &f)
{
    Pixels p;
    p.width = f->width;
    p.height = f->height;
    p.stride = f->width * 4;
    if (!f->synthetic.empty())
    {
        p.data = f->synthetic;
        return p;
    }
    std::lock_guard<std::mutex> gpu(impl->gpu_mutex);
    D3D11_TEXTURE2D_DESC desc{};
    f->texture->GetDesc(&desc);
    desc.Usage = D3D11_USAGE_STAGING;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.BindFlags = 0;
    desc.MiscFlags = 0;
    ComPtr<ID3D11Texture2D> staging;
    check(impl->device->CreateTexture2D(&desc, nullptr, &staging));
    impl->context->CopyResource(staging.Get(), f->texture.Get());
    D3D11_MAPPED_SUBRESOURCE mapped{};
    check(impl->context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped));
    try
    {
        p.stride = mapped.RowPitch;
        p.data.resize(static_cast<size_t>(p.stride) * p.height);
        std::memcpy(p.data.data(), mapped.pData, p.data.size());
    }
    catch (...)
    {
        impl->context->Unmap(staging.Get(), 0);
        throw;
    }
    impl->context->Unmap(staging.Get(), 0);
    return p;
}
std::vector<uint8_t> Capture::nv12(const std::shared_ptr<Frame> &f)
{
    // Preserve exact pixels at odd edges using the replication fallback.
    if (f->texture && !(f->width & 1) && !(f->height & 1))
    {
        auto gpu = impl->video_pixels(*f);
        if (!gpu.empty())
        {
            return gpu;
        }
    }
    // Portable fallback accepts both hardware and software MF transforms.
    // BT.709 limited-range NV12, padding odd right/bottom edges by replication.
    auto p = pixels(f);
    uint32_t w = (p.width + 1) & ~1U, h = (p.height + 1) & ~1U;
    std::vector<uint8_t> result(static_cast<size_t>(w) * h * 3 / 2);
    auto clip = [](int n) { return static_cast<uint8_t>(std::clamp(n, 0, 255)); };
    for (uint32_t y = 0; y < h; y += 2)
    {
        for (uint32_t x = 0; x < w; x += 2)
        {
            int sr = 0, sg = 0, sb = 0;
            for (uint32_t dy = 0; dy < 2; ++dy)
            {
                for (uint32_t dx = 0; dx < 2; ++dx)
                {
                    const uint8_t *b =
                        p.data.data() +
                        static_cast<size_t>(std::min(y + dy, p.height - 1)) * p.stride +
                        std::min(x + dx, p.width - 1) * 4;
                    sr += b[2];
                    sg += b[1];
                    sb += b[0];
                    result[static_cast<size_t>(y + dy) * w + x + dx] =
                        clip(((47 * b[2] + 157 * b[1] + 16 * b[0] + 128) >> 8) + 16);
                }
            }
            size_t uv = static_cast<size_t>(w) * h + static_cast<size_t>(y / 2) * w + x;
            result[uv] = clip(((-26 * sr - 87 * sg + 112 * sb + 512) >> 10) + 128);
            result[uv + 1] = clip(((112 * sr - 102 * sg - 10 * sb + 512) >> 10) + 128);
        }
    }
    return result;
}
namespace
{
constexpr const char *capture_name = "scapkit.windows.capture";
void destroy_capture(PyObject *capsule) noexcept
{
    auto *c = static_cast<Capture *>(PyCapsule_GetPointer(capsule, capture_name));
    if (!c)
    {
        PyErr_Clear();
        return;
    }
    NoPython detached;
    delete c;
}
Capture *parse_capture(PyObject *obj)
{
    return static_cast<Capture *>(PyCapsule_GetPointer(obj, capture_name));
}
PyObject *wrap_capture(std::unique_ptr<Capture> capture)
{
    PyObject *capsule = PyCapsule_New(capture.get(), capture_name, destroy_capture);
    if (capsule)
    {
        capture.release();
    }
    else
    {
        NoPython detached;
        capture.reset();
    }
    return capsule;
}
std::vector<uint8_t> jpeg(Pixels pixels, int quality)
{
    Apartment apartment;
    ComPtr<IWICImagingFactory> factory;
    check(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                           IID_PPV_ARGS(&factory)));
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
    VARIANT value{};
    value.vt = VT_R4;
    value.fltVal = quality / 100.0f;
    check(options->Write(1, &property, &value));
    check(frame->Initialize(options.Get()));
    check(frame->SetSize(pixels.width, pixels.height));
    WICPixelFormatGUID format = GUID_WICPixelFormat24bppBGR;
    check(frame->SetPixelFormat(&format));
    ComPtr<IWICBitmap> bitmap;
    check(factory->CreateBitmapFromMemory(pixels.width, pixels.height, GUID_WICPixelFormat32bppBGRA,
                                          pixels.stride, static_cast<UINT>(pixels.data.size()),
                                          pixels.data.data(), &bitmap));
    ComPtr<IWICFormatConverter> converter;
    check(factory->CreateFormatConverter(&converter));
    check(converter->Initialize(bitmap.Get(), format, WICBitmapDitherTypeNone, nullptr, 0,
                                WICBitmapPaletteTypeCustom));
    check(frame->WriteSource(converter.Get(), nullptr));
    check(frame->Commit());
    check(encoder->Commit());
    STATSTG stat{};
    check(stream->Stat(&stat, STATFLAG_NONAME));
    if (stat.cbSize.QuadPart > SIZE_MAX)
    {
        throw std::bad_alloc();
    }
    HGLOBAL global;
    check(GetHGlobalFromStream(stream.Get(), &global));
    auto *p = static_cast<uint8_t *>(GlobalLock(global));
    if (!p)
    {
        winfail();
    }
    std::vector<uint8_t> result;
    try
    {
        result.assign(p, p + static_cast<size_t>(stat.cbSize.QuadPart));
    }
    catch (...)
    {
        GlobalUnlock(global);
        throw;
    }
    GlobalUnlock(global);
    return result;
}
} // namespace
PyObject *py_start_capture(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *obj;
            if (!PyArg_ParseTuple(args, "O", &obj))
            {
                return nullptr;
            }
            uint32_t id;
            if (!positive_u32(obj, id, "display_id"))
            {
                return nullptr;
            }
            std::unique_ptr<Capture> capture;
            {
                NoPython detached;
                capture = std::make_unique<Capture>(id);
            }
            return wrap_capture(std::move(capture));
        });
}
PyObject *py_stop_capture(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *obj;
            if (!PyArg_ParseTuple(args, "O", &obj))
            {
                return nullptr;
            }
            auto *c = parse_capture(obj);
            if (!c)
            {
                return nullptr;
            }
            {
                NoPython detached;
                c->stop();
            }
            Py_RETURN_NONE;
        });
}
PyObject *py_current_frame_bgra(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *obj;
            if (!PyArg_ParseTuple(args, "O", &obj))
            {
                return nullptr;
            }
            auto *c = parse_capture(obj);
            if (!c)
            {
                return nullptr;
            }
            Pixels p;
            {
                NoPython detached;
                auto f = c->latest();
                if (f)
                {
                    p = c->pixels(f);
                }
            }
            if (p.data.empty())
            {
                Py_RETURN_NONE;
            }
            return Py_BuildValue("{s:y#,s:I,s:I,s:I}", "data", p.data.data(),
                                 static_cast<Py_ssize_t>(p.data.size()), "width", p.width, "height",
                                 p.height, "bytes_per_row", p.stride);
        });
}
PyObject *py_current_frame_jpg(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *obj;
            int quality = 80;
            if (!PyArg_ParseTuple(args, "O|i", &obj, &quality))
            {
                return nullptr;
            }
            if (quality < 0 || quality > 100)
            {
                throw std::invalid_argument("quality must be between 0 and 100");
            }
            auto *c = parse_capture(obj);
            if (!c)
            {
                return nullptr;
            }
            std::vector<uint8_t> bytes;
            {
                NoPython detached;
                auto f = c->latest();
                if (f)
                {
                    bytes = jpeg(c->pixels(f), quality);
                }
            }
            if (bytes.empty())
            {
                Py_RETURN_NONE;
            }
            return PyBytes_FromStringAndSize(reinterpret_cast<const char *>(bytes.data()),
                                             static_cast<Py_ssize_t>(bytes.size()));
        });
}
#ifdef SCAPKIT_TESTING
PyObject *py_test_capture(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            int w, h;
            Py_buffer data{};
            if (!PyArg_ParseTuple(args, "iiy*", &w, &h, &data))
            {
                return nullptr;
            }
            Pixels p;
            try
            {
                if (w <= 0 || h <= 0 || w > 16384 || h > 16384 ||
                    static_cast<int64_t>(w) * h * 4 != data.len)
                {
                    throw std::invalid_argument("Invalid synthetic BGRA dimensions");
                }
                p.width = w;
                p.height = h;
                p.stride = w * 4;
                p.data.assign(static_cast<uint8_t *>(data.buf),
                              static_cast<uint8_t *>(data.buf) + data.len);
            }
            catch (...)
            {
                PyBuffer_Release(&data);
                throw;
            }
            PyBuffer_Release(&data);
            std::unique_ptr<Capture> c;
            {
                NoPython detached;
                c = std::make_unique<Capture>(std::move(p));
            }
            return wrap_capture(std::move(c));
        });
}
#endif
} // namespace scap
