#include "recording.h"
#import "recording_writer.h"
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#import <CoreGraphics/CoreGraphics.h>
#include <sys/stat.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#ifdef SCAPKIT_TESTING
#include <stdatomic.h>
#import <AudioToolbox/AudioToolbox.h>
static _Atomic long recording_live_owners = 0;
#endif

static NSString *const RecorderErrorDomain = @"com.scapkit.recording";
enum { RecorderInvalid = 1, RecorderTimeout = 2, RecorderFailure = 3 };

static NSError *recording_error(NSInteger code, NSString *message)
{
    return [NSError errorWithDomain:RecorderErrorDomain code:code
                          userInfo:@{NSLocalizedDescriptionKey: message}];
}

static CMTime recording_now(void)
{
    return CMClockGetTime(CMClockGetHostTimeClock());
}

@class ScapkitRecorder;
// The stop completion and timeout share a single strong owner. Whichever fires
// first takes it; the other can arrive later without retaining a closed recorder.
@interface ScapkitRecordingStop : NSObject
@property(nonatomic, strong) ScapkitRecorder *owner;
@end
@implementation ScapkitRecordingStop
@end

@interface ScapkitRecordingOutput : NSObject <SCStreamOutput, SCStreamDelegate>
// Weak Objective-C ownership protects callbacks arriving after capsule disposal.
@property(nonatomic, weak) ScapkitRecorder *owner;
@end

@interface ScapkitRecorder : NSObject
{
@public
    // queue owns stream, writer, timer, all frame references and the frame clock.
    dispatch_queue_t queue;
    ScapkitRecordingWriter *writer;
    // Immutable owner of the thread-safe publication gate, even during finish.
    ScapkitRecordingWriter *publicationWriter;
    SCStream *stream;
    ScapkitRecordingOutput *output;
    dispatch_source_t timer;
    CVPixelBufferRef latest;
    CVPixelBufferRef pending;
    CMTime origin;
    CMTime pendingTime;
    CMTime nextTime;
    int fps;
    BOOL finalizing;
    BOOL synthetic;
    BOOL manualClock;
    // condition protects cross-thread startup/shutdown state, errors and results.
    NSCondition *condition;
    BOOL ready;
    BOOL stopping;
    BOOL aborting;
    BOOL done;
    CMTime cutoff;
    CMTime testTime;
    NSError *failure;
    NSDictionary *result;
#ifdef SCAPKIT_TESTING
    NSString *testMode;
    BOOL countedOwner;
#endif
}
- (instancetype)initWithWriter:(ScapkitRecordingWriter *)value fps:(int)rate;
- (void)acceptFrame:(CVPixelBufferRef)frame;
- (void)acceptAudio:(CMSampleBufferRef)sample;
- (void)advanceTo:(CMTime)time final:(BOOL)isFinal;
- (void)recordFailure:(NSError *)error;
- (NSError *)currentFailure;
- (BOOL)waitReady:(NSTimeInterval)seconds;
- (void)requestStopAt:(CMTime)time abort:(BOOL)abort;
- (NSDictionary *)waitFinished;
- (void)finishAfterStream:(NSError *)error;
@end

@implementation ScapkitRecordingOutput
- (void)stream:(SCStream *)source didOutputSampleBuffer:(CMSampleBufferRef)sample ofType:(SCStreamOutputType)type
{
    @autoreleasepool
    {
        ScapkitRecorder *owner = self.owner;
        if (!owner || !CMSampleBufferIsValid(sample))
        {
            return;
        }
        if (type == SCStreamOutputTypeAudio)
        {
            [owner acceptAudio:sample];
        }
        else if (type == SCStreamOutputTypeScreen)
        {
            CFArrayRef attachments = CMSampleBufferGetSampleAttachmentsArray(sample, false);
            if (!attachments || CFArrayGetCount(attachments) == 0)
            {
                return;
            }
            NSDictionary *info = (__bridge NSDictionary *)CFArrayGetValueAtIndex(attachments, 0);
            NSNumber *status = info[SCStreamFrameInfoStatus];
            if (status && status.integerValue == SCFrameStatusComplete)
            {
                CVPixelBufferRef frame = CMSampleBufferGetImageBuffer(sample);
                if (frame)
                {
                    [owner acceptFrame:frame];
                }
            }
        }
    }
}

