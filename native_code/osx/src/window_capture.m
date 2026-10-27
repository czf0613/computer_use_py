#import "window_internal.h"
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>
#import <ImageIO/ImageIO.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#include <mach/mach_time.h>
#include <math.h>

@interface SWContentResult : NSObject
@property dispatch_semaphore_t ready;
@property SCShareableContent *content;
@property NSError *error;
@end
@implementation SWContentResult
@end

static SWContentResult *shareable_content(void)
{
    // A command-line Python process may not yet have a WindowServer connection.
    // Initialize it through a public read-only CoreGraphics query before SCK.
    if (CGMainDisplayID() == kCGNullDirectDisplay) { return nil; }
    SWContentResult *result = [SWContentResult new];
    result.ready = dispatch_semaphore_create(0);
    [SCShareableContent getShareableContentExcludingDesktopWindows:YES onScreenWindowsOnly:NO
        completionHandler:^(SCShareableContent *content, NSError *error) {
            @autoreleasepool {
                @synchronized (result) { result.content = content; result.error = error; }
                dispatch_semaphore_signal(result.ready);
            }
        }];
    double wait = MIN(2, SWRemaining() * 0.8);
    if (wait <= 0 || dispatch_semaphore_wait(result.ready, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(wait * NSEC_PER_SEC)))) { return nil; }
    return result;
}
NSDictionary *SWCaptureEnumerate(pid_t pid, NSUInteger limit)
{
    if (!CGPreflightScreenCaptureAccess()) { return SWFail(@"permission_denied"); }
    SWContentResult *result = shareable_content();
    if (!result) { return SWFail(@"capture_timeout"); }
    SCShareableContent *content;
    NSError *error;
    @synchronized (result) { content = result.content; error = result.error; }
    if (!content) { return SWError(@"capture_unavailable", @"ScreenCaptureKit", error ? @(error.code) : nil, @"not_dispatched", @"never"); }
    NSMutableArray *windows = [NSMutableArray array];
    for (SCWindow *w in content.windows) {
        pid_t owner = w.owningApplication.processID;
        if (owner <= 0 || (pid && owner != pid) || w.windowLayer != 0 || !SWGoodRect(w.frame)) { continue; }
        if (windows.count == limit) { return SWFail(@"resource_limit"); }
        NSString *process = SWProcess(owner);
        if (!process) { return SWFail(@"identity_unverifiable"); }
        SWTarget *t = [SWTarget new];
        t->window = w; t->pid = owner; t->process = process; t->bounds = w.frame;
        t->minimized = NSNull.null; // off-screen is not equivalent to minimized
        // Keep this SCWindow for the whole incarnation. Read-only capture does
        // not request AX, inspect other applications or require a fixture/profile.
        SWReference *ref = [SWReference new]; ref->target = t; ref->capture = YES;
        [windows addObject:SWInfo(ref, SWString(w.title), [NSString stringWithFormat:@"%u", w.windowID], @(w.isOnScreen))];
        if (SWDeadlineExpired()) { return SWFail(@"backend_busy"); }
    }
    return SWOK(windows);
}
BOOL SWCaptureRefresh(SWTarget *t)
{
    if (t->lifetime && SWValidateTarget(t->lifetime)) { return NO; }
    if (t->lifetime) {
        t->minimized = t->lifetime->minimized;
        // Some SCK versions omit minimized windows from shareable content.
        // An explicitly attached, live AX witness distinguishes this from close.
        if ([t->minimized isEqual:@YES]) { return YES; }
    }
    SWContentResult *result = shareable_content();
    SCShareableContent *content;
    @synchronized (result) { content = result.content; }
    if (!content) { return NO; } // loss of monitoring invalidates permanently
    NSUInteger matches = 0;
    CGRect bounds = CGRectNull;
    for (SCWindow *live in content.windows) {
        if (live.windowID == t->window.windowID) {
            if (live.owningApplication.processID != t->pid || !SWGoodRect(live.frame)) { return NO; }
            ++matches; bounds = live.frame;
        }
    }
    if (matches != 1 || ![SWProcess(t->pid) isEqual:t->process]) { return NO; }
    if (!CGRectEqualToRect(t->bounds, bounds)) { ++t->epoch; t->bounds = bounds; }
    if (t->lifetime) { t->minimized = t->lifetime->minimized; }
    // Crucially do not assign the fresh SCWindow to t->window. Observable
    // disappearance/stop errors poison this target; unseen ABA cannot be excluded.
    return YES;
}

