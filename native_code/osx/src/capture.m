#include "capture.h"
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>
#import <ImageIO/ImageIO.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#include <dispatch/dispatch.h>
#include <pthread.h>
#include <stdint.h>

@class ScapkitStreamOutput;
typedef struct scapkit_handle_t
{
    // Mutable capture state and the delegate's back-pointer belong to queue.
    SCStream *stream;
    dispatch_queue_t queue;
    CVPixelBufferRef current_frame;
    ScapkitStreamOutput *delegate;
    BOOL stopped;
    // Readers retain a snapshot on queue, then serialize pixel-buffer locking.
    pthread_mutex_t read_lock;
} scapkit_handle;

@interface ScapkitStreamOutput : NSObject <SCStreamOutput>
@property (nonatomic, assign) scapkit_handle *handle;
- (void)stream:(SCStream * _Nullable)stream didOutputSampleBuffer:(CMSampleBufferRef)sampleBuffer ofType:(SCStreamOutputType)type;
@end

@implementation ScapkitStreamOutput
- (void)stream:(SCStream *)stream didOutputSampleBuffer:(CMSampleBufferRef)sampleBuffer ofType:(SCStreamOutputType)type
{
    @autoreleasepool
    {
        // ScreenCaptureKit invokes this method on handle->queue only.
        scapkit_handle *h = self.handle;
        if (!h || h->stopped || type != SCStreamOutputTypeScreen || !CMSampleBufferIsValid(sampleBuffer))
        {
            return;
        }
        CFArrayRef attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, false);
        if (!attachments || CFArrayGetCount(attachments) == 0)
        {
            return;
        }
        NSDictionary *info = (__bridge NSDictionary *)CFArrayGetValueAtIndex(attachments, 0);
        NSNumber *status = info[SCStreamFrameInfoStatus];
        if (!status || status.integerValue != SCFrameStatusComplete)
        {
            return;
        }
        CVPixelBufferRef frame = CMSampleBufferGetImageBuffer(sampleBuffer);
        if (!frame)
        {
            return;
        }
        CVPixelBufferRetain(frame);
        if (h->current_frame)
        {
            CVPixelBufferRelease(h->current_frame);
        }
        h->current_frame = frame;
    }
}
@end

// Completion blocks own this object even when a Python call times out.
// All result fields are accessed under @synchronized(result).
@interface ScapkitCompletion : NSObject
{
@public
    dispatch_semaphore_t semaphore;
    NSError *error;
    SCDisplay *display;
    BOOL abandoned;
    BOOL completed;
}
@end
@implementation ScapkitCompletion
- (instancetype)init
{
    self = [super init];
    if (self)
    {
        semaphore = dispatch_semaphore_create(0);
        if (!semaphore)
        {
            return nil;
        }
    }
    return self;
}
@end

static scapkit_handle *new_handle(void)
{
    scapkit_handle *h = calloc(1, sizeof(*h));
    if (!h)
    {
        PyErr_NoMemory();
        return NULL;
    }
    if (pthread_mutex_init(&h->read_lock, NULL) != 0)
    {
        free(h);
        PyErr_SetString(PyExc_RuntimeError, "failed to create capture reader lock");
        return NULL;
    }
    h->queue = dispatch_queue_create("com.scapkit.capture", DISPATCH_QUEUE_SERIAL);
    h->delegate = [[ScapkitStreamOutput alloc] init];
    if (!h->queue || !h->delegate)
    {
        h->queue = nil;
        h->delegate = nil;
        pthread_mutex_destroy(&h->read_lock);
        free(h);
        PyErr_NoMemory();
        return NULL;
    }
    h->delegate.handle = h;
    return h;
}

// Must be called with the Python thread state detached. Never mutate the capsule:
// its pointer stays valid until the last Python reference is destroyed.
static SCStream *close_handle(scapkit_handle *h)
{
    __block SCStream *stream = nil;
    dispatch_sync(h->queue, ^{
        h->stopped = YES;
        h->delegate.handle = NULL;
        stream = h->stream;
        h->stream = nil;
        if (h->current_frame)
        {
            CVPixelBufferRelease(h->current_frame);
            h->current_frame = NULL;
        }
    });
    return stream;
}

static void stop_without_waiting(SCStream *stream)
{
    if (stream)
    {
        // Keep the stream alive until its completion; no raw handle is captured.
        [stream stopCaptureWithCompletionHandler:^(NSError *error) {
            (void)stream;
        }];
    }
}

static void dispose_handle(scapkit_handle *h)
{
    Py_BEGIN_ALLOW_THREADS
    stop_without_waiting(close_handle(h));
    Py_END_ALLOW_THREADS
    // ARC does not destroy strong fields when a calloc'd struct is freed.
    h->delegate = nil;
    h->queue = nil;
    pthread_mutex_destroy(&h->read_lock);
    free(h);
}

