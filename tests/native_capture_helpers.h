// Included only in SCAPKIT_TESTING builds. Never asks ScreenCaptureKit for content.
#include <stdatomic.h>
static atomic_long synthetic_live_frames;
static atomic_long synthetic_live_streams;
static atomic_long synthetic_live_delegates;
static atomic_long synthetic_live_callback_blocks;
static atomic_long synthetic_stop_calls;
static atomic_long synthetic_callbacks;
static atomic_long synthetic_attached_outputs;

@interface ScapkitTestStreamOutput : ScapkitStreamOutput
@end

@implementation ScapkitTestStreamOutput
- (instancetype)init
{
    self = [super init];
    if (self)
    {
        atomic_fetch_add(&synthetic_live_delegates, 1);
    }
    return self;
}
- (void)dealloc
{
    atomic_fetch_sub(&synthetic_live_delegates, 1);
}
@end

// Counts the lifetime of the copied production callback, not ScapkitCompletion
// itself. Releasing handler also releases any stream/result it captured.
@interface ScapkitTestCallback : NSObject
@property (nonatomic, copy) void (^handler)(NSError *);
// Owns a CF retain independent of the fake stream's lifetime.
@property (nonatomic, assign) CMSampleBufferRef sample;
@end

@implementation ScapkitTestCallback
- (instancetype)init
{
    self = [super init];
    if (self)
    {
        atomic_fetch_add(&synthetic_live_callback_blocks, 1);
    }
    return self;
}
- (void)dealloc
{
    if (_sample)
    {
        CFRelease(_sample);
    }
    atomic_fetch_sub(&synthetic_live_callback_blocks, 1);
}
@end

typedef enum
{
    ScapkitTestNoStream,
    ScapkitTestStopSuccess,
    ScapkitTestStopError,
    ScapkitTestStopDelayed,
} ScapkitTestStopMode;

// Deliberately an NSObject: never initialize or start an actual SCStream.
@interface ScapkitTestStream : NSObject
@property (nonatomic, strong) ScapkitTestStreamOutput *output;
@property (nonatomic, strong) dispatch_queue_t queue;
@property (nonatomic) ScapkitTestStopMode mode;
// Takes ownership of the Create reference supplied during fixture setup.
@property (nonatomic, assign) CMSampleBufferRef sample;
- (void)stopCaptureWithCompletionHandler:(void (^)(NSError *))completion;
@end

@implementation ScapkitTestStream
- (instancetype)init
{
    self = [super init];
    if (self)
    {
        atomic_fetch_add(&synthetic_live_streams, 1);
    }
    return self;
}
- (void)dealloc
{
    if (_sample)
    {
        CFRelease(_sample);
    }
    atomic_fetch_sub(&synthetic_live_streams, 1);
}
- (void)stopCaptureWithCompletionHandler:(void (^)(NSError *))completion
{
    atomic_fetch_add(&synthetic_stop_calls, 1);
    ScapkitTestCallback *callback = [[ScapkitTestCallback alloc] init];
    if (!callback)
    {
        completion([NSError errorWithDomain:@"ScapkitSynthetic" code:2
            userInfo:@{NSLocalizedDescriptionKey: @"could not allocate synthetic callback"}]);
        return;
    }
    callback.handler = completion;
    callback.sample = (CMSampleBufferRef)CFRetain(self.sample);
    ScapkitTestStreamOutput *output = self.output;
    BOOL fail = self.mode == ScapkitTestStopError;
    // Capture the output and callback only, never self or a raw handle. Thus the
    // production completion block must retain the stream across a late reply.
    void (^finish)(void) = ^{
        @autoreleasepool
        {
            if (output.handle != NULL)
            {
                // Report missing detachment without dereferencing freed memory.
                atomic_fetch_add(&synthetic_attached_outputs, 1);
            }
            else
            {
                [output stream:nil didOutputSampleBuffer:callback.sample ofType:SCStreamOutputTypeScreen];
            }
            NSError *error = fail ? [NSError errorWithDomain:@"ScapkitSynthetic" code:1
                userInfo:@{NSLocalizedDescriptionKey: @"synthetic stop failure"}] : nil;
            callback.handler(error);
            atomic_fetch_add(&synthetic_callbacks, 1);
        }
    };
    if (self.mode == ScapkitTestStopDelayed)
    {
        // The production wait expires at five seconds; reply after six.
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 6 * NSEC_PER_SEC), self.queue, finish);
    }
    else
    {
        dispatch_sync(self.queue, finish);
    }
}
@end

PyObject *scapkit_test_lifecycle_stats(PyObject *self, PyObject *args)
{
    return Py_BuildValue("{s:l,s:l,s:l,s:l,s:l,s:l,s:l}",
        "frames", atomic_load(&synthetic_live_frames),
        "streams", atomic_load(&synthetic_live_streams),
        "delegates", atomic_load(&synthetic_live_delegates),
        "callback_blocks", atomic_load(&synthetic_live_callback_blocks),
        "stop_calls", atomic_load(&synthetic_stop_calls),
        "callbacks", atomic_load(&synthetic_callbacks),
        "attached_outputs", atomic_load(&synthetic_attached_outputs));
}

static void release_synthetic_bytes(void *context, const void *bytes)
{
    free((void *)bytes);
    atomic_fetch_sub(&synthetic_live_frames, 1);
}

PyObject *scapkit_test_live_frames(PyObject *self, PyObject *args)
{
    return PyLong_FromLong(atomic_load(&synthetic_live_frames));
}