NSDictionary *SWMapping(NSDictionary *m, size_t width, size_t height, CGRect bounds)
{
    NSDictionary *unavailable = @{@"local_rect": NSNull.null, @"image_content_rect_px": NSNull.null,
        @"image_to_local": NSNull.null, @"mapping_status": @"unavailable", @"client_rect_local": NSNull.null};
    CGRect content;
    id rect = m[SCStreamFrameInfoContentRect], scale = m[SCStreamFrameInfoScaleFactor], fit = m[SCStreamFrameInfoContentScale];
    if (![rect isKindOfClass:NSDictionary.class] || !CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)rect, &content) ||
        ![scale isKindOfClass:NSNumber.class] || ![fit isKindOfClass:NSNumber.class]) { return unavailable; }
    double pixelScale = [scale doubleValue], contentScale = [fit doubleValue];
    if (!SWGoodRect(content) || !isfinite(pixelScale) || pixelScale < 1 || pixelScale > 4 ||
        !isfinite(contentScale) || contentScale <= 0 || contentScale > 16) { return unavailable; }
    // SDK contentRect is in surface POINTS. scaleFactor and contentScale are
    // copied from this exact CMSampleBuffer, never the enumeration display.
    CGRect pixels = CGRectMake(content.origin.x * pixelScale, content.origin.y * pixelScale,
                               content.size.width * pixelScale, content.size.height * pixelScale);
    CGRect local = CGRectMake(0, 0, content.size.width / contentScale, content.size.height / contentScale);
    if (!SWGoodRect(pixels) || !SWGoodRect(local) || !CGRectContainsRect(CGRectMake(0, 0, width, height), pixels) ||
        fabs(local.size.width - bounds.size.width) > 0.01 || fabs(local.size.height - bounds.size.height) > 0.01) { return unavailable; }
    double sx = local.size.width / pixels.size.width, sy = local.size.height / pixels.size.height;
    return @{@"local_rect": SWRect(local), @"image_content_rect_px": SWRect(pixels),
        @"image_to_local": @[@(sx), @0.0, @0.0, @(sy), @(-pixels.origin.x * sx), @(-pixels.origin.y * sy)],
        @"mapping_status": @"verified", @"client_rect_local": NSNull.null};
}