- (void)stream:(SCStream *)source didStopWithError:(NSError *)error
{
    @autoreleasepool
    {
        ScapkitRecorder *owner = self.owner;
        if (owner)
        {
            [owner recordFailure:error];
            [owner requestStopAt:recording_now() abort:NO];
        }
    }
}
@end

@implementation ScapkitRecorder
- (instancetype)initWithWriter:(ScapkitRecordingWriter *)value fps:(int)rate
{
    self = [super init];
    if (self)
    {
        queue = dispatch_queue_create("com.scapkit.recording.capture", DISPATCH_QUEUE_SERIAL);
        condition = [[NSCondition alloc] init];
        output = [[ScapkitRecordingOutput alloc] init];
        writer = value;
        publicationWriter = value;
        fps = rate;
        origin = kCMTimeInvalid;
        testTime = kCMTimeZero;
        if (!queue || !condition || !output)
        {
            return nil;
        }
        output.owner = self;
#ifdef SCAPKIT_TESTING
        countedOwner = YES;
        atomic_fetch_add(&recording_live_owners, 1);
#endif
    }
    return self;
}

- (void)dealloc
{
#ifdef SCAPKIT_TESTING
    if (countedOwner)
    {
        atomic_fetch_sub(&recording_live_owners, 1);
    }
#endif
    // Explicit shutdown detaches outputs and cancels the writer before releasing
    // the last owner. These CF references also cover partially initialized state.
    if (latest)
    {
        CVPixelBufferRelease(latest);
    }
    if (pending)
    {
        CVPixelBufferRelease(pending);
    }
}

- (void)recordFailure:(NSError *)error
{
    if (!error)
    {
        return;
    }
    [condition lock];
    if (!failure && !done)
    {
        failure = error;
    }
    [condition broadcast];
    [condition unlock];
}

- (NSError *)currentFailure
{
    [condition lock];
    NSError *error = failure;
    [condition unlock];
    return error;
}

- (void)acceptFrame:(CVPixelBufferRef)frame
{
    [condition lock];
    BOOL closed = stopping;
    [condition unlock];
    if (closed || finalizing)
    {
        return;
    }
    CVPixelBufferRetain(frame);
    if (latest)
    {
        CVPixelBufferRelease(latest);
    }
    latest = frame;
    if (!pending)
    {
        pending = CVPixelBufferRetain(frame);
        pendingTime = kCMTimeZero;
        nextTime = CMTimeMake(1, fps);
        origin = manualClock ? CMTimeMake(10, 1) : recording_now();
        if (!manualClock)
        {
            timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, queue);
            if (!timer)
            {
                [self recordFailure:recording_error(RecorderFailure, @"could not create recording timer")];
                [self requestStopAt:recording_now() abort:YES];
                return;
            }
            uint64_t interval = MAX((uint64_t)1, NSEC_PER_SEC / (uint64_t)fps);
            __weak ScapkitRecorder *weakOwner = self;
            dispatch_source_set_event_handler(timer, ^{
                @autoreleasepool
                {
                    ScapkitRecorder *owner = weakOwner;
                    if (owner)
                    {
                        CMTime now = recording_now();
                        [owner->condition lock];
                        BOOL stop = owner->stopping;
                        CMTime end = owner->cutoff;
                        [owner->condition unlock];
                        [owner advanceTo:CMTimeSubtract(stop ? CMTimeMinimum(now, end) : now, owner->origin) final:NO];
                    }
                }
            });
            dispatch_source_set_timer(timer, dispatch_time(DISPATCH_TIME_NOW, (int64_t)interval), interval,
                                      MIN(interval / 10, (uint64_t)NSEC_PER_MSEC));
            dispatch_resume(timer);
        }
        [condition lock];
        ready = YES;
        [condition broadcast];
        [condition unlock];
    }
}