static void capsule_destructor(PyObject *capsule)
{
    @autoreleasepool
    {
        scapkit_handle *h = PyCapsule_GetPointer(capsule, "scapkit_handle");
        if (h)
        {
            dispose_handle(h);
        }
        else
        {
            PyErr_WriteUnraisable(capsule);
        }
    }
}

static PyObject *make_capsule(scapkit_handle *h)
{
    PyObject *capsule = PyCapsule_New(h, "scapkit_handle", capsule_destructor);
    if (!capsule)
    {
        dispose_handle(h);
    }
    return capsule;
}

static long wait_for_completion(ScapkitCompletion *result)
{
    long timed_out;
    Py_BEGIN_ALLOW_THREADS
    timed_out = dispatch_semaphore_wait(result->semaphore, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));
    Py_END_ALLOW_THREADS
    return timed_out;
}

PyObject *scapkit_start_capture(PyObject *self, PyObject *args)
{
    unsigned long long display_id;
    PyObject *display_arg;
    if (!PyArg_ParseTuple(args, "O:start_capture", &display_arg))
    {
        return NULL;
    }
    display_id = PyLong_AsUnsignedLongLong(display_arg);
    if (PyErr_Occurred())
    {
        return NULL;
    }
    if (display_id == 0 || display_id > UINT32_MAX)
    {
        PyErr_SetString(PyExc_ValueError, "display_id must be a nonzero 32-bit display ID");
        return NULL;
    }
    @autoreleasepool
    {
        ScapkitCompletion *content_result = [[ScapkitCompletion alloc] init];
        if (!content_result)
        {
            return PyErr_NoMemory();
        }
        [SCShareableContent getShareableContentExcludingDesktopWindows:NO
                                                  onScreenWindowsOnly:NO
                                                    completionHandler:^(SCShareableContent *content, NSError *error) {
            @autoreleasepool
            {
                @synchronized(content_result)
                {
                    content_result->error = error;
                    for (SCDisplay *d in content.displays)
                    {
                        if (d.displayID == (CGDirectDisplayID)display_id)
                        {
                            content_result->display = d;
                            break;
                        }
                    }
                }
                dispatch_semaphore_signal(content_result->semaphore);
            }
        }];
        if (wait_for_completion(content_result))
        {
            PyErr_SetString(PyExc_TimeoutError, "SCShareableContent timed out");
            return NULL;
        }
        SCDisplay *target_display;
        NSError *content_error;
        @synchronized(content_result)
        {
            target_display = content_result->display;
            content_error = content_result->error;
        }
        if (content_error)
        {
            PyErr_Format(PyExc_OSError, "SCShareableContent failed: %s", content_error.localizedDescription.UTF8String);
            return NULL;
        }
        if (!target_display)
        {
            PyErr_Format(PyExc_ValueError, "display %llu not found", display_id);
            return NULL;
        }
        CGDisplayModeRef mode = CGDisplayCopyDisplayMode((CGDirectDisplayID)display_id);
        if (!mode)
        {
            PyErr_SetString(PyExc_OSError, "failed to get display mode");
            return NULL;
        }
        size_t pixel_width = CGDisplayModeGetPixelWidth(mode);
        size_t pixel_height = CGDisplayModeGetPixelHeight(mode);
        CGDisplayModeRelease(mode);
        if (!pixel_width || !pixel_height)
        {
            PyErr_SetString(PyExc_OSError, "display mode has no pixels");
            return NULL;
        }
        SCContentFilter *filter = [[SCContentFilter alloc] initWithDisplay:target_display excludingWindows:@[]];
        SCStreamConfiguration *config = [[SCStreamConfiguration alloc] init];
        if (!filter || !config)
        {
            return PyErr_NoMemory();
        }
        config.width = pixel_width;
        config.height = pixel_height;
        config.pixelFormat = kCVPixelFormatType_32BGRA;
        config.showsCursor = YES;
        scapkit_handle *h = new_handle();
        if (!h)
        {
            return NULL;
        }
        SCStream *stream = [[SCStream alloc] initWithFilter:filter configuration:config delegate:nil];
        h->stream = stream;
        NSError *add_error = nil;
        if (!stream || ![stream addStreamOutput:h->delegate type:SCStreamOutputTypeScreen sampleHandlerQueue:h->queue error:&add_error])
        {
            dispose_handle(h);
            PyErr_Format(PyExc_OSError, "addStreamOutput failed: %s", add_error ? add_error.localizedDescription.UTF8String : "could not create stream/output");
            return NULL;
        }
        ScapkitCompletion *start_result = [[ScapkitCompletion alloc] init];
        if (!start_result)
        {
            dispose_handle(h);
            return PyErr_NoMemory();
        }
        [stream startCaptureWithCompletionHandler:^(NSError *error) {
            @autoreleasepool
            {
                BOOL abandoned;
                @synchronized(start_result)
                {
                    start_result->error = error;
                    start_result->completed = YES;
                    abandoned = start_result->abandoned;
                }
                if (abandoned)
                {
                    stop_without_waiting(stream);
                }
                dispatch_semaphore_signal(start_result->semaphore);
            }
        }];
        if (wait_for_completion(start_result))
        {
            BOOL completed;
            @synchronized(start_result)
            {
                start_result->abandoned = YES;
                completed = start_result->completed;
            }
            // Detach the output now. If start is still in flight, its completion
            // performs stop afterwards, so a late start cannot escape cleanup.
            Py_BEGIN_ALLOW_THREADS
            close_handle(h);
            if (completed)
            {
                stop_without_waiting(stream);
            }
            Py_END_ALLOW_THREADS
            dispose_handle(h);
            PyErr_SetString(PyExc_TimeoutError, "startCapture timed out");
            return NULL;
        }
        NSError *start_error;
        @synchronized(start_result)
        {
            start_error = start_result->error;
        }
        if (start_error)
        {
            dispose_handle(h);
            PyErr_Format(PyExc_OSError, "startCapture failed: %s", start_error.localizedDescription.UTF8String);
            return NULL;
        }
        return make_capsule(h);
    }
}