// One capture may be cleaning up after a late completion. Refuse another stream
// until it finishes; never accumulate timed-out streams or replacement threads.
static atomic_bool captureBusy;
@interface SWShot : NSObject <SCStreamOutput, SCStreamDelegate> {
@public
    dispatch_queue_t queue;
    dispatch_semaphore_t frameReady, started, stopped;
    SCStream *stream;
    NSData *pixels;
    NSDictionary *metadata;
    NSError *startError, *stopError, *streamError;
    size_t width, height, stride;
    double captured, since;
    BOOL abandoned, finished, received, stopIssued, startCompleted, stopCompleted;
    SWShot *cleanupOwner; // bounded self-owner until stop AND output removal succeed
}
- (void)start;
- (void)abandon;
- (void)stop;
@end
@implementation SWShot
- (instancetype)init
{
    self = [super init];
    if (self) {
        queue = dispatch_queue_create("scapkit.window.frame", DISPATCH_QUEUE_SERIAL);
        frameReady = dispatch_semaphore_create(0);
        started = dispatch_semaphore_create(0);
        stopped = dispatch_semaphore_create(0);
    }
    return self;
}
- (void)start
{
    // Retain native ownership even if the caller/capsule disappears and SCK
    // completes late or fails teardown. At most one such flight holds the gate.
    dispatch_sync(queue, ^{ self->cleanupOwner = self; });
    [stream startCaptureWithCompletionHandler:^(NSError *error) {
        @autoreleasepool {
            dispatch_async(self->queue, ^{
                self->startCompleted = YES;
                self->startError = error;
                if (self->abandoned) { [self stop]; }
                dispatch_semaphore_signal(self->started);
            });
        }
    }];
}
- (void)abandon
{
    abandoned = YES;
    // Covers both orders of the start-completion/timeout race.
    if (startCompleted) { [self stop]; }
}
- (void)stream:(SCStream *)source didStopWithError:(NSError *)error
{
    (void)source;
    @autoreleasepool {
        // Delegate callback queue is not documented as the sample queue.
        dispatch_async(queue, ^{ self->streamError = error; dispatch_semaphore_signal(self->frameReady); });
    }
}
- (void)stream:(SCStream *)source didOutputSampleBuffer:(CMSampleBufferRef)sample ofType:(SCStreamOutputType)type
{
    (void)source;
    @autoreleasepool {
        if (abandoned || finished || received || type != SCStreamOutputTypeScreen || !CMSampleBufferIsValid(sample)) { return; }
        CFArrayRef attachments = CMSampleBufferGetSampleAttachmentsArray(sample, false);
        if (!attachments || CFArrayGetCount(attachments) < 1) { return; }
        NSDictionary *info = (__bridge NSDictionary *)CFArrayGetValueAtIndex(attachments, 0);
        if (![info[SCStreamFrameInfoStatus] isKindOfClass:NSNumber.class] || [info[SCStreamFrameInfoStatus] integerValue] != SCFrameStatusComplete) { return; }
        NSNumber *tick = info[SCStreamFrameInfoDisplayTime];
        if (![tick isKindOfClass:NSNumber.class]) { return; }
        mach_timebase_info_data_t timebase;
        mach_timebase_info(&timebase);
        double time = (double)tick.unsignedLongLongValue * timebase.numer / timebase.denom / 1e9;
        if (!isfinite(time) || time < since || time > SWNow() + 0.001) { return; }
        CVPixelBufferRef frame = CMSampleBufferGetImageBuffer(sample);
        if (!frame || CVPixelBufferGetPixelFormatType(frame) != kCVPixelFormatType_32BGRA || CVPixelBufferIsPlanar(frame)) { return; }
        size_t w = CVPixelBufferGetWidth(frame), h = CVPixelBufferGetHeight(frame), row = CVPixelBufferGetBytesPerRow(frame);
        if (!w || !h || w > 16384 || h > 16384 || row < w * 4 || row > 128 * 1024 * 1024 / h) { return; }
        if (CVPixelBufferLockBaseAddress(frame, kCVPixelBufferLock_ReadOnly) != kCVReturnSuccess) { return; }
        void *base = CVPixelBufferGetBaseAddress(frame);
        NSData *copy = base ? [NSData dataWithBytes:base length:row * h] : nil;
        CVReturn unlocked = CVPixelBufferUnlockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
        if (!copy || unlocked != kCVReturnSuccess) { return; }
        width = w; height = h; stride = row; pixels = copy; metadata = [info copy]; captured = time;
        received = YES;
        dispatch_semaphore_signal(frameReady);
    }
}
- (void)stop
{
    // queue only; callbacks retain self and its stream until teardown completes.
    abandoned = YES;
    if (stopIssued) { return; }
    stopIssued = YES;
    SCStream *owned = stream;
    [owned stopCaptureWithCompletionHandler:^(NSError *error) {
        @autoreleasepool {
            dispatch_async(self->queue, ^{
                self->stopError = error;
                NSError *removeError = nil;
                BOOL detached = [owned removeStreamOutput:self type:SCStreamOutputTypeScreen error:&removeError];
                if (!detached && !self->stopError) {
                    self->stopError = removeError;
                }
                self->stopCompleted = YES; // a callback is not proof of cleanup
                self->finished = !error && detached;
                if (self->finished) {
                    self->stream = nil;
                    self->cleanupOwner = nil;
                    atomic_store(&captureBusy, false);
                }
                // Failure retains stream/owner and the global gate. Do not
                // automatically retry teardown or admit replacement streams.
                dispatch_semaphore_signal(self->stopped);
            });
        }
    }];
}
@end