- (void)acceptAudio:(CMSampleBufferRef)sample
{
    if (!CMTIME_IS_NUMERIC(origin) || finalizing || [self currentFailure])
    {
        return;
    }
    [condition lock];
    BOOL closing = stopping;
    CMTime end = cutoff;
    [condition unlock];
    if (closing)
    {
        CMTime start = CMSampleBufferGetPresentationTimeStamp(sample);
        if (!CMTIME_IS_NUMERIC(start) || CMTimeCompare(start, end) >= 0)
        {
            return;
        }
        // A crossing block is bounded and held by the writer until finishAt:
        // clips its normalized PCM. CoreMedia sample-range copying cannot split
        // non-interleaved buffers, a format ScreenCaptureKit can deliver.
    }
    NSError *error = nil;
    BOOL accepted = [writer appendAudio:sample origin:origin error:&error];
    if (!accepted)
    {
        [self recordFailure:error ?: recording_error(RecorderFailure, @"recording audio append failed")];
        [self requestStopAt:recording_now() abort:NO];
    }
}

- (void)advanceTo:(CMTime)time final:(BOOL)isFinal
{
    if (!pending || finalizing || [self currentFailure])
    {
        return;
    }
    // A blocked encoder must not cause unbounded catch-up work on the capture
    // queue. Neither memory nor loop count depends without limit on fps/duration.
    if (CMTimeGetSeconds(CMTimeSubtract(time, pendingTime)) > 2.0)
    {
        [self recordFailure:recording_error(RecorderFailure, @"recording encoder fell more than two seconds behind")];
        [self requestStopAt:recording_now() abort:NO];
        return;
    }
    unsigned int submitted = 0;
    while (CMTimeCompare(nextTime, time) <= 0)
    {
        if (++submitted > 240)
        {
            [self recordFailure:recording_error(RecorderFailure, @"recording frame backlog exceeded its bound")];
            break;
        }
        NSError *error = nil;
        if (![writer appendVideo:pending time:pendingTime duration:CMTimeMake(1, fps) error:&error])
        {
            [self recordFailure:error ?: recording_error(RecorderFailure, @"recording video append failed")];
            break;
        }
        CVPixelBufferRelease(pending);
        pending = CVPixelBufferRetain(latest);
        pendingTime = nextTime;
        nextTime = CMTimeAdd(nextTime, CMTimeMake(1, fps));
    }
    if (isFinal && ![self currentFailure] && CMTimeCompare(pendingTime, time) < 0)
    {
        NSError *error = nil;
        if (![writer appendVideo:pending time:pendingTime duration:CMTimeSubtract(time, pendingTime) error:&error])
        {
            [self recordFailure:error ?: recording_error(RecorderFailure, @"recording final frame failed")];
        }
    }
    if ([self currentFailure])
    {
        [self requestStopAt:recording_now() abort:NO];
    }
}

- (BOOL)waitReady:(NSTimeInterval)seconds
{
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:seconds];
    [condition lock];
    while (!ready && !failure)
    {
        if (![condition waitUntilDate:deadline])
        {
            failure = recording_error(RecorderTimeout, @"timed out waiting for the first complete recording frame");
            break;
        }
    }
    BOOL success = ready && !failure;
    [condition unlock];
    return success;
}