PyObject *scapkit_stop_capture(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "O:stop_capture", &capsule))
    {
        return NULL;
    }
    scapkit_handle *h = PyCapsule_GetPointer(capsule, "scapkit_handle");
    if (!h)
    {
        return NULL;
    }
    @autoreleasepool
    {
        SCStream *stream;
        Py_BEGIN_ALLOW_THREADS
        stream = close_handle(h);
        Py_END_ALLOW_THREADS
        if (stream)
        {
            ScapkitCompletion *result = [[ScapkitCompletion alloc] init];
            if (!result)
            {
                stop_without_waiting(stream);
                return PyErr_NoMemory();
            }
            [stream stopCaptureWithCompletionHandler:^(NSError *error) {
                @synchronized(result)
                {
                    result->error = error;
                }
                (void)stream;
                dispatch_semaphore_signal(result->semaphore);
            }];
            if (wait_for_completion(result))
            {
                PyErr_SetString(PyExc_TimeoutError, "stopCapture timed out; handle is closed");
                return NULL;
            }
            NSError *error;
            @synchronized(result)
            {
                error = result->error;
            }
            if (error)
            {
                PyErr_Format(PyExc_OSError, "stopCapture failed: %s", error.localizedDescription.UTF8String);
                return NULL;
            }
        }
        Py_RETURN_NONE;
    }
}

static CVPixelBufferRef retain_frame(scapkit_handle *h)
{
    __block CVPixelBufferRef frame = NULL;
    Py_BEGIN_ALLOW_THREADS
    dispatch_sync(h->queue, ^{
        frame = h->current_frame;
        if (frame)
        {
            CVPixelBufferRetain(frame);
        }
    });
    Py_END_ALLOW_THREADS
    return frame;
}

static int validate_frame_layout(CVPixelBufferRef frame)
{
    size_t row = CVPixelBufferGetBytesPerRow(frame);
    size_t height = CVPixelBufferGetHeight(frame);
    size_t width = CVPixelBufferGetWidth(frame);
    if (CVPixelBufferIsPlanar(frame) || CVPixelBufferGetPixelFormatType(frame) != kCVPixelFormatType_32BGRA ||
        !width || !height || width > (size_t)PY_SSIZE_T_MAX / 4 || row < width * 4 ||
        row > (size_t)PY_SSIZE_T_MAX / height)
    {
        PyErr_SetString(PyExc_OSError, "invalid BGRA pixel buffer");
        return 0;
    }
    return 1;
}