static NSData *jpeg(NSData *pixels, size_t width, size_t height, size_t stride, int quality)
{
    CGDataProviderRef provider = CGDataProviderCreateWithCFData((__bridge CFDataRef)pixels);
    CGColorSpaceRef color = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGImageRef image = provider && color ? CGImageCreate(width, height, 8, 32, stride, color,
        kCGBitmapByteOrder32Little | kCGImageAlphaNoneSkipFirst, provider, NULL, false, kCGRenderingIntentDefault) : NULL;
    NSMutableData *data = [NSMutableData data];
    CGImageDestinationRef dest = image ? CGImageDestinationCreateWithData((__bridge CFMutableDataRef)data, (__bridge CFStringRef)UTTypeJPEG.identifier, 1, NULL) : NULL;
    BOOL ok = NO;
    if (dest) {
        CGImageDestinationAddImage(dest, image, (__bridge CFDictionaryRef)@{(__bridge NSString *)kCGImageDestinationLossyCompressionQuality: @((double)quality / 100)});
        ok = CGImageDestinationFinalize(dest);
        CFRelease(dest);
    }
    if (image) { CGImageRelease(image); }
    if (color) { CGColorSpaceRelease(color); }
    if (provider) { CGDataProviderRelease(provider); }
    return ok && data.length <= 128 * 1024 * 1024 ? [data copy] : nil;
}
static BOOL wait_until(dispatch_semaphore_t semaphore, double deadline)
{
    double left = deadline - SWNow();
    return left > 0 && dispatch_semaphore_wait(semaphore, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(left * NSEC_PER_SEC))) == 0;
}
BOOL SWCaptureCleanup(SWSession *s, double timeout)
{
    SWShot *shot = s->captureFlight;
    if (!shot) { return YES; }
    __block BOOL finished, completed;
    dispatch_sync(shot->queue, ^{ [shot abandon]; finished = shot->finished; completed = shot->stopCompleted; });
    if (!completed && timeout > 0) { wait_until(shot->stopped, SWNow() + timeout); }
    dispatch_sync(shot->queue, ^{ finished = shot->finished; });
    if (finished) { s->captureFlight = nil; }
    return finished;
}
NSDictionary *SWCapture(SWSession *s, NSString *format, int quality, double timeout)
{
    NSDictionary *failure = SWValidate(s);
    if (failure) { return failure; }
    if (!s->captureTarget) { return SWFail(@"unsupported_background_operation"); }
    SWTarget *t = s->captureTarget;
    if ([t->minimized isEqual:@YES]) { return SWFail(@"window_minimized"); }
    if (!CGPreflightScreenCaptureAccess()) { return SWFail(@"permission_denied"); }
    BOOL expected = false;
    if (!atomic_compare_exchange_strong(&captureBusy, &expected, true)) { return SWFail(@"backend_busy"); }
    timeout = MIN(timeout, SWRemaining());
    double began = SWNow(), frameDeadline = began + timeout * 0.70, stopDeadline = began + timeout * 0.95;
    CGRect before = t->bounds;
    uint64_t revision = s->revision, epoch = t->epoch;
    SCContentFilter *filter = [[SCContentFilter alloc] initWithDesktopIndependentWindow:t->window];
    SCStreamConfiguration *config = [SCStreamConfiguration new];
    double scale = 2;
    if (@available(macOS 14.0, *)) { scale = filter.pointPixelScale; config.ignoreShadowsSingleWindow = YES; }
    if (@available(macOS 14.2, *)) { config.includeChildWindows = NO; }
    if (@available(macOS 15.0, *)) { config.captureMicrophone = NO; }
    if (!isfinite(scale) || scale < 1 || scale > 4) { scale = 1; }
    double w = ceil(before.size.width * scale), h = ceil(before.size.height * scale);
    if (w < 1 || h < 1 || w > 16384 || h > 16384 || w * h * 4 > 128 * 1024 * 1024) {
        atomic_store(&captureBusy, false); return SWFail(@"resource_limit");
    }
    config.width = (size_t)w; config.height = (size_t)h;
    config.pixelFormat = kCVPixelFormatType_32BGRA;
    config.showsCursor = NO; config.capturesAudio = NO;
    config.queueDepth = 3; config.minimumFrameInterval = CMTimeMake(1, 30);
    SWShot *shot = [SWShot new]; shot->since = began;
    shot->stream = [[SCStream alloc] initWithFilter:filter configuration:config delegate:shot];
    NSError *error = nil;
    if (!shot->stream || ![shot->stream addStreamOutput:shot type:SCStreamOutputTypeScreen sampleHandlerQueue:shot->queue error:&error]) {
        shot->stream = nil; atomic_store(&captureBusy, false);
        return SWError(@"capture_unavailable", @"ScreenCaptureKit", error ? @(error.code) : nil, @"not_dispatched", @"never");
    }
    s->captureFlight = shot;
    [shot start];
    BOOL started = wait_until(shot->started, frameDeadline);
    if (!started) {
        // A late successful start must stop itself. No borrowed session/capsule.
        dispatch_sync(shot->queue, ^{ [shot abandon]; });
        wait_until(shot->stopped, stopDeadline);
        BOOL cleaned = SWCaptureCleanup(s, 0);
        return SWFail(cleaned ? @"capture_timeout" : @"backend_busy");
    }
    __block NSError *startError;
    dispatch_sync(shot->queue, ^{ startError = shot->startError; });
    if (!startError) { wait_until(shot->frameReady, frameDeadline); }
    __block NSData *pixels;
    __block NSDictionary *metadata;
    __block size_t width, height, stride;
    __block double captured;
    __block NSError *streamError;
    dispatch_sync(shot->queue, ^{
        pixels = shot->pixels; metadata = shot->metadata;
        width = shot->width; height = shot->height; stride = shot->stride; captured = shot->captured;
        streamError = shot->streamError;
        [shot stop];
    });
    if (!wait_until(shot->stopped, stopDeadline)) { return SWFail(@"backend_busy"); }
    if (!SWCaptureCleanup(s, 0)) { t->stale = YES; return SWFail(@"backend_busy"); }
    __block NSError *stopError;
    dispatch_sync(shot->queue, ^{ stopError = shot->stopError; });
    if (stopError) { t->stale = YES; return SWError(@"capture_unavailable", @"ScreenCaptureKit", @(stopError.code), @"not_dispatched", @"never"); }
    failure = SWValidate(s);
    if (failure) { return failure; }
    if ([t->minimized isEqual:@YES]) { return SWFail(@"window_minimized"); }
    if (s->revision != revision || t->epoch != epoch || !CGRectEqualToRect(t->bounds, before)) { return SWFail(@"geometry_changed"); }
    if (startError || streamError) {
        // Treat any stop/start source failure as permanent loss of this retained
        // target, rather than silently rebuilding it from a possibly reused ID.
        t->stale = YES;
        return SWError(@"capture_unavailable", @"ScreenCaptureKit", @((startError ?: streamError).code), @"not_dispatched", @"never");
    }
    if (!pixels) { return SWFail(@"capture_timeout"); }
    if (@available(macOS 13.1, *)) {
        id screen = metadata[SCStreamFrameInfoScreenRect];
        CGRect actual;
        if ([screen isKindOfClass:NSDictionary.class] && CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)screen, &actual) &&
            !CGRectEqualToRect(actual, before)) { return SWFail(@"geometry_changed"); }
    }
    NSData *data = [format isEqual:@"jpeg"] ? jpeg(pixels, width, height, stride, quality) : pixels;
    if (!data) { return SWFail(@"capture_unavailable"); }
    NSMutableDictionary *frame = [SWIdentity(s) mutableCopy];
    [frame addEntriesFromDictionary:SWMapping(metadata, width, height, before)];
    [frame addEntriesFromDictionary:@{@"frame_id": NSUUID.UUID.UUIDString, @"captured_at": @(captured),
        @"state": @"complete", @"format": format, @"data": data, @"image_width": @(width), @"image_height": @(height),
        @"stride": [format isEqual:@"bgra"] ? @(stride) : (id)NSNull.null, @"window_bounds": SWRect(before), @"coordinate_unit": @"points"}];
    return SWOK(frame);
}