- (void)finishAfterStream:(NSError *)error
{
    // Only called on queue; both a timeout and a late stop completion may arrive.
    if (finalizing)
    {
        return;
    }
    finalizing = YES;
    [self recordFailure:error];
    output.owner = nil;
    output = nil;
    stream = nil;
    if (latest)
    {
        CVPixelBufferRelease(latest);
        latest = NULL;
    }
    if (pending)
    {
        CVPixelBufferRelease(pending);
        pending = NULL;
    }
    ScapkitRecordingWriter *closingWriter = writer;
    writer = nil;
    [condition lock];
    BOOL abort = aborting;
    CMTime end = CMTimeSubtract(cutoff, origin);
    [condition unlock];
    NSError *initialError = [self currentFailure];
    // VT completion may execute callbacks needed by the writer; never hold the
    // capture queue, an NSCondition, or Python thread state while draining it.
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        @autoreleasepool
        {
            NSError *finishError = initialError;
            NSDictionary *finished = nil;
            if (abort || finishError)
            {
                [closingWriter cancel];
                if (!finishError)
                {
                    finishError = recording_error(RecorderFailure, @"recording was aborted");
                }
            }
            else
            {
                finished = [closingWriter finishAt:CMTimeMaximum(end, CMTimeMake(1, NSEC_PER_SEC)) error:&finishError];
                if (!finished && !finishError)
                {
                    finishError = recording_error(RecorderFailure, @"recording writer did not produce a result");
                }
            }
            [self->condition lock];
            if (!self->failure)
            {
                self->failure = finishError;
            }
            self->result = finished;
            self->done = YES;
            [self->condition broadcast];
            [self->condition unlock];
        }
    });
}

- (void)requestStopAt:(CMTime)time abort:(BOOL)abort
{
    [condition lock];
    if (stopping || done)
    {
        [condition unlock];
        return;
    }
    stopping = YES;
    aborting = abort;
    cutoff = manualClock && !CMTIME_IS_NUMERIC(time) ? CMTimeAdd(CMTimeMake(10, 1), testTime) : time;
    [condition unlock];
    dispatch_async(queue, ^{
        @autoreleasepool
        {
            if (self->timer)
            {
                dispatch_source_cancel(self->timer);
                self->timer = nil;
            }
            if (!abort && CMTIME_IS_NUMERIC(self->origin))
            {
                [self advanceTo:CMTimeSubtract(self->cutoff, self->origin) final:YES];
            }
            SCStream *closingStream = self->stream;
            ScapkitRecordingStop *completion = [[ScapkitRecordingStop alloc] init];
            if (!completion)
            {
                // Even allocation failure must request physical stop. Outputs are
                // detached below before any possible late callback can use them.
                [closingStream stopCaptureWithCompletionHandler:^(NSError *stopError) { (void)closingStream; }];
                [self finishAfterStream:recording_error(RecorderFailure, @"could not allocate recording stop completion")];
                return;
            }
            completion.owner = self;
            void (^completed)(NSError *) = ^(NSError *stopError) {
                @autoreleasepool
                {
                    ScapkitRecorder *owner;
                    @synchronized(completion)
                    {
                        owner = completion.owner;
                        completion.owner = nil;
                    }
                    if (owner)
                    {
                        dispatch_async(owner->queue, ^{
                            @autoreleasepool
                            {
                                [owner finishAfterStream:stopError];
                            }
                        });
                    }
                }
            };
            NSTimeInterval stopTimeout = 5;
#ifdef SCAPKIT_TESTING
            if (self->synthetic && [self->testMode isEqualToString:@"stop_delayed"])
            {
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 150 * NSEC_PER_MSEC),
                               dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{ completed(nil); });
            }
            else if (self->synthetic && [self->testMode isEqualToString:@"stop_timeout"])
            {
                stopTimeout = 0.03;
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 100 * NSEC_PER_MSEC),
                               dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{ completed(nil); });
            }
            else if (self->synthetic && [self->testMode isEqualToString:@"stop_error"])
            {
                completed(recording_error(RecorderFailure, @"synthetic stream stop error"));
            }
            else
#endif
            if (closingStream)
            {
                [closingStream stopCaptureWithCompletionHandler:^(NSError *stopError) {
                    // Retain the stream through completion, but not a raw handle.
                    (void)closingStream;
                    completed(stopError);
                }];
            }
            else
            {
                completed(nil);
            }
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(stopTimeout * NSEC_PER_SEC)), self->queue, ^{
                @autoreleasepool
                {
                    completed(recording_error(RecorderTimeout, @"recording stream stop timed out"));
                }
            });
        }
    });
}

