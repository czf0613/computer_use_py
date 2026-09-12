// Included only by recording.m in SCAPKIT_TESTING builds. No screen APIs here.

@interface ScapkitDelayedRecordingWriter : ScapkitRecordingWriter
@end
@implementation ScapkitDelayedRecordingWriter
- (NSDictionary *)finishAt:(CMTime)endTime error:(NSError **)error
{
    [NSThread sleepForTimeInterval:0.15];
    return [super finishAt:endTime error:error];
}
@end

@interface ScapkitPostPublishRecordingWriter : ScapkitRecordingWriter
@property(nonatomic, strong) dispatch_semaphore_t published;
@end
@implementation ScapkitPostPublishRecordingWriter
- (NSDictionary *)finishAt:(CMTime)endTime error:(NSError **)error
{
    NSDictionary *published = [super finishAt:endTime error:error];
    dispatch_semaphore_signal(self.published);
    [NSThread sleepForTimeInterval:0.15];
    return published;
}
@end

static CVPixelBufferRef recording_test_frame(void)
{
    CVPixelBufferRef frame = NULL;
    NSDictionary *attributes = @{(__bridge NSString *)kCVPixelBufferIOSurfacePropertiesKey: @{}};
    if (CVPixelBufferCreate(kCFAllocatorDefault, 64, 48, kCVPixelFormatType_32BGRA,
                            (__bridge CFDictionaryRef)attributes, &frame) != kCVReturnSuccess)
    {
        return NULL;
    }
    if (CVPixelBufferLockBaseAddress(frame, 0) != kCVReturnSuccess)
    {
        CVPixelBufferRelease(frame);
        return NULL;
    }
    unsigned char *bytes = CVPixelBufferGetBaseAddress(frame);
    size_t stride = CVPixelBufferGetBytesPerRow(frame);
    for (size_t y = 0; y < 48; ++y)
    {
        for (size_t x = 0; x < 64; ++x)
        {
            bytes[y * stride + x * 4] = 32;
            bytes[y * stride + x * 4 + 1] = 64;
            bytes[y * stride + x * 4 + 2] = 128;
            bytes[y * stride + x * 4 + 3] = 255;
        }
    }
    CVPixelBufferUnlockBaseAddress(frame, 0);
    return frame;
}

PyObject *scapkit_test_recording(PyObject *self, PyObject *args)
{
    PyObject *pathArg, *fpsArg, *qualityArg = Py_None;
    const char *mode;
    if (!PyArg_ParseTuple(args, "OOs|O:_test_recording", &pathArg, &fpsArg, &mode, &qualityArg))
    {
        return NULL;
    }
    long long fps;
    double videoQuality = -1;
    if (!recording_integer(fpsArg, INT32_MAX, "fps", &fps))
    {
        return NULL;
    }
    if (!recording_quality(qualityArg, &videoQuality))
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
        NSString *testMode = [NSString stringWithUTF8String:mode];
        if (![@[@"normal", @"realtime", @"first_frame_timeout", @"stream_error", @"stop_error", @"stop_timeout", @"stop_delayed", @"finish_timeout", @"publish_handoff_timeout"] containsObject:testMode])
        {
            PyErr_SetString(PyExc_ValueError, "invalid synthetic recording mode");
            return NULL;
        }
        NSError *error = nil;
        ScapkitRecorder *owner = nil;
        Py_BEGIN_ALLOW_THREADS
        Class writerClass = [testMode isEqualToString:@"finish_timeout"] ? [ScapkitDelayedRecordingWriter class] : [ScapkitRecordingWriter class];
        if ([testMode isEqualToString:@"publish_handoff_timeout"])
        {
            writerClass = [ScapkitPostPublishRecordingWriter class];
        }
        ScapkitRecordingWriter *writer = [[writerClass alloc] initWithPath:path width:64 height:48 fps:(int)fps videoQuality:videoQuality error:&error];
        if ([writer isKindOfClass:[ScapkitPostPublishRecordingWriter class]])
        {
            ((ScapkitPostPublishRecordingWriter *)writer).published = dispatch_semaphore_create(0);
        }
        if (writer)
        {
            owner = [[ScapkitRecorder alloc] initWithWriter:writer fps:(int)fps];
            if (!owner)
            {
                [writer cancel];
                error = recording_error(RecorderFailure, @"could not allocate synthetic recording");
            }
            else
            {
                owner->synthetic = YES;
                owner->manualClock = ![testMode isEqualToString:@"realtime"];
                owner->testMode = testMode;
                if (![testMode isEqualToString:@"first_frame_timeout"])
                {
                    CVPixelBufferRef frame = recording_test_frame();
                    if (frame)
                    {
                        dispatch_sync(owner->queue, ^{ [owner acceptFrame:frame]; });
                        CVPixelBufferRelease(frame);
                    }
                    else
                    {
                        [owner recordFailure:recording_error(RecorderFailure, @"could not allocate synthetic frame")];
                    }
                }
                if (![owner waitReady:0.03])
                {
                    error = [owner currentFailure];
                    [owner requestStopAt:kCMTimeInvalid abort:YES];
                    [owner waitFinished];
                    owner = nil;
                }
                else if ([testMode isEqualToString:@"stream_error"])
                {
                    [owner recordFailure:recording_error(RecorderFailure, @"synthetic stream failed during capture")];
                    [owner requestStopAt:kCMTimeInvalid abort:NO];
                }
            }
        }
        Py_END_ALLOW_THREADS
        if (!owner)
        {
            recording_raise(error);
            return NULL;
        }
        return recording_capsule(owner);
    }
}

