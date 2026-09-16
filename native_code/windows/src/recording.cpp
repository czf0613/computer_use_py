#include "backend.h"
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <propvarutil.h>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <deque>

namespace scap
{
namespace
{
// WASAPI packets may arrive after their presentation time. Submit audio a
// little behind video and let the final packets arrive before closing capture.
constexpr int64_t audio_grace = 1000000; // 100 ms in QPC's 100 ns units.
void recording_check(HRESULT hr, int line)
{
    if (FAILED(hr))
    {
        throw winrt::hresult_error(
            hr, winrt::to_hstring("Media Foundation operation at recording.cpp:" +
                                  std::to_string(line)));
    }
}
#define check(value) recording_check((value), __LINE__)
struct Media
{
    Media()
    {
        // Delay-loaded media DLLs must be checked before the first MF call so
        // stripped Windows editions fail normally, without a delay-load crash.
        for (auto name : {L"mfplat.dll", L"mfreadwrite.dll"})
        {
            HMODULE dll = LoadLibraryExW(name, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
            if (!dll)
            {
                throw std::runtime_error("Windows Media Foundation is unavailable; install the "
                                         "Windows Media Feature Pack");
            }
            FreeLibrary(dll);
        }
        check(MFStartup(MF_VERSION));
    }
    ~Media()
    {
        MFShutdown();
    }
};
ComPtr<IMFMediaType> media_type(const GUID &major, const GUID &subtype)
{
    ComPtr<IMFMediaType> t;
    check(MFCreateMediaType(&t));
    check(t->SetGUID(MF_MT_MAJOR_TYPE, major));
    check(t->SetGUID(MF_MT_SUBTYPE, subtype));
    return t;
}
ComPtr<IMFSample> sample(const void *data, size_t size, int64_t pts, int64_t duration)
{
    if (size > MAXDWORD)
    {
        throw std::invalid_argument("Media sample too large");
    }
    ComPtr<IMFMediaBuffer> buffer;
    check(MFCreateMemoryBuffer(static_cast<DWORD>(size), &buffer));
    BYTE *dest;
    check(buffer->Lock(&dest, nullptr, nullptr));
    std::memcpy(dest, data, size);
    check(buffer->Unlock());
    check(buffer->SetCurrentLength(static_cast<DWORD>(size)));
    ComPtr<IMFSample> s;
    check(MFCreateSample(&s));
    check(s->AddBuffer(buffer.Get()));
    check(s->SetSampleTime(pts));
    check(s->SetSampleDuration(duration));
    return s;
}
struct AudioPacket
{
    int64_t timestamp;
    std::vector<int16_t> data;
};
class Audio
{
    std::mutex mutex;
    std::condition_variable cv;
    std::thread worker;
    bool stopping = false, ready = false;
    std::exception_ptr error;
    std::deque<AudioPacket> queue;
    size_t queued = 0;
    void run() noexcept
    {
        try
        {
            Apartment apartment;
            ComPtr<IMMDeviceEnumerator> enumerator;
            check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
                                   IID_PPV_ARGS(&enumerator)));
            ComPtr<IMMDevice> device;
            check(enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device));
            ComPtr<IAudioClient> client;
            check(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                                   reinterpret_cast<void **>(client.GetAddressOf())));
            WAVEFORMATEX format{};
            format.wFormatTag = WAVE_FORMAT_PCM;
            format.nChannels = 2;
            format.nSamplesPerSec = 48000;
            format.wBitsPerSample = 16;
            format.nBlockAlign = 4;
            format.nAvgBytesPerSec = 192000;
            check(client->Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
                    AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                1000000, 0, &format, nullptr));
            Handle event(CreateEventW(nullptr, FALSE, FALSE, nullptr));
            if (!event.value)
            {
                throw winrt::hresult_error(HRESULT_FROM_WIN32(GetLastError()));
            }
            check(client->SetEventHandle(event.value));
            ComPtr<IAudioCaptureClient> capture;
            check(client->GetService(IID_PPV_ARGS(&capture)));
            check(client->Start());
            {
                std::lock_guard<std::mutex> lock(mutex);
                ready = true;
            }
            cv.notify_all();
            for (;;)
            {
                if (WaitForSingleObject(event.value, 20) == WAIT_FAILED)
                {
                    winfail();
                }
                UINT32 count;
                check(capture->GetNextPacketSize(&count));
                while (count)
                {
                    BYTE *data;
                    UINT32 frames;
                    DWORD flags;
                    UINT64 position, qpc;
                    check(capture->GetBuffer(&data, &frames, &flags, &position, &qpc));
                    AudioPacket packet;
                    packet.timestamp = static_cast<int64_t>(qpc);
                    try
                    {
                        packet.data.resize(static_cast<size_t>(frames) * 2);
                        if (!(flags & AUDCLNT_BUFFERFLAGS_SILENT))
                        {
                            std::memcpy(packet.data.data(), data,
                                        packet.data.size() * sizeof(int16_t));
                        }
                        if (flags & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR)
                        {
                            packet.timestamp =
                                clock100() - static_cast<int64_t>(frames) * 10000000 / 48000;
                        }
                    }
                    catch (...)
                    {
                        capture->ReleaseBuffer(frames);
                        throw;
                    }
                    check(capture->ReleaseBuffer(frames));
                    {
                        std::lock_guard<std::mutex> lock(mutex);
                        if (queued + packet.data.size() > 48000 * 2 * 5)
                        {
                            throw std::runtime_error(
                                "Audio queue exceeded five seconds while the encoder was blocked");
                        }
                        queued += packet.data.size();
                        queue.push_back(std::move(packet));
                    }
                    check(capture->GetNextPacketSize(&count));
                }
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    if (stopping)
                    {
                        break;
                    }
                }
                DWORD state;
                check(device->GetState(&state));
                if (!(state & DEVICE_STATE_ACTIVE))
                {
                    throw std::runtime_error("The recording audio endpoint is no longer active");
                }
            }
            check(client->Stop());
        }
        catch (...)
        {
            std::lock_guard<std::mutex> lock(mutex);
            error = std::current_exception();
            ready = true;
        }
        cv.notify_all();
    }

  public:
    Audio()
    {
        worker = std::thread([this] { run(); });
        try
        {
            std::unique_lock<std::mutex> lock(mutex);
            if (!cv.wait_for(lock, std::chrono::seconds(10), [&] { return ready; }))
            {
                throw Timeout("Timed out starting system audio capture");
            }
            if (error)
            {
                std::rethrow_exception(error);
            }
        }
        catch (...)
        {
            stop();
            throw;
        }
    }
    ~Audio()
    {
        stop();
    }
    void stop(int64_t until = 0)
    {
        int64_t wait = until > 0 ? until + audio_grace - clock100() : 0;
        if (wait > 0)
        {
            std::this_thread::sleep_for(std::chrono::nanoseconds(wait * 100));
        }
        {
            std::lock_guard<std::mutex> lock(mutex);
            stopping = true;
        }
        if (worker.joinable())
        {
            worker.join();
        }
    }
    std::vector<int16_t> read(int64_t start, int64_t frames, int64_t origin)
    {
        std::vector<int16_t> result(static_cast<size_t>(frames) * 2, 0);
        std::lock_guard<std::mutex> lock(mutex);
        if (error)
        {
            std::rethrow_exception(error);
        }
        int64_t end = start + frames;
        for (const auto &p : queue)
        {
            int64_t ps = (p.timestamp - origin) * 48000 / 10000000,
                    pe = ps + static_cast<int64_t>(p.data.size() / 2);
            if (ps >= end)
            {
                break;
            }
            int64_t lo = std::max(start, ps), hi = std::min(end, pe);
            if (hi > lo)
            {
                std::memcpy(result.data() + (lo - start) * 2, p.data.data() + (lo - ps) * 2,
                            static_cast<size_t>(hi - lo) * 4);
            }
        }
        while (!queue.empty())
        {
            auto &p = queue.front();
            int64_t pe =
                (p.timestamp - origin) * 48000 / 10000000 + static_cast<int64_t>(p.data.size() / 2);
            if (pe > end)
            {
                break;
            }
            queued -= p.data.size();
            queue.pop_front();
        }
        return result;
    }
};
struct Result
{
    std::wstring path;
    uint64_t size = 0, frames = 0, dropped = 0;
    int width = 0, height = 0, fps = 0;
    double duration = 0;
};
struct Recorder
{
    std::mutex mutex, close_mutex;
    std::condition_variable cv;
    std::thread worker;
    bool ready = false, stopping = false, abort = false, done = false;
    int64_t cutoff = 0, origin = 0;
    std::exception_ptr error;
    Result result;
    uint32_t display_id;
    double quality;
    int synthetic_width = 0, synthetic_height = 0, delay_ms = 0;
    double limit = 0;
    bool force_software = false;
    Recorder(uint32_t id, std::wstring path, int fps, double q) : display_id(id), quality(q)
    {
        result.path = std::move(path);
        result.fps = fps;
    }
    ~Recorder()
    {
        try
        {
            finish(true);
        }
        catch (...)
        {
        }
    }
    void start()
    {
        worker = std::thread([this] { run(); });
        std::unique_lock<std::mutex> lock(mutex);
        cv.wait(lock, [&] { return ready || done; });
        if (error)
        {
            std::rethrow_exception(error);
        }
    }
    void finish(bool cancel = false)
    {
        std::lock_guard<std::mutex> closing(close_mutex);
        {
            std::lock_guard<std::mutex> lock(mutex);
            if (!stopping)
            {
                cutoff = clock100();
                stopping = true;
                abort = cancel;
            }
        }
        cv.notify_all();
        if (worker.joinable())
        {
            worker.join();
        }
        if (!cancel && error)
        {
            std::rethrow_exception(error);
        }
        if (!cancel && abort)
        {
            throw std::runtime_error("Recording was aborted");
        }
    }
    void run() noexcept
    {
        std::wstring temporary;
        try
        {
            Apartment apartment;
            Media media;
            if (GetFileAttributesW(result.path.c_str()) != INVALID_FILE_ATTRIBUTES)
            {
                throw std::runtime_error("Output path already exists");
            }
            GUID guid;
            check(CoCreateGuid(&guid));
            wchar_t suffix[40];
            if (!StringFromGUID2(guid, suffix, 40))
            {
                throw std::runtime_error("Could not format temporary file identifier");
            }
            temporary = result.path + L"." + suffix + L".tmp.mp4";
            {
                Handle reserve(CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                                           FILE_ATTRIBUTE_NORMAL, nullptr));
                if (reserve.value == INVALID_HANDLE_VALUE)
                {
                    winfail();
                }
            }
            std::unique_ptr<Capture> capture;
            if (synthetic_width)
            {
                Pixels pixels;
                pixels.width = synthetic_width;
                pixels.height = synthetic_height;
                pixels.stride = pixels.width * 4;
                pixels.data.resize(static_cast<size_t>(pixels.stride) * pixels.height);
                for (size_t i = 0; i < pixels.data.size(); i += 4)
                {
                    pixels.data[i] = 20;
                    pixels.data[i + 1] = 180;
                    pixels.data[i + 2] = 40;
                    pixels.data[i + 3] = 255;
                }
                capture = std::make_unique<Capture>(std::move(pixels));
            }
            else
            {
                capture = std::make_unique<Capture>(display_id);
            }
            auto initial = capture->first();
            result.width = static_cast<int>((initial->width + 1) & ~1U);
            result.height = static_cast<int>((initial->height + 1) & ~1U);
            ComPtr<IMFSinkWriter> writer;
            ComPtr<IMFByteStream> stream;
            DWORD video = 0, audio_index = 0;
            auto configure = [&](bool hardware)
            {
                writer.Reset();
                stream.Reset();
                check(MFCreateFile(MF_ACCESSMODE_WRITE, MF_OPENMODE_DELETE_IF_EXIST,
                                   MF_FILEFLAGS_NONE, temporary.c_str(), &stream));
                ComPtr<IMFAttributes> attrs;
                check(MFCreateAttributes(&attrs, 4));
                check(attrs->SetGUID(MF_TRANSCODE_CONTAINERTYPE, MFTranscodeContainerType_MPEG4));
                check(attrs->SetUINT32(MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS,
                                       hardware ? TRUE : FALSE));
                check(attrs->SetUINT32(MF_LOW_LATENCY, TRUE));
                // Keep MF's default blocking throttle. The capture cache stores
                // only the latest frame while this worker waits for the encoder.
                check(MFCreateSinkWriterFromURL(nullptr, stream.Get(), attrs.Get(), &writer));
                auto output = media_type(MFMediaType_Video, MFVideoFormat_H264);
                double q = quality < 0 ? 0.75 : quality;
                auto bitrate = static_cast<uint32_t>(
                    std::clamp(static_cast<double>(result.width) * result.height *
                                   std::min(result.fps, 60) * (0.03 + 0.17 * q),
                               128000.0, 100000000.0));
                check(output->SetUINT32(MF_MT_AVG_BITRATE, bitrate));
                check(output->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive));
                check(MFSetAttributeSize(output.Get(), MF_MT_FRAME_SIZE, result.width,
                                         result.height));
                check(MFSetAttributeRatio(output.Get(), MF_MT_FRAME_RATE, result.fps, 1));
                check(MFSetAttributeRatio(output.Get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1));
                check(output->SetUINT32(MF_MT_VIDEO_PRIMARIES, MFVideoPrimaries_BT709));
                check(output->SetUINT32(MF_MT_YUV_MATRIX, MFVideoTransferMatrix_BT709));
                check(output->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE, MFNominalRange_16_235));
                check(writer->AddStream(output.Get(), &video));
                auto input = media_type(MFMediaType_Video, MFVideoFormat_NV12);
                check(input->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive));
                check(
                    MFSetAttributeSize(input.Get(), MF_MT_FRAME_SIZE, result.width, result.height));
                check(MFSetAttributeRatio(input.Get(), MF_MT_FRAME_RATE, result.fps, 1));
                check(MFSetAttributeRatio(input.Get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1));
                check(input->SetUINT32(MF_MT_DEFAULT_STRIDE, result.width));
                check(input->SetUINT32(MF_MT_VIDEO_PRIMARIES, MFVideoPrimaries_BT709));
                check(input->SetUINT32(MF_MT_YUV_MATRIX, MFVideoTransferMatrix_BT709));
                check(input->SetUINT32(MF_MT_VIDEO_NOMINAL_RANGE, MFNominalRange_16_235));
                check(writer->SetInputMediaType(video, input.Get(), nullptr));
                auto ao = media_type(MFMediaType_Audio, MFAudioFormat_AAC);
                check(ao->SetUINT32(MF_MT_AUDIO_NUM_CHANNELS, 2));
                check(ao->SetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, 48000));
                check(ao->SetUINT32(MF_MT_AUDIO_BITS_PER_SAMPLE, 16));
                check(ao->SetUINT32(MF_MT_AUDIO_AVG_BYTES_PER_SECOND, 24000));
                check(writer->AddStream(ao.Get(), &audio_index));
                auto ai = media_type(MFMediaType_Audio, MFAudioFormat_PCM);
                check(ai->SetUINT32(MF_MT_AUDIO_NUM_CHANNELS, 2));
                check(ai->SetUINT32(MF_MT_AUDIO_SAMPLES_PER_SECOND, 48000));
                check(ai->SetUINT32(MF_MT_AUDIO_BITS_PER_SAMPLE, 16));
                check(ai->SetUINT32(MF_MT_AUDIO_BLOCK_ALIGNMENT, 4));
                check(ai->SetUINT32(MF_MT_AUDIO_AVG_BYTES_PER_SECOND, 192000));
                check(writer->SetInputMediaType(audio_index, ai.Get(), nullptr));
                check(writer->BeginWriting());
            };
            try
            {
                configure(!force_software);
            }
            catch (const winrt::hresult_error &)
            {
                if (force_software)
                {
                    throw;
                }
                configure(false);
            }
            std::unique_ptr<Audio> audio;
            if (!synthetic_width)
            {
                audio = std::make_unique<Audio>();
            }
            auto pending = capture->nv12(initial);
            int64_t previous_pts = 0, previous_tick = 0, audio_position = 0;
            {
                std::lock_guard<std::mutex> lock(mutex);
                origin = clock100();
                ready = true;
            }
            cv.notify_all();
            auto write_audio = [&](int64_t until)
            {
                int64_t end = until * 48000 / 10000000;
                while (audio_position < end)
                {
                    int64_t count = std::min<int64_t>(4800, end - audio_position);
                    std::vector<int16_t> pcm;
                    if (audio)
                    {
                        pcm = audio->read(audio_position, count, origin);
                    }
                    else
                    {
                        pcm.resize(static_cast<size_t>(count) * 2);
                        for (int64_t i = 0; i < count; ++i)
                        {
                            int16_t n = static_cast<int16_t>(
                                6000 *
                                std::sin((audio_position + i) * 6.283185307179586 * 440 / 48000));
                            pcm[i * 2] = pcm[i * 2 + 1] = n;
                        }
                    }
                    int64_t pts = audio_position * 10000000 / 48000,
                            duration = (audio_position + count) * 10000000 / 48000 - pts;
                    auto s = sample(pcm.data(), pcm.size() * 2, pts, duration);
                    check(writer->WriteSample(audio_index, s.Get()));
                    audio_position += count;
                }
            };
            auto write_video = [&](int64_t end, bool final = false)
            {
                if (end <= previous_pts)
                {
                    return;
                }
                write_audio(final ? end : std::max<int64_t>(0, end - audio_grace));
                auto s = sample(pending.data(), pending.size(), previous_pts, end - previous_pts);
                if (delay_ms)
                {
                    Sleep(static_cast<DWORD>(delay_ms));
                }
                check(writer->WriteSample(video, s.Get()));
                ++result.frames;
            };
            for (;;)
            {
                int64_t due = origin + (previous_tick + 1) * 10000000 / result.fps;
                {
                    std::unique_lock<std::mutex> lock(mutex);
                    if (limit > 0 && clock100() >= origin + static_cast<int64_t>(limit * 10000000))
                    {
                        stopping = true;
                        cutoff = origin + static_cast<int64_t>(limit * 10000000);
                    }
                    if (!stopping)
                    {
                        int64_t wait = due - clock100();
                        if (wait > 0)
                        {
                            cv.wait_for(lock, std::chrono::nanoseconds(wait * 100),
                                        [&] { return stopping; });
                        }
                    }
                    if (stopping)
                    {
                        break;
                    }
                }
                int64_t tick =
                    std::max(previous_tick + 1, (clock100() - origin) * result.fps / 10000000);
                int64_t pts = tick * 10000000 / result.fps;
                auto frame = capture->latest();
                if (!frame)
                {
                    throw std::runtime_error("Capture stopped during recording");
                }
                if (frame->width != initial->width || frame->height != initial->height)
                {
                    throw std::runtime_error("Display dimensions changed during recording");
                }
                auto next = capture->nv12(frame);
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    // Startup/CPU conversion can be slow. Do not publish a
                    // sample extending past a stop request received meanwhile.
                    if (stopping || (limit > 0 && pts >= static_cast<int64_t>(limit * 10000000)))
                    {
                        if (!stopping)
                        {
                            stopping = true;
                            cutoff = origin + static_cast<int64_t>(limit * 10000000);
                        }
                        break;
                    }
                }
                write_video(pts);
                result.dropped += static_cast<uint64_t>(tick - previous_tick - 1);
                pending = std::move(next);
                previous_pts = pts;
                previous_tick = tick;
            }
            capture->stop();
            bool cancel;
            int64_t end;
            {
                std::lock_guard<std::mutex> lock(mutex);
                cancel = abort;
                end = std::max<int64_t>(1, cutoff - origin);
            }
            if (audio)
            {
                audio->stop(cancel ? 0 : origin + end);
            }
            if (cancel)
            {
                writer.Reset();
                stream.Reset();
                DeleteFileW(temporary.c_str());
                temporary.clear();
            }
            else
            {
                write_video(end, true);
                // Include missed slots at the tail when stop arrives during
                // a slow WriteSample and no subsequent sampling tick occurs.
                uint64_t expected =
                    static_cast<uint64_t>(end / 10000000) * result.fps +
                    static_cast<uint64_t>((end % 10000000) * result.fps + 9999999) / 10000000;
                result.dropped = expected > result.frames ? expected - result.frames : 0;
                check(writer->Finalize());
                writer.Reset();
                // Finalize shuts down the sink and closes its byte stream.
                // Calling Close a second time returns E_INVALIDARG on Windows.
                stream.Reset();
                result.duration = end / 10000000.0;
                WIN32_FILE_ATTRIBUTE_DATA info{};
                wincheck(GetFileAttributesExW(temporary.c_str(), GetFileExInfoStandard, &info));
                result.size = (static_cast<uint64_t>(info.nFileSizeHigh) << 32) | info.nFileSizeLow;
                wincheck(
                    MoveFileExW(temporary.c_str(), result.path.c_str(), MOVEFILE_WRITE_THROUGH));
                temporary.clear();
            }
        }
        catch (...)
        {
            std::lock_guard<std::mutex> lock(mutex);
            error = std::current_exception();
        }
        if (!temporary.empty())
        {
            DeleteFileW(temporary.c_str());
        }
        {
            std::lock_guard<std::mutex> lock(mutex);
            done = true;
        }
        cv.notify_all();
    }
};
constexpr const char *recording_name = "scapkit.windows.recording";
void destroy_recording(PyObject *capsule) noexcept
{
    auto *r = static_cast<Recorder *>(PyCapsule_GetPointer(capsule, recording_name));
    if (!r)
    {
        PyErr_Clear();
        return;
    }
    NoPython detached;
    delete r;
}
PyObject *start_recorder(std::unique_ptr<Recorder> owned)
{
    auto *capsule = PyCapsule_New(owned.get(), recording_name, destroy_recording);
    if (!capsule)
    {
        return nullptr;
    }
    Recorder *r = owned.release();
    try
    {
        NoPython detached;
        r->start();
    }
    catch (...)
    {
        Py_DECREF(capsule);
        throw;
    }
    return capsule;
}
std::wstring path_string(PyObject *obj)
{
    Py_ssize_t n;
    wchar_t *s = PyUnicode_AsWideCharString(obj, &n);
    if (!s)
    {
        return {};
    }
    std::wstring result;
    try
    {
        result.assign(s, static_cast<size_t>(n));
    }
    catch (...)
    {
        PyMem_Free(s);
        throw;
    }
    PyMem_Free(s);
    if (result.empty() || result.find(L'\0') != std::wstring::npos)
    {
        throw std::invalid_argument("Invalid output path");
    }
    return result;
}
PyObject *result_object(const Result &r)
{
    PyObject *path = PyUnicode_FromWideChar(r.path.data(), static_cast<Py_ssize_t>(r.path.size()));
    if (!path)
    {
        return nullptr;
    }
    return Py_BuildValue("{s:N,s:K,s:d,s:i,s:i,s:i,s:K,s:K}", "path", path, "size_bytes", r.size,
                         "duration_s", r.duration, "width", r.width, "height", r.height, "fps",
                         r.fps, "frames_written", r.frames, "frames_dropped", r.dropped);
}
} // namespace
PyObject *py_start_recording(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *id_obj, *path_obj, *fps_obj, *q_obj = nullptr;
            if (!PyArg_ParseTuple(args, "OUO|O", &id_obj, &path_obj, &fps_obj, &q_obj))
            {
                return nullptr;
            }
            uint32_t id, fps;
            if (!positive_u32(id_obj, id, "display_id") || !positive_u32(fps_obj, fps, "fps"))
            {
                return nullptr;
            }
            if (fps > INT_MAX)
            {
                throw std::invalid_argument("fps exceeds INT32_MAX");
            }
            double quality = 0.75;
            if (q_obj == Py_None)
            {
                quality = -1;
            }
            else if (q_obj)
            {
                if (PyBool_Check(q_obj) || (!PyFloat_Check(q_obj) && !PyLong_Check(q_obj)))
                {
                    PyErr_SetString(PyExc_TypeError, "video_quality must be a number");
                    return nullptr;
                }
                quality = PyFloat_AsDouble(q_obj);
                if (PyErr_Occurred())
                {
                    return nullptr;
                }
                if (!std::isfinite(quality) || quality < 0 || quality > 1)
                {
                    throw std::invalid_argument("video_quality must be finite and between 0 and 1");
                }
            }
            auto path = path_string(path_obj);
            if (PyErr_Occurred())
            {
                return nullptr;
            }
            auto owned =
                std::make_unique<Recorder>(id, std::move(path), static_cast<int>(fps), quality);
            return start_recorder(std::move(owned));
        });
}
PyObject *py_stop_recording(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *obj;
            if (!PyArg_ParseTuple(args, "O", &obj))
            {
                return nullptr;
            }
            auto *r = static_cast<Recorder *>(PyCapsule_GetPointer(obj, recording_name));
            if (!r)
            {
                return nullptr;
            }
            {
                NoPython detached;
                r->finish();
            }
            return result_object(r->result);
        });
}
PyObject *py_abort_recording(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *obj;
            if (!PyArg_ParseTuple(args, "O", &obj))
            {
                return nullptr;
            }
            auto *r = static_cast<Recorder *>(PyCapsule_GetPointer(obj, recording_name));
            if (!r)
            {
                return nullptr;
            }
            {
                NoPython detached;
                r->finish(true);
            }
            Py_RETURN_NONE;
        });
}
#ifdef SCAPKIT_TESTING
PyObject *py_test_recording(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *path;
            int w, h, fps, delay = 0, software = 1, live = 0;
            double duration = 1;
            if (!PyArg_ParseTuple(args, "Uiii|diip", &path, &w, &h, &fps, &duration, &delay,
                                  &software, &live))
            {
                return nullptr;
            }
            if (w < 1 || h < 1 || w > 8192 || h > 8192 || fps < 1 || fps > 240 ||
                !std::isfinite(duration) || duration <= 0 || duration > 30 || delay < 0 ||
                delay > 1000)
            {
                throw std::invalid_argument("Invalid synthetic recording arguments");
            }
            auto filename = path_string(path);
            if (PyErr_Occurred())
            {
                return nullptr;
            }
            if (live)
            {
                auto r = std::make_unique<Recorder>(0, std::move(filename), fps, 0.75);
                r->synthetic_width = w;
                r->synthetic_height = h;
                r->delay_ms = delay;
                r->force_software = software != 0;
                return start_recorder(std::move(r));
            }
            Result result;
            {
                NoPython detached;
                Recorder r(0, std::move(filename), fps, 0.75);
                r.synthetic_width = w;
                r.synthetic_height = h;
                r.delay_ms = delay;
                r.limit = duration;
                r.force_software = software != 0;
                r.start();
                {
                    std::unique_lock<std::mutex> lock(r.mutex);
                    r.cv.wait(lock, [&] { return r.done; });
                }
                r.finish();
                result = r.result;
            }
            return result_object(result);
        });
}
PyObject *py_test_decode(PyObject *, PyObject *args)
{
    return boundary(
        [&]() -> PyObject *
        {
            PyObject *path;
            if (!PyArg_ParseTuple(args, "U", &path))
            {
                return nullptr;
            }
            auto filename = path_string(path);
            if (PyErr_Occurred())
            {
                return nullptr;
            }
            std::vector<int64_t> timestamps, durations;
            uint64_t audio_frames = 0;
            int width = 0, height = 0, first_luma = -1, audio_peak = 0;
            {
                NoPython detached;
                Apartment apartment;
                Media media;
                ComPtr<IMFSourceReader> reader;
                check(MFCreateSourceReaderFromURL(filename.c_str(), nullptr, &reader));
                check(reader->SetStreamSelection(static_cast<DWORD>(MF_SOURCE_READER_ALL_STREAMS),
                                                 FALSE));
                check(reader->SetStreamSelection(
                    static_cast<DWORD>(MF_SOURCE_READER_FIRST_VIDEO_STREAM), TRUE));
                auto desired = media_type(MFMediaType_Video, MFVideoFormat_NV12);
                check(reader->SetCurrentMediaType(
                    static_cast<DWORD>(MF_SOURCE_READER_FIRST_VIDEO_STREAM), nullptr,
                    desired.Get()));
                ComPtr<IMFMediaType> actual;
                check(reader->GetCurrentMediaType(
                    static_cast<DWORD>(MF_SOURCE_READER_FIRST_VIDEO_STREAM), &actual));
                UINT32 w, h;
                check(MFGetAttributeSize(actual.Get(), MF_MT_FRAME_SIZE, &w, &h));
                width = w;
                height = h;
                for (;;)
                {
                    DWORD flags;
                    LONGLONG time;
                    ComPtr<IMFSample> s;
                    check(
                        reader->ReadSample(static_cast<DWORD>(MF_SOURCE_READER_FIRST_VIDEO_STREAM),
                                           0, nullptr, &flags, &time, &s));
                    if (flags & MF_SOURCE_READERF_ENDOFSTREAM)
                    {
                        break;
                    }
                    if (s)
                    {
                        LONGLONG d = 0;
                        s->GetSampleDuration(&d);
                        timestamps.push_back(time);
                        durations.push_back(d);
                        if (first_luma < 0)
                        {
                            ComPtr<IMFMediaBuffer> buffer;
                            check(s->ConvertToContiguousBuffer(&buffer));
                            BYTE *bytes;
                            DWORD size;
                            check(buffer->Lock(&bytes, nullptr, &size));
                            if (size)
                            {
                                first_luma = bytes[0];
                            }
                            check(buffer->Unlock());
                        }
                    }
                }
                reader.Reset();
                check(MFCreateSourceReaderFromURL(filename.c_str(), nullptr, &reader));
                check(reader->SetStreamSelection(static_cast<DWORD>(MF_SOURCE_READER_ALL_STREAMS),
                                                 FALSE));
                check(reader->SetStreamSelection(
                    static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM), TRUE));
                auto pcm = media_type(MFMediaType_Audio, MFAudioFormat_PCM);
                check(reader->SetCurrentMediaType(
                    static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM), nullptr, pcm.Get()));
                for (;;)
                {
                    DWORD flags;
                    LONGLONG time;
                    ComPtr<IMFSample> s;
                    check(
                        reader->ReadSample(static_cast<DWORD>(MF_SOURCE_READER_FIRST_AUDIO_STREAM),
                                           0, nullptr, &flags, &time, &s));
                    if (flags & MF_SOURCE_READERF_ENDOFSTREAM)
                    {
                        break;
                    }
                    if (s)
                    {
                        DWORD bytes;
                        check(s->GetTotalLength(&bytes));
                        audio_frames += bytes / 4;
                        ComPtr<IMFMediaBuffer> buffer;
                        check(s->ConvertToContiguousBuffer(&buffer));
                        BYTE *data;
                        DWORD size;
                        check(buffer->Lock(&data, nullptr, &size));
                        for (DWORD i = 0; i + 1 < size; i += 2)
                        {
                            int16_t value;
                            std::memcpy(&value, data + i, sizeof(value));
                            audio_peak = std::max(audio_peak, std::abs(static_cast<int>(value)));
                        }
                        check(buffer->Unlock());
                    }
                }
            }
            PyObject *list = PyList_New(static_cast<Py_ssize_t>(timestamps.size()));
            if (!list)
            {
                return nullptr;
            }
            for (size_t i = 0; i < timestamps.size(); ++i)
            {
                auto *item = Py_BuildValue("LL", timestamps[i], durations[i]);
                if (!item)
                {
                    Py_DECREF(list);
                    return nullptr;
                }
                PyList_SET_ITEM(list, static_cast<Py_ssize_t>(i), item);
            }
            return Py_BuildValue("{s:N,s:i,s:i,s:K,s:i,s:i}", "video", list, "width", width,
                                 "height", height, "audio_frames", audio_frames, "first_luma",
                                 first_luma, "audio_peak", audio_peak);
        });
}
#endif
} // namespace scap