- (NSDictionary *)waitFinished
{
    NSTimeInterval timeout = 20;
#ifdef SCAPKIT_TESTING
    if ([testMode isEqualToString:@"publish_handoff_timeout"])
    {
        // This test orders publication before the short handoff timeout; codec
        // speed must not accidentally turn it into a pre-publication timeout.
        dispatch_semaphore_t published = [publicationWriter valueForKey:@"published"];
        [condition lock];
        BOOL completed = done;
        [condition unlock];
        if (!completed)
        {
            dispatch_semaphore_wait(published, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
        }
    }
    if ([testMode isEqualToString:@"finish_timeout"] || [testMode isEqualToString:@"publish_handoff_timeout"])
    {
        timeout = 0.03;
    }
#endif
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:timeout];
    [condition lock];
    while (!done)
    {
        if (![condition waitUntilDate:deadline])
        {
            [condition unlock];
            // Atomically either close publication or adopt an already committed
            // file. The finish worker retains its resources until cleanup ends.
            NSDictionary *published = [publicationWriter preventPublication];
            [condition lock];
            if (!done && published)
            {
                result = published;
                done = YES;
                [condition broadcast];
            }
            else if (!done && !failure)
            {
                failure = recording_error(RecorderTimeout, @"recording file finalization timed out");
            }
            break;
        }
    }
    NSDictionary *finished = result;
    [condition unlock];
    return finished;
}
@end

// Callback result objects outlive synchronous lookup/start timeouts.
@interface ScapkitRecordingCompletion : NSObject
@property(nonatomic, strong) dispatch_semaphore_t semaphore;
@property(nonatomic, strong) NSError *error;
@property(nonatomic, strong) SCDisplay *display;
@property(nonatomic) BOOL abandoned;
@end
@implementation ScapkitRecordingCompletion
- (instancetype)init
{
    self = [super init];
    if (self)
    {
        _semaphore = dispatch_semaphore_create(0);
        if (!_semaphore)
        {
            return nil;
        }
    }
    return self;
}
@end

static void recording_raise(NSError *error)
{
    PyObject *exception = PyExc_OSError;
    if ([error.domain isEqualToString:RecorderErrorDomain])
    {
        if (error.code == RecorderInvalid)
        {
            exception = PyExc_ValueError;
        }
        else if (error.code == RecorderTimeout)
        {
            exception = PyExc_TimeoutError;
        }
    }
    else if ([error.domain isEqualToString:NSPOSIXErrorDomain] && error.code == EEXIST)
    {
        exception = PyExc_FileExistsError;
    }
    PyErr_SetString(exception, error.localizedDescription.UTF8String ?: "recording failed");
}

static BOOL recording_integer(PyObject *value, long long maximum, const char *name, long long *result)
{
    if (!PyLong_Check(value) || PyBool_Check(value))
    {
        PyErr_Format(PyExc_TypeError, "%s must be an integer", name);
        return NO;
    }
    int overflow = 0;
    long long converted = PyLong_AsLongLongAndOverflow(value, &overflow);
    if (PyErr_Occurred())
    {
        return NO;
    }
    if (overflow || converted <= 0 || converted > maximum)
    {
        PyErr_Format(PyExc_ValueError, "%s must be in 1..%lld", name, maximum);
        return NO;
    }
    *result = converted;
    return YES;
}

static BOOL recording_quality(PyObject *value, double *result)
{
    if (value == Py_None)
    {
        *result = -1;
        return YES;
    }
    if (PyBool_Check(value) || (!PyFloat_Check(value) && !PyLong_Check(value)))
    {
        PyErr_SetString(PyExc_TypeError, "video_quality must be a number");
        return NO;
    }
    double quality = PyFloat_AsDouble(value);
    if (PyErr_Occurred())
    {
        return NO;
    }
    if (!isfinite(quality) || quality < 0 || quality > 1)
    {
        PyErr_SetString(PyExc_ValueError, "video_quality must be finite and between 0.0 and 1.0");
        return NO;
    }
    *result = quality;
    return YES;
}