static ScapkitRecorder *recording_test_timed_args(PyObject *args, long long *nanoseconds)
{
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "OL", &capsule, nanoseconds))
    {
        return nil;
    }
    if (*nanoseconds < 0)
    {
        PyErr_SetString(PyExc_ValueError, "synthetic time must be nonnegative");
        return nil;
    }
    ScapkitRecorder *owner = (__bridge ScapkitRecorder *)PyCapsule_GetPointer(capsule, "scapkit_recording");
    if (owner && !owner->manualClock)
    {
        PyErr_SetString(PyExc_ValueError, "test clock requires a synthetic recording");
        return nil;
    }
    return owner;
}

PyObject *scapkit_test_recording_tick(PyObject *self, PyObject *args)
{
    long long nanoseconds;
    ScapkitRecorder *owner = recording_test_timed_args(args, &nanoseconds);
    if (!owner)
    {
        return NULL;
    }
    @autoreleasepool
    {
        NSError *error;
        CMTime time = CMTimeMake(nanoseconds, NSEC_PER_SEC);
        Py_BEGIN_ALLOW_THREADS
        dispatch_sync(owner->queue, ^{
            [owner->condition lock];
            BOOL closed = owner->stopping;
            owner->testTime = CMTimeMaximum(time, owner->testTime);
            [owner->condition unlock];
            if (!closed)
            {
                [owner advanceTo:time final:NO];
            }
        });
        error = [owner currentFailure];
        Py_END_ALLOW_THREADS
        if (error)
        {
            recording_raise(error);
            return NULL;
        }
        Py_RETURN_NONE;
    }
}

PyObject *scapkit_test_stop_recording_at(PyObject *self, PyObject *args)
{
    long long nanoseconds;
    ScapkitRecorder *owner = recording_test_timed_args(args, &nanoseconds);
    if (!owner)
    {
        return NULL;
    }
    @autoreleasepool
    {
        Py_BEGIN_ALLOW_THREADS
        [owner requestStopAt:CMTimeAdd(CMTimeMake(10, 1), CMTimeMake(nanoseconds, NSEC_PER_SEC)) abort:NO];
        Py_END_ALLOW_THREADS
        return recording_result(owner);
    }
}

PyObject *scapkit_test_recording_owners(PyObject *self, PyObject *unused)
{
    return PyLong_FromLong(atomic_load(&recording_live_owners));
}

PyObject *scapkit_test_recording_state(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    if (!PyArg_ParseTuple(args, "O", &capsule))
    {
        return NULL;
    }
    ScapkitRecorder *owner = (__bridge ScapkitRecorder *)PyCapsule_GetPointer(capsule, "scapkit_recording");
    if (!owner)
    {
        return NULL;
    }
    BOOL ready, stopping, done;
    Py_BEGIN_ALLOW_THREADS
    [owner->condition lock];
    ready = owner->ready;
    stopping = owner->stopping;
    done = owner->done;
    [owner->condition unlock];
    Py_END_ALLOW_THREADS
    return Py_BuildValue("{s:O,s:O,s:O}", "ready", ready ? Py_True : Py_False,
                         "stopping", stopping ? Py_True : Py_False, "done", done ? Py_True : Py_False);
}

PyObject *scapkit_test_recording_audio(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    long long nanoseconds;
    int count;
    if (!PyArg_ParseTuple(args, "OLi", &capsule, &nanoseconds, &count))
    {
        return NULL;
    }
    ScapkitRecorder *owner = (__bridge ScapkitRecorder *)PyCapsule_GetPointer(capsule, "scapkit_recording");
    if (!owner)
    {
        return NULL;
    }
    if (!owner->manualClock || count <= 0 || count > 96000)
    {
        PyErr_SetString(PyExc_ValueError, "synthetic audio requires a manual clock and 1..96000 samples");
        return NULL;
    }
    @autoreleasepool
    {
        OSStatus status;
        NSError *error;
        Py_BEGIN_ALLOW_THREADS
        AudioStreamBasicDescription format = {48000, kAudioFormatLinearPCM,
            kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked, 8, 1, 8, 2, 32, 0};
        CMAudioFormatDescriptionRef description = NULL;
        CMBlockBufferRef block = NULL;
        CMSampleBufferRef sample = NULL;
        status = CMAudioFormatDescriptionCreate(NULL, &format, 0, NULL, 0, NULL, NULL, &description);
        if (status == noErr)
        {
            status = CMBlockBufferCreateWithMemoryBlock(NULL, NULL, (size_t)count * 8, NULL, NULL, 0, (size_t)count * 8, 0, &block);
        }
        if (status == noErr)
        {
            status = CMBlockBufferFillDataBytes(0, block, 0, (size_t)count * 8);
        }
        if (status == noErr)
        {
            CMSampleTimingInfo timing = {CMTimeMake(1, 48000), CMTimeAdd(CMTimeMake(10, 1), CMTimeMake(nanoseconds, NSEC_PER_SEC)), kCMTimeInvalid};
            status = CMSampleBufferCreateReady(NULL, block, description, count, 1, &timing, 0, NULL, &sample);
        }
        if (status == noErr)
        {
            dispatch_sync(owner->queue, ^{ [owner acceptAudio:sample]; });
        }
        if (sample)
        {
            CFRelease(sample);
        }
        if (block)
        {
            CFRelease(block);
        }
        if (description)
        {
            CFRelease(description);
        }
        error = [owner currentFailure];
        Py_END_ALLOW_THREADS
        if (error || status != noErr)
        {
            recording_raise(error ?: recording_error(RecorderFailure, @"could not create synthetic audio"));
            return NULL;
        }
        Py_RETURN_NONE;
    }
}