static CVPixelBufferRef synthetic_frame(void)
{
    CVPixelBufferRef frame = NULL;
    void *bytes = calloc(8 * 8, 4);
    if (!bytes)
    {
        PyErr_NoMemory();
        return NULL;
    }
    atomic_fetch_add(&synthetic_live_frames, 1);
    CVReturn status = CVPixelBufferCreateWithBytes(kCFAllocatorDefault, 8, 8,
        kCVPixelFormatType_32BGRA, bytes, 8 * 4, release_synthetic_bytes, NULL, NULL, &frame);
    if (status != kCVReturnSuccess)
    {
        free(bytes);
        atomic_fetch_sub(&synthetic_live_frames, 1);
        PyErr_SetString(PyExc_OSError, "could not create synthetic pixel buffer");
        return NULL;
    }
    if (CVPixelBufferLockBaseAddress(frame, 0) != kCVReturnSuccess)
    {
        CVPixelBufferRelease(frame);
        PyErr_SetString(PyExc_OSError, "could not lock synthetic pixel buffer");
        return NULL;
    }
    unsigned char *base = CVPixelBufferGetBaseAddress(frame);
    size_t row = CVPixelBufferGetBytesPerRow(frame);
    memset(base, 0, row * 8);
    for (size_t y = 0; y < 8; y++)
    {
        for (size_t x = 0; x < 8; x++)
        {
            unsigned char *pixel = base + y * row + x * 4;
            pixel[0] = 32;
            pixel[1] = 64;
            pixel[2] = 128;
            pixel[3] = 255;
        }
    }
    CVPixelBufferUnlockBaseAddress(frame, 0);
    return frame;
}

PyObject *scapkit_test_capture(PyObject *self, PyObject *args)
{
    const char *mode_name = "none";
    if (!PyArg_ParseTuple(args, "|s:_test_capture", &mode_name))
    {
        return NULL;
    }
    ScapkitTestStopMode mode;
    if (strcmp(mode_name, "none") == 0)
    {
        mode = ScapkitTestNoStream;
    }
    else if (strcmp(mode_name, "success") == 0)
    {
        mode = ScapkitTestStopSuccess;
    }
    else if (strcmp(mode_name, "error") == 0)
    {
        mode = ScapkitTestStopError;
    }
    else if (strcmp(mode_name, "delayed") == 0)
    {
        mode = ScapkitTestStopDelayed;
    }
    else
    {
        PyErr_SetString(PyExc_ValueError, "test mode must be none, success, error, or delayed");
        return NULL;
    }
    @autoreleasepool
    {
        scapkit_handle *h = new_handle();
        if (!h)
        {
            return NULL;
        }
        h->current_frame = synthetic_frame();
        if (!h->current_frame)
        {
            dispose_handle(h);
            return NULL;
        }
        if (mode != ScapkitTestNoStream)
        {
            ScapkitTestStreamOutput *output = [[ScapkitTestStreamOutput alloc] init];
            ScapkitTestStream *stream = [[ScapkitTestStream alloc] init];
            if (!output || !stream)
            {
                dispose_handle(h);
                return PyErr_NoMemory();
            }
            CMVideoFormatDescriptionRef format = NULL;
            CMSampleBufferRef sample = NULL;
            OSStatus status = CMVideoFormatDescriptionCreateForImageBuffer(kCFAllocatorDefault,
                h->current_frame, &format);
            if (status == noErr)
            {
                CMSampleTimingInfo timing = {kCMTimeInvalid, kCMTimeZero, kCMTimeInvalid};
                status = CMSampleBufferCreateReadyWithImageBuffer(kCFAllocatorDefault,
                    h->current_frame, format, &timing, &sample);
                CFRelease(format);
            }
            if (status != noErr)
            {
                dispose_handle(h);
                PyErr_SetString(PyExc_OSError, "could not create fake stream sample");
                return NULL;
            }
            stream.sample = sample;
            // No stream is registered yet, so no callback can race this setup.
            h->delegate.handle = NULL;
            h->delegate = output;
            output.handle = h;
            stream.output = output;
            stream.queue = h->queue;
            stream.mode = mode;
            h->stream = (SCStream *)stream;
        }
        return make_capsule(h);
    }
}

PyObject *scapkit_test_update_frame(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "O", &capsule))
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
        CVPixelBufferRef frame = synthetic_frame();
        if (!frame)
        {
            return NULL;
        }
        CMSampleBufferRef sample = NULL;
        CMVideoFormatDescriptionRef format = NULL;
        OSStatus status = CMVideoFormatDescriptionCreateForImageBuffer(kCFAllocatorDefault, frame, &format);
        if (status == noErr)
        {
            CMSampleTimingInfo timing = {kCMTimeInvalid, kCMTimeZero, kCMTimeInvalid};
            status = CMSampleBufferCreateReadyWithImageBuffer(kCFAllocatorDefault, frame, format, &timing, &sample);
            CFRelease(format);
        }
        CVPixelBufferRelease(frame);
        if (status != noErr)
        {
            PyErr_SetString(PyExc_OSError, "could not create synthetic sample");
            return NULL;
        }
        NSArray *attachments = (__bridge NSArray *)CMSampleBufferGetSampleAttachmentsArray(sample, true);
        NSMutableDictionary *info = attachments[0];
        info[SCStreamFrameInfoStatus] = @(SCFrameStatusComplete);
        Py_BEGIN_ALLOW_THREADS
        dispatch_sync(h->queue, ^{
            [h->delegate stream:nil didOutputSampleBuffer:sample ofType:SCStreamOutputTypeScreen];
        });
        Py_END_ALLOW_THREADS
        CFRelease(sample);
        Py_RETURN_NONE;
    }
}