static NSString *recording_path(PyObject *value)
{
    if (!PyUnicode_Check(value))
    {
        PyErr_SetString(PyExc_TypeError, "output_path must be a string");
        return nil;
    }
    Py_ssize_t size;
    const char *bytes = PyUnicode_AsUTF8AndSize(value, &size);
    if (!bytes)
    {
        return nil;
    }
    if (!size || memchr(bytes, '\0', (size_t)size))
    {
        PyErr_SetString(PyExc_ValueError, "output_path must not be empty or contain NUL");
        return nil;
    }
    NSString *path = [[NSString alloc] initWithBytes:bytes length:(NSUInteger)size encoding:NSUTF8StringEncoding];
    if (!path)
    {
        PyErr_NoMemory();
        return nil;
    }
    if (!path.isAbsolutePath)
    {
        path = [NSFileManager.defaultManager.currentDirectoryPath stringByAppendingPathComponent:path];
    }
    path = path.stringByStandardizingPath;
    struct stat info;
    if (lstat(path.fileSystemRepresentation, &info) == 0)
    {
        PyErr_SetString(PyExc_FileExistsError, "recording output already exists");
        return nil;
    }
    if (errno != ENOENT)
    {
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, value);
        return nil;
    }
    if (stat(path.stringByDeletingLastPathComponent.fileSystemRepresentation, &info) != 0)
    {
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, value);
        return nil;
    }
    if (!S_ISDIR(info.st_mode))
    {
        PyErr_SetString(PyExc_NotADirectoryError, "recording output parent is not a directory");
        return nil;
    }
    return path;
}