#ifdef SCAPKIT_TESTING
// Offline completion transport: exercises the production start/abandon/stop
// ordering and ownership, without constructing SCStream or touching a display.
@interface SWOfflineStream : NSObject
@property double delay;
@property BOOL detached;
@end
@implementation SWOfflineStream
- (void)startCaptureWithCompletionHandler:(void (^)(NSError *))completion
{
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(self.delay * NSEC_PER_SEC)), dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        @autoreleasepool { completion(nil); }
    });
}
- (void)stopCaptureWithCompletionHandler:(void (^)(NSError *))completion
{
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{ @autoreleasepool { completion(nil); } });
}
- (BOOL)removeStreamOutput:(id)output type:(SCStreamOutputType)type error:(NSError **)error
{
    (void)output; (void)type; (void)error; self.detached = YES; return YES;
}
@end
// Fault injection changes the transport's effects and completion results, while
// the production SWShot/SWCaptureCleanup/SWClose state machine remains in use.
@interface SWFaultStream : SWOfflineStream
@property BOOL failStop, failRemove, sourceRunning;
@property double stopDelay;
@property id outputOwner;
@end
@implementation SWFaultStream
- (void)startCaptureWithCompletionHandler:(void (^)(NSError *))completion
{
    [super startCaptureWithCompletionHandler:^(NSError *error) {
        self.sourceRunning = !error; completion(error);
    }];
}
- (void)stopCaptureWithCompletionHandler:(void (^)(NSError *))completion
{
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(self.stopDelay * NSEC_PER_SEC)), dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
        @autoreleasepool {
            if (!self.failStop) { self.sourceRunning = NO; }
            completion(self.failStop ? [NSError errorWithDomain:@"test.stop" code:1 userInfo:nil] : nil);
        }
    });
}
- (BOOL)removeStreamOutput:(id)output type:(SCStreamOutputType)type error:(NSError **)error
{
    if (self.failRemove) { if (error) { *error = [NSError errorWithDomain:@"test.remove" code:2 userInfo:nil]; } return NO; }
    self.outputOwner = nil;
    return [super removeStreamOutput:output type:type error:error];
}
@end
NSDictionary *SWTestFrame(NSString *mode)
{
    SWShot *shot = [SWShot new];
    if ([mode hasPrefix:@"probe_"]) {
        BOOL expected = false;
        if (!atomic_compare_exchange_strong(&captureBusy, &expected, true)) { return SWFail(@"backend_busy"); }
        SWFaultStream *transport = [SWFaultStream new]; transport.delay = 0.05;
        transport.failStop = [mode isEqual:@"probe_stop_fail"] || [mode isEqual:@"probe_both_fail"];
        transport.failRemove = [mode isEqual:@"probe_remove_fail"] || [mode isEqual:@"probe_both_fail"];
        transport.stopDelay = [mode isEqual:@"probe_late_success"] ? 0.05 : 0;
        transport.outputOwner = shot;
        shot->stream = (SCStream *)transport;
        SWSession *session = [SWSession new]; session->captureFlight = shot;
        [shot start];
        BOOL early = SWCaptureCleanup(session, 0.001);
        BOOL close = SWClose(session);
        BOOL retained = session->captureFlight != nil;
        BOOL repeated = SWClose(session);
        __block BOOL finished, released;
        dispatch_sync(shot->queue, ^{ finished = shot->finished; released = shot->stream == nil; });
        __weak SWShot *weakShot = shot;
        session = nil; shot = nil;
        // Probe runs in a disposable child process: failures intentionally keep
        // the native quarantine alive, without resetting production gate state.
        return SWOK(@{@"early_cleanup": @(early), @"close_cleanup": @(close), @"repeat_cleanup": @(repeated),
            @"finished": @(finished), @"stream_released": @(released), @"flight_retained": @(retained),
            @"capture_gate_busy": @(atomic_load(&captureBusy)), @"owner_alive": @(weakShot != nil),
            @"source_running": @(transport.sourceRunning), @"output_detached": @(transport.detached)});
    }
    if ([mode hasPrefix:@"close_"]) {
        SWOfflineStream *transport = [SWOfflineStream new]; transport.delay = 0.05;
        shot->stream = (SCStream *)transport;
        SWSession *session = [SWSession new]; session->captureFlight = shot;
        [shot start];
        BOOL early = [mode isEqual:@"close_incomplete"];
        BOOL first = early ? SWCaptureCleanup(session, 0.001) : SWClose(session);
        BOOL retained = session->captureFlight != nil;
        BOOL final = SWCaptureCleanup(session, 1);
        return SWOK(@{@"first_cleanup": @(first), @"retained_until_complete": @(retained),
            @"final_cleanup": @(final), @"stream_released": @(shot->stream == nil), @"detached": @(transport.detached)});
    }
    if ([mode hasPrefix:@"late_"]) {
        SWOfflineStream *transport = [SWOfflineStream new]; transport.delay = 0.01;
        shot->stream = (SCStream *)transport;
        [shot start];
        if ([mode isEqual:@"late_after"]) { wait_until(shot->started, SWNow() + 1); }
        dispatch_sync(shot->queue, ^{ [shot abandon]; });
        if (!wait_until(shot->stopped, SWNow() + 1)) { return SWFail(@"backend_busy"); }
        __block NSDictionary *state;
        dispatch_sync(shot->queue, ^{
            state = @{@"finished": @(shot->finished), @"detached": @(transport.detached), @"stream_released": @(shot->stream == nil)};
        });
        return SWOK(state);
    }
    CVPixelBufferRef frame = NULL;
    if (CVPixelBufferCreate(NULL, 8, 4, kCVPixelFormatType_32BGRA, NULL, &frame) != kCVReturnSuccess) { return SWFail(@"resource_limit"); }
    if (CVPixelBufferLockBaseAddress(frame, 0) != kCVReturnSuccess) { CFRelease(frame); return SWFail(@"capture_unavailable"); }
    memset(CVPixelBufferGetBaseAddress(frame), 0x7f, CVPixelBufferGetBytesPerRow(frame) * 4);
    CVPixelBufferUnlockBaseAddress(frame, 0);
    CMVideoFormatDescriptionRef description = NULL;
    CMVideoFormatDescriptionCreateForImageBuffer(NULL, frame, &description);
    CMSampleTimingInfo timing = {CMTimeMake(1, 30), CMTimeMake(1, 1), kCMTimeInvalid};
    CMSampleBufferRef sample = NULL;
    OSStatus status = description ? CMSampleBufferCreateReadyWithImageBuffer(NULL, frame, description, &timing, &sample) : -1;
    if (description) { CFRelease(description); }
    if (status != noErr || !sample) { CFRelease(frame); return SWFail(@"capture_unavailable"); }
    CFArrayRef attachments = CMSampleBufferGetSampleAttachmentsArray(sample, true);
    NSMutableDictionary *info = (__bridge NSMutableDictionary *)CFArrayGetValueAtIndex(attachments, 0);
    NSInteger state = SCFrameStatusComplete;
    if ([mode isEqual:@"idle"]) { state = SCFrameStatusIdle; }
    if ([mode isEqual:@"blank"]) { state = SCFrameStatusBlank; }
    if ([mode isEqual:@"suspended"]) { state = SCFrameStatusSuspended; }
    info[SCStreamFrameInfoStatus] = @(state);
    info[SCStreamFrameInfoDisplayTime] = @(mach_absolute_time());
    shot->since = [mode isEqual:@"old"] ? SWNow() + 1 : SWNow() - 1;
    SWOfflineStream *source = [SWOfflineStream new];
    dispatch_sync(shot->queue, ^{
        if ([mode isEqual:@"abandoned"]) { shot->abandoned = YES; }
        [shot stream:(SCStream *)source didOutputSampleBuffer:sample ofType:SCStreamOutputTypeScreen];
    });
    // Overwrite and release the original sample: result must be an owned copy.
    CVPixelBufferLockBaseAddress(frame, 0);
    memset(CVPixelBufferGetBaseAddress(frame), 0, CVPixelBufferGetBytesPerRow(frame) * 4);
    CVPixelBufferUnlockBaseAddress(frame, 0);
    CFRelease(sample); CFRelease(frame);
    __block NSData *pixels;
    __block BOOL received;
    dispatch_sync(shot->queue, ^{ pixels = shot->pixels; received = shot->received; });
    NSData *encoded = pixels ? jpeg(pixels, shot->width, shot->height, shot->stride, 80) : nil;
    return SWOK(@{@"received": @(received), @"pixels": pixels ?: (id)NSNull.null,
        @"jpeg": encoded ?: (id)NSNull.null, @"width": @(shot->width), @"height": @(shot->height), @"stride": @(shot->stride)});
}
#endif