PyObject *scapkit_current_frame_bgra(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "O:current_frame_bgra", &capsule))
    {
        return NULL;
    }
    scapkit_handle *h = PyCapsule_GetPointer(capsule, "scapkit_handle");
    if (!h)
    {
        return NULL;
    }
    CVPixelBufferRef frame = retain_frame(h);
    if (!frame)
    {
        Py_RETURN_NONE;
    }
    if (!validate_frame_layout(frame))
    {
        CVPixelBufferRelease(frame);
        return NULL;
    }
    size_t row = CVPixelBufferGetBytesPerRow(frame);
    size_t height = CVPixelBufferGetHeight(frame);
    size_t width = CVPixelBufferGetWidth(frame);
    // Allocate before locking: Python allocation/GC may reenter this function.
    PyObject *data = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)(row * height));
    if (!data)
    {
        CVPixelBufferRelease(frame);
        return NULL;
    }
    char *destination = PyBytes_AS_STRING(data);
    BOOL copied = NO;
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&h->read_lock);
    if (CVPixelBufferLockBaseAddress(frame, kCVPixelBufferLock_ReadOnly) == kCVReturnSuccess)
    {
        const void *base = CVPixelBufferGetBaseAddress(frame);
        if (base)
        {
            memcpy(destination, base, row * height);
            copied = YES;
        }
        CVPixelBufferUnlockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
    }
    CVPixelBufferRelease(frame);
    pthread_mutex_unlock(&h->read_lock);
    Py_END_ALLOW_THREADS
    if (!copied)
    {
        Py_DECREF(data);
        PyErr_SetString(PyExc_OSError, "failed to access pixel buffer");
        return NULL;
    }
    PyObject *result = Py_BuildValue("{s:O,s:n,s:n,s:n}",
        "data", data, "width", (Py_ssize_t)width,
        "height", (Py_ssize_t)height, "bytes_per_row", (Py_ssize_t)row);
    Py_DECREF(data);
    return result;
}

PyObject *scapkit_current_frame_jpg(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    int quality = 80;
    if (!PyArg_ParseTuple(args, "O|i:current_frame_jpg", &capsule, &quality))
    {
        return NULL;
    }
    if (quality < 0 || quality > 100)
    {
        PyErr_SetString(PyExc_ValueError, "quality must be between 0 and 100");
        return NULL;
    }
    scapkit_handle *h = PyCapsule_GetPointer(capsule, "scapkit_handle");
    if (!h)
    {
        return NULL;
    }
    @autoreleasepool
    {
        CVPixelBufferRef frame = retain_frame(h);
        if (!frame)
        {
            Py_RETURN_NONE;
        }
        if (!validate_frame_layout(frame))
        {
            CVPixelBufferRelease(frame);
            return NULL;
        }
        NSMutableData *jpeg_data = [NSMutableData data];
        BOOL encoded = NO;
        Py_BEGIN_ALLOW_THREADS
        pthread_mutex_lock(&h->read_lock);
        CVReturn lock_status = CVPixelBufferLockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
        if (lock_status == kCVReturnSuccess && CVPixelBufferGetBaseAddress(frame))
        {
            CGColorSpaceRef color_space = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
            CGDataProviderRef provider = CGDataProviderCreateWithData(NULL, CVPixelBufferGetBaseAddress(frame),
                CVPixelBufferGetBytesPerRow(frame) * CVPixelBufferGetHeight(frame), NULL);
            CGImageRef image = color_space && provider ? CGImageCreate(
                CVPixelBufferGetWidth(frame), CVPixelBufferGetHeight(frame), 8, 32,
                CVPixelBufferGetBytesPerRow(frame), color_space,
                kCGBitmapByteOrder32Little | kCGImageAlphaNoneSkipFirst,
                provider, NULL, false, kCGRenderingIntentDefault) : NULL;
            CGImageDestinationRef dest = image && jpeg_data ? CGImageDestinationCreateWithData(
                (__bridge CFMutableDataRef)jpeg_data, (__bridge CFStringRef)UTTypeJPEG.identifier, 1, NULL) : NULL;
            if (dest)
            {
                NSDictionary *props = @{(__bridge NSString *)kCGImageDestinationLossyCompressionQuality: @(quality / 100.0)};
                CGImageDestinationAddImage(dest, image, (__bridge CFDictionaryRef)props);
                encoded = CGImageDestinationFinalize(dest);
                CFRelease(dest);
            }
            if (image)
            {
                CGImageRelease(image);
            }
            if (provider)
            {
                CGDataProviderRelease(provider);
            }
            if (color_space)
            {
                CGColorSpaceRelease(color_space);
            }
        }
        if (lock_status == kCVReturnSuccess)
        {
            CVPixelBufferUnlockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
        }
        CVPixelBufferRelease(frame);
        pthread_mutex_unlock(&h->read_lock);
        Py_END_ALLOW_THREADS
        if (!encoded || jpeg_data.length > (NSUInteger)PY_SSIZE_T_MAX)
        {
            PyErr_SetString(PyExc_OSError, "failed to encode JPEG");
            return NULL;
        }
        return PyBytes_FromStringAndSize((const char *)jpeg_data.bytes, (Py_ssize_t)jpeg_data.length);
    }
}

#ifdef SCAPKIT_TESTING
#include "../../../tests/native_capture_helpers.h"
#endif