static ScapkitRecorder *start_native_recording(CGDirectDisplayID displayID, NSString *path, int fps, double videoQuality, NSError **error)
{
    ScapkitRecordingCompletion *lookup = [[ScapkitRecordingCompletion alloc] init];
    if (!lookup)
    {
        *error = recording_error(RecorderFailure, @"could not allocate recording display lookup");
        return nil;
    }
    [SCShareableContent getShareableContentExcludingDesktopWindows:NO onScreenWindowsOnly:NO
                                              completionHandler:^(SCShareableContent *content, NSError *lookupError) {
        @autoreleasepool
        {
            @synchronized(lookup)
            {
                lookup.error = lookupError;
                for (SCDisplay *display in content.displays)
                {
                    if (display.displayID == displayID)
                    {
                        lookup.display = display;
                        break;
                    }
                }
            }
            dispatch_semaphore_signal(lookup.semaphore);
        }
    }];
    if (dispatch_semaphore_wait(lookup.semaphore, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)))
    {
        *error = recording_error(RecorderTimeout, @"recording display lookup timed out");
        return nil;
    }
    SCDisplay *display;
    @synchronized(lookup)
    {
        *error = lookup.error;
        display = lookup.display;
    }
    if (*error)
    {
        return nil;
    }
    if (!display)
    {
        *error = recording_error(RecorderInvalid, @"recording display_id was not found");
        return nil;
    }
    CGDisplayModeRef mode = CGDisplayCopyDisplayMode(displayID);
    if (!mode)
    {
        *error = recording_error(RecorderFailure, @"could not read recording display dimensions");
        return nil;
    }
    size_t width = CGDisplayModeGetPixelWidth(mode);
    size_t height = CGDisplayModeGetPixelHeight(mode);
    CGDisplayModeRelease(mode);
    if (!width || !height || width >= INT_MAX || height >= INT_MAX)
    {
        *error = recording_error(RecorderFailure, @"invalid recording display dimensions");
        return nil;
    }
    int encodedWidth = (int)((width + 1) & ~(size_t)1);
    int encodedHeight = (int)((height + 1) & ~(size_t)1);
    ScapkitRecordingWriter *writer = [[ScapkitRecordingWriter alloc] initWithPath:path width:encodedWidth
                                                                        height:encodedHeight fps:fps videoQuality:videoQuality error:error];
    if (!writer)
    {
        return nil;
    }
    ScapkitRecorder *owner = [[ScapkitRecorder alloc] initWithWriter:writer fps:fps];
    SCStreamConfiguration *config = [[SCStreamConfiguration alloc] init];
    SCContentFilter *filter = [[SCContentFilter alloc] initWithDisplay:display excludingApplications:@[] exceptingWindows:@[]];
    if (!owner || !config || !filter)
    {
        [writer cancel];
        *error = recording_error(RecorderFailure, @"could not allocate recording capture objects");
        return nil;
    }
    config.width = encodedWidth;
    config.height = encodedHeight;
    config.destinationRect = CGRectMake(0, 0, width, height);
    config.pixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange;
    config.colorSpaceName = kCGColorSpaceITUR_709;
    config.colorMatrix = kCGDisplayStreamYCbCrMatrix_ITU_R_709_2;
    config.minimumFrameInterval = CMTimeMake(1, fps);
    config.queueDepth = 5;
    config.showsCursor = YES;
    config.capturesAudio = YES;
    config.excludesCurrentProcessAudio = NO;
    config.sampleRate = 48000;
    config.channelCount = 2;
    if (@available(macOS 15.0, *))
    {
        config.captureMicrophone = NO;
        config.captureDynamicRange = SCCaptureDynamicRangeSDR;
    }
    SCStream *captureStream = [[SCStream alloc] initWithFilter:filter configuration:config delegate:owner->output];
    owner->stream = captureStream;
    BOOL added = captureStream && [captureStream addStreamOutput:owner->output type:SCStreamOutputTypeScreen
                                             sampleHandlerQueue:owner->queue error:error];
    if (added)
    {
        added = [captureStream addStreamOutput:owner->output type:SCStreamOutputTypeAudio
                            sampleHandlerQueue:owner->queue error:error];
    }
    ScapkitRecordingCompletion *started = [[ScapkitRecordingCompletion alloc] init];
    if (!added || !started)
    {
        *error = *error ?: recording_error(RecorderFailure, @"could not initialize recording stream outputs");
        [owner recordFailure:*error];
        [owner requestStopAt:recording_now() abort:YES];
        [owner waitFinished];
        return nil;
    }
    [captureStream startCaptureWithCompletionHandler:^(NSError *startError) {
        @autoreleasepool
        {
            @synchronized(started)
            {
                started.error = startError;
                if (started.abandoned)
                {
                    [captureStream stopCaptureWithCompletionHandler:^(NSError *stopError) { (void)captureStream; }];
                }
            }
            dispatch_semaphore_signal(started.semaphore);
        }
    }];
    if (dispatch_semaphore_wait(started.semaphore, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)))
    {
        @synchronized(started)
        {
            started.abandoned = YES;
        }
        *error = recording_error(RecorderTimeout, @"recording stream start timed out");
    }
    else
    {
        @synchronized(started)
        {
            *error = started.error;
        }
    }
    if (*error || ![owner waitReady:5])
    {
        *error = *error ?: [owner currentFailure];
        [owner recordFailure:*error];
        [owner requestStopAt:recording_now() abort:YES];
        [owner waitFinished];
        return nil;
    }
    return owner;
}

static void recording_capsule_destructor(PyObject *capsule)
{
    @autoreleasepool
    {
        void *pointer = PyCapsule_GetPointer(capsule, "scapkit_recording");
        if (!pointer)
        {
            PyErr_WriteUnraisable(capsule);
            return;
        }
        ScapkitRecorder *owner = (__bridge_transfer ScapkitRecorder *)pointer;
        Py_BEGIN_ALLOW_THREADS
        [owner requestStopAt:owner->manualClock ? kCMTimeInvalid : recording_now() abort:YES];
        Py_END_ALLOW_THREADS
    }
}

static PyObject *recording_capsule(ScapkitRecorder *owner)
{
    void *pointer = (__bridge_retained void *)owner;
    PyObject *capsule = PyCapsule_New(pointer, "scapkit_recording", recording_capsule_destructor);
    if (!capsule)
    {
        ScapkitRecorder *unclaimed = (__bridge_transfer ScapkitRecorder *)pointer;
        Py_BEGIN_ALLOW_THREADS
        [unclaimed requestStopAt:unclaimed->manualClock ? kCMTimeInvalid : recording_now() abort:YES];
        Py_END_ALLOW_THREADS
    }
    return capsule;
}

PyObject *scapkit_start_recording(PyObject *self, PyObject *args)
{
    PyObject *displayArg, *pathArg, *fpsArg, *qualityArg = NULL;
    if (!PyArg_ParseTuple(args, "OOO|O:start_recording", &displayArg, &pathArg, &fpsArg, &qualityArg))
    {
        return NULL;
    }
    long long display, fps;
    double videoQuality = 0.75;
    if (!recording_integer(displayArg, UINT32_MAX, "display_id", &display) ||
        !recording_integer(fpsArg, INT32_MAX, "fps", &fps))
    {
        return NULL;
    }
    if (qualityArg && !recording_quality(qualityArg, &videoQuality))
    {
        return NULL;
    }
    @autoreleasepool
    {
        NSString *path = recording_path(pathArg);
        if (!path)
        {
            return NULL;
        }
        NSError *error = nil;
        ScapkitRecorder *owner;
        Py_BEGIN_ALLOW_THREADS
        owner = start_native_recording((CGDirectDisplayID)display, path, (int)fps, videoQuality, &error);
        Py_END_ALLOW_THREADS
        if (!owner)
        {
            recording_raise(error);
            return NULL;
        }
        return recording_capsule(owner);
    }
}

static PyObject *recording_result(ScapkitRecorder *owner)
{
    NSDictionary *value;
    NSError *error;
    Py_BEGIN_ALLOW_THREADS
    value = [owner waitFinished];
    error = [owner currentFailure];
    Py_END_ALLOW_THREADS
    if (error || !value)
    {
        recording_raise(error);
        return NULL;
    }
    return Py_BuildValue("{s:s,s:L,s:d,s:i,s:i,s:i}",
                         "path", [value[@"path"] UTF8String],
                         "size_bytes", [value[@"size_bytes"] longLongValue],
                         "duration_s", [value[@"duration_s"] doubleValue],
                         "width", [value[@"width"] intValue], "height", [value[@"height"] intValue],
                         "fps", [value[@"fps"] intValue]);
}

PyObject *scapkit_stop_recording(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "O:stop_recording", &capsule))
    {
        return NULL;
    }
    ScapkitRecorder *owner = (__bridge ScapkitRecorder *)PyCapsule_GetPointer(capsule, "scapkit_recording");
    if (!owner)
    {
        return NULL;
    }
    @autoreleasepool
    {
        Py_BEGIN_ALLOW_THREADS
        [owner requestStopAt:owner->manualClock ? kCMTimeInvalid : recording_now() abort:NO];
        Py_END_ALLOW_THREADS
        return recording_result(owner);
    }
}

PyObject *scapkit_abort_recording(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "O:_abort_recording", &capsule))
    {
        return NULL;
    }
    ScapkitRecorder *owner = (__bridge ScapkitRecorder *)PyCapsule_GetPointer(capsule, "scapkit_recording");
    if (!owner)
    {
        return NULL;
    }
    @autoreleasepool
    {
        Py_BEGIN_ALLOW_THREADS
        [owner requestStopAt:owner->manualClock ? kCMTimeInvalid : recording_now() abort:YES];
        [owner waitFinished];
        Py_END_ALLOW_THREADS
        Py_RETURN_NONE;
    }
}

#ifdef SCAPKIT_TESTING
#include "../../../tests/native_recording_helpers.h"
#endif
