#import "recording_writer.h"
#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
#import <VideoToolbox/VideoToolbox.h>
#import <errno.h>
#import <math.h>
#import <os/lock.h>
#import <sys/stat.h>
#import <unistd.h>

static NSError *RWError(NSString *message, NSInteger code) {
    return [NSError errorWithDomain:@"ScapkitRecordingWriter"
                               code:code
                           userInfo:@{NSLocalizedDescriptionKey : message}];
}
@interface ScapkitAudioChunk : NSObject
@property(nonatomic) int64_t start;
@property(nonatomic, strong) NSData *bytes;
@end
@implementation ScapkitAudioChunk
@end

@interface ScapkitEncoderContext : NSObject
@property(nonatomic, weak) ScapkitRecordingWriter *owner;
@end
@implementation ScapkitEncoderContext
@end
static char RWQueueKey;

@interface ScapkitRecordingWriter () {
    dispatch_queue_t _queue;
    dispatch_semaphore_t _videoSlots;
    os_unfair_lock _publicationLock;
    BOOL _publicationPrevented;
    NSDictionary *_publishedResult;
    VTCompressionSessionRef _encoder;
    ScapkitEncoderContext *_encoderContext;
    AVAssetWriter *_writer;
    AVAssetWriterInput *_videoInput;
    AVAssetWriterInput *_audioInput;
    NSMutableArray<ScapkitAudioChunk *> *_audio;
    NSError *_failure;
    NSString *_path;
    NSString *_temporary;
    int _width, _height, _fps;
    BOOL _closed, _started;
    int64_t _audioCursor;
    NSUInteger _audioBytes;
    CMTime _videoEnd;
}
- (void)receivedVideo:(CMSampleBufferRef)sample
               status:(OSStatus)status
                flags:(VTEncodeInfoFlags)flags;
@end

static void RWEncoded(void *refcon, void *source, OSStatus status, VTEncodeInfoFlags flags,
                      CMSampleBufferRef sample) {
    (void)source;
    @autoreleasepool {
        ScapkitEncoderContext *context = (__bridge ScapkitEncoderContext *)refcon;
        ScapkitRecordingWriter *owner = context.owner;
        [owner receivedVideo:sample status:status flags:flags];
    }
}

@implementation ScapkitRecordingWriter
- (instancetype)initWriterWithPath:(NSString *)path
                             width:(int)width
                            height:(int)height
                               fps:(int)fps
                      videoQuality:(double)videoQuality
                             error:(NSError *__strong *)error {
    self = [super init];
    if (!self) {
        return nil;
    }
    @autoreleasepool {
        _publicationLock = (os_unfair_lock)OS_UNFAIR_LOCK_INIT;
        _queue = dispatch_queue_create("scapkit.recording.writer", DISPATCH_QUEUE_SERIAL);
        _videoSlots = dispatch_semaphore_create(2);
        _audio = [NSMutableArray array];
        if (!_queue || !_videoSlots || !_audio) {
            if (error) {
                *error = RWError(@"Writer state allocation failed", ENOMEM);
            }
            return nil;
        }
        dispatch_queue_set_specific(_queue, &RWQueueKey, (__bridge void *)self, NULL);
        _videoEnd = kCMTimeZero;
        if (!path.length || width <= 0 || height <= 0 || width % 2 || height % 2 || fps <= 0 ||
            !isfinite(videoQuality) || (videoQuality != -1 && (videoQuality < 0 || videoQuality > 1))) {
            if (error) {
                *error = RWError(@"Invalid recording path, even dimensions or frame rate", EINVAL);
            }
            return nil;
        }
        _path = [path.stringByStandardizingPath copy];
        if (!_path.isAbsolutePath) {
            _path = [[[NSFileManager defaultManager] currentDirectoryPath]
                stringByAppendingPathComponent:_path];
        }
        if (!_path) {
            if (error) {
                *error = RWError(@"Recording path allocation failed", ENOMEM);
            }
            return nil;
        }
        struct stat st;
        if (lstat(_path.fileSystemRepresentation, &st) == 0 || errno != ENOENT) {
            if (error) {
                *error = RWError(@"Recording target already exists or cannot be accessed", EEXIST);
            }
            return nil;
        }
        _width = width;
        _height = height;
        _fps = fps;
        NSDictionary *spec = @{
            (__bridge NSString *)
            kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder : @YES
        };
        NSDictionary *attrs = @{
            (__bridge NSString *)
            kCVPixelBufferPixelFormatTypeKey : @(kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange),
            (__bridge NSString *)kCVPixelBufferIOSurfacePropertiesKey : @{}
        };
        _encoderContext = [ScapkitEncoderContext new];
        if (!_encoderContext) {
            if (error) {
                *error = RWError(@"Encoder context allocation failed", ENOMEM);
            }
            return nil;
        }
        _encoderContext.owner = self;
        OSStatus status = VTCompressionSessionCreate(
            NULL, width, height, kCMVideoCodecType_H264, (__bridge CFDictionaryRef)spec,
            (__bridge CFDictionaryRef)attrs, NULL, RWEncoded, (__bridge void *)_encoderContext, &_encoder);
        if (status != noErr) {
            if (error) {
                *error =
                    [NSError errorWithDomain:@"ScapkitRecordingHardware"
                                        code:status
                                    userInfo:@{
                                        NSLocalizedDescriptionKey :
                                            @"Required hardware H.264 encoder could not be created"
                                    }];
            }
            return nil;
        }
        NSDictionary *properties = @{
            (__bridge NSString *)kVTCompressionPropertyKey_RealTime : @YES,
            (__bridge NSString *)kVTCompressionPropertyKey_AllowFrameReordering : @NO,
            // Two slots require the encoder to emit the preceding frame by the
            // next submission, instead of waiting for an unbounded lookahead.
            (__bridge NSString *)kVTCompressionPropertyKey_MaxFrameDelayCount : @1,
            (__bridge NSString *)kVTCompressionPropertyKey_ExpectedFrameRate : @(fps),
            (__bridge NSString *)kVTCompressionPropertyKey_ProfileLevel :
                (__bridge NSString *)kVTProfileLevel_H264_High_AutoLevel,
            (__bridge NSString *)kVTCompressionPropertyKey_ColorPrimaries :
                (__bridge NSString *)kCMFormatDescriptionColorPrimaries_ITU_R_709_2,
            (__bridge NSString *)kVTCompressionPropertyKey_TransferFunction :
                (__bridge NSString *)kCMFormatDescriptionTransferFunction_ITU_R_709_2,
            (__bridge NSString *)kVTCompressionPropertyKey_YCbCrMatrix :
                (__bridge NSString *)kCMFormatDescriptionYCbCrMatrix_ITU_R_709_2
        };
        status = VTSessionSetProperties(_encoder, (__bridge CFDictionaryRef)properties);
        if (status == noErr && videoQuality >= 0) {
            CFDictionaryRef supported = NULL;
            status = VTSessionCopySupportedPropertyDictionary(_encoder, &supported);
            if (status == noErr && (!supported ||
                !CFDictionaryContainsKey(supported, kVTCompressionPropertyKey_Quality))) {
                status = kVTPropertyNotSupportedErr;
            }
            if (supported) {
                CFRelease(supported);
            }
            if (status == noErr) {
                status = VTSessionSetProperty(_encoder, kVTCompressionPropertyKey_Quality,
                                              (__bridge CFNumberRef)@(videoQuality));
            }
            if (status != noErr) {
                if (error) {
                    *error = RWError(@"Hardware H.264 encoder does not support the requested quality", status);
                }
                [self cancel];
                return nil;
            }
        }
        if (status == noErr) {
            status = VTCompressionSessionPrepareToEncodeFrames(_encoder);
        }
        if (status != noErr) {
            if (error) {
                *error = RWError(@"Hardware encoder configuration failed", status);
            }
            [self cancel];
            return nil;
        }
        NSString *template = [[_path stringByDeletingLastPathComponent]
            stringByAppendingPathComponent:@".scapkit-recording-XXXXXX.tmp.mp4"];
        char *temporary = strdup(template.fileSystemRepresentation);
        if (!temporary) {
            if (error) {
                *error = RWError(@"Temporary path allocation failed", ENOMEM);
            }
            [self cancel];
            return nil;
        }
        int fd = mkstemps(temporary, 8);
        if (fd < 0) {
            int saved = errno;
            free(temporary);
            if (error) {
                *error = RWError(@"Could not create recording temporary file", saved);
            }
            [self cancel];
            return nil;
        }
        close(fd);
        _temporary =
            [[NSFileManager defaultManager] stringWithFileSystemRepresentation:temporary
                                                                        length:strlen(temporary)];
        free(temporary);
        /* AVAssetWriter requires a nonexistent path; the random reservation is
         * released immediately before it opens that unique name. */
        unlink(_temporary.fileSystemRepresentation);
        NSError *writerError = nil;
        _writer = [[AVAssetWriter alloc] initWithURL:[NSURL fileURLWithPath:_temporary]
                                            fileType:AVFileTypeMPEG4
                                               error:&writerError];
        if (!_writer && error) {
            *error = writerError;
        }
        if (!_writer) {
            [self cancel];
            return nil;
        }
        _audioInput = [[AVAssetWriterInput alloc] initWithMediaType:AVMediaTypeAudio
                                                     outputSettings:@{
                                                         AVFormatIDKey : @(kAudioFormatMPEG4AAC),
                                                         AVSampleRateKey : @48000,
                                                         AVNumberOfChannelsKey : @2
                                                     }];
        _audioInput.expectsMediaDataInRealTime = YES;
        if (![_writer canAddInput:_audioInput]) {
            if (error) {
                *error = RWError(@"AAC writer input unavailable", EINVAL);
            }
            [self cancel];
            return nil;
        }
        [_writer addInput:_audioInput];
    }
    return self;
}

/* All methods below ending in OnQueue, and callback blocks, own writer state. */
- (BOOL)readyOnQueue:(AVAssetWriterInput *)input {
    double deadline = NSProcessInfo.processInfo.systemUptime + 5.;
    while (!input.readyForMoreMediaData && !_failure) {
        if (_writer.status == AVAssetWriterStatusFailed ||
            _writer.status == AVAssetWriterStatusCancelled) {
            _failure = _writer.error ?: RWError(@"Writer stopped accepting media", EIO);
            break;
        }
        if (NSProcessInfo.processInfo.systemUptime >= deadline) {
            _failure = RWError(@"Recording writer backpressure exceeded five seconds", ETIMEDOUT);
            break;
        }
        [NSThread sleepForTimeInterval:.001];
    }
    return !_failure;
}
- (void)receivedVideo:(CMSampleBufferRef)sample
               status:(OSStatus)status
                flags:(VTEncodeInfoFlags)flags {
    if (sample) {
        CFRetain(sample);
    }
    dispatch_async(_queue, ^{
        @autoreleasepool {
            if (!self->_failure && !self->_closed) {
                if (status != noErr || !sample || (flags & kVTEncodeInfo_FrameDropped)) {
                    self->_failure =
                        RWError(@"Hardware encoder failed or dropped a frame", status ?: EIO);
                } else {
                    if (!self->_started) {
                        self->_videoInput = [[AVAssetWriterInput alloc]
                            initWithMediaType:AVMediaTypeVideo
                               outputSettings:nil
                             sourceFormatHint:CMSampleBufferGetFormatDescription(sample)];
                        self->_videoInput.expectsMediaDataInRealTime = YES;
                        self->_videoInput.mediaTimeScale = 600000;
                        if (![self->_writer canAddInput:self->_videoInput]) {
                            self->_failure =
                                RWError(@"H.264 passthrough input unavailable", EINVAL);
                        } else {
                            [self->_writer addInput:self->_videoInput];
                            if (![self->_writer startWriting]) {
                                self->_failure = self->_writer.error
                                                     ?: RWError(@"MP4 writer startup failed", EIO);
                            } else {
                                [self->_writer startSessionAtSourceTime:kCMTimeZero];
                                self->_started = YES;
                            }
                        }
                    }
                    if (!self->_failure && [self readyOnQueue:self->_videoInput] &&
                        ![self->_videoInput appendSampleBuffer:sample]) {
                        self->_failure =
                            self->_writer.error ?: RWError(@"Video sample append failed", EIO);
                    }
                    // Commit silence against completed video, with one second
                    // available for audio callbacks that arrive later.
                    CMTime end = CMTimeAdd(CMSampleBufferGetPresentationTimeStamp(sample),
                                           CMSampleBufferGetDuration(sample));
                    int64_t horizon = CMTimeConvertScale(end, 48000,
                        kCMTimeRoundingMethod_RoundTowardZero).value - 48000;
                    if (!self->_failure && horizon > self->_audioCursor) {
                        [self flushAudioOnQueue:horizon];
                    }
                }
            }
            if (sample) {
                CFRelease(sample);
            }
            dispatch_semaphore_signal(self->_videoSlots);
        }
    });
}
- (BOOL)appendVideoImpl:(CVPixelBufferRef)frame
                   time:(CMTime)time
               duration:(CMTime)duration
                  error:(NSError *__strong *)error {
    @autoreleasepool {
        if (_closed || !frame || !CMTIME_IS_NUMERIC(time) || !CMTIME_IS_NUMERIC(duration) ||
            CMTimeCompare(time, _videoEnd) != 0 || CMTimeCompare(duration, kCMTimeZero) <= 0 ||
            CVPixelBufferGetWidth(frame) != (size_t)_width ||
            CVPixelBufferGetHeight(frame) != (size_t)_height) {
            if (error) {
                *error = RWError(@"Invalid video frame, noncontiguous timestamp, or closed writer",
                                 EINVAL);
            }
            return NO;
        }
        /* Permit two frames in flight, including samples waiting for the file
         * writer. Waiting for every frame to finish here stalls SCStream delivery
         * and makes the frame clock repeatedly encode its old cached image.
         * Only finishAt: drains the encoder; sustained overload remains bounded. */
        if (dispatch_semaphore_wait(_videoSlots,
                                    dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC))) {
            if (error) {
                *error = RWError(@"Hardware encoder backlog exceeded five seconds", ETIMEDOUT);
            }
            return NO;
        }
        NSDictionary *properties =
            CMTimeCompare(time, kCMTimeZero) == 0
                ? @{(__bridge NSString *)kVTEncodeFrameOptionKey_ForceKeyFrame : @YES}
                : nil;
        OSStatus status = VTCompressionSessionEncodeFrame(
            _encoder, frame, time, duration, (__bridge CFDictionaryRef)properties, NULL, NULL);
        if (status != noErr) {
            dispatch_semaphore_signal(_videoSlots);
        }
        __block NSError *failure;
        dispatch_sync(_queue, ^{
            if (status != noErr && !self->_failure) {
                self->_failure = RWError(@"Hardware video encoding failed", status);
            }
            self->_videoEnd = CMTimeAdd(time, duration);
            failure = self->_failure;
        });
        if (failure && error) {
            *error = failure;
        }
        return !failure;
    }
}
- (BOOL)appendAudioImpl:(CMSampleBufferRef)sample
                 origin:(CMTime)origin
                  error:(NSError *__strong *)error {
    @autoreleasepool {
        if (_closed || !sample || !CMTIME_IS_NUMERIC(origin) ||
            !CMTIME_IS_NUMERIC(CMSampleBufferGetPresentationTimeStamp(sample))) {
            if (error) {
                *error = RWError(@"Invalid audio sample or closed writer", EINVAL);
            }
            return NO;
        }
        CMAudioFormatDescriptionRef format = CMSampleBufferGetFormatDescription(sample);
        const AudioStreamBasicDescription *asbd =
            format ? CMAudioFormatDescriptionGetStreamBasicDescription(format) : NULL;
        if (!asbd || asbd->mFormatID != kAudioFormatLinearPCM || asbd->mSampleRate != 48000 ||
            asbd->mChannelsPerFrame != 2 || !(asbd->mFormatFlags & kAudioFormatFlagIsFloat) ||
            asbd->mBitsPerChannel != 32 || (asbd->mFormatFlags & kAudioFormatFlagIsBigEndian)) {
            if (error) {
                *error = RWError(@"Expected 48000 Hz stereo float32 PCM", EINVAL);
            }
            return NO;
        }
        CMItemCount count = CMSampleBufferGetNumSamples(sample);
        if (count < 0 || count > 96000) {
            if (error) {
                *error = RWError(@"Audio block exceeds bounded pending capacity", EOVERFLOW);
            }
            return NO;
        }
        CMTime relative = CMTimeConvertScale(
            CMTimeSubtract(CMSampleBufferGetPresentationTimeStamp(sample), origin), 48000,
            kCMTimeRoundingMethod_RoundHalfAwayFromZero);
        if (!CMTIME_IS_NUMERIC(relative) || relative.timescale != 48000 ||
            relative.value > INT64_MAX - count) {
            if (error) {
                *error = RWError(@"Unrepresentable audio timestamp", EOVERFLOW);
            }
            return NO;
        }
        int64_t start = relative.value;
        if (count == 0) {
            return YES;
        }
        size_t listSize = 0;
        OSStatus status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sample, &listSize, NULL, 0, NULL, NULL, 0, NULL);
        if (status != noErr || listSize > 4096) {
            if (error) {
                *error = RWError(@"Invalid PCM buffer list size", status ?: EOVERFLOW);
            }
            return NO;
        }
        AudioBufferList *buffers = malloc(listSize);
        if (!buffers) {
            if (error) {
                *error = RWError(@"PCM buffer list allocation failed", ENOMEM);
            }
            return NO;
        }
        CMBlockBufferRef block = NULL;
        status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sample, NULL, buffers, listSize, NULL, NULL,
            kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, &block);
        if (status != noErr) {
            free(buffers);
            if (block) {
                CFRelease(block);
            }
            if (error) {
                *error = RWError(@"Unable to access PCM buffers", status);
            }
            return NO;
        }
        BOOL planar = (asbd->mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0;
        BOOL valid = buffers->mNumberBuffers == (planar ? 2 : 1);
        for (UInt32 i = 0; valid && i < buffers->mNumberBuffers; i++) {
            valid = buffers->mBuffers[i].mData &&
                    buffers->mBuffers[i].mDataByteSize >= (size_t)count * (planar ? 4 : 8);
        }
        if (!valid) {
            free(buffers);
            if (block) {
                CFRelease(block);
            }
            if (error) {
                *error = RWError(@"Invalid PCM buffer layout", EINVAL);
            }
            return NO;
        }
        NSMutableData *bytes = [NSMutableData dataWithLength:(NSUInteger)count * 8];
        float *out = bytes.mutableBytes;
        if (!bytes || !out) {
            free(buffers);
            if (block) {
                CFRelease(block);
            }
            if (error) {
                *error = RWError(@"PCM copy allocation failed", ENOMEM);
            }
            return NO;
        }
        if (planar) {
            const float *left = buffers->mBuffers[0].mData, *right = buffers->mBuffers[1].mData;
            for (CMItemCount i = 0; i < count; i++) {
                out[2 * i] = left[i];
                out[2 * i + 1] = right[i];
            }
        } else {
            memcpy(out, buffers->mBuffers[0].mData, (size_t)count * 8);
        }
        free(buffers);
        if (block) {
            CFRelease(block);
        }
        __block NSError *failure;
        dispatch_sync(_queue, ^{
            if (!self->_failure && start + count > 0 && self->_audioCursor > 0 &&
                start < self->_audioCursor) {
                self->_failure =
                    RWError(@"Audio arrived after its timeline was committed", ETIMEDOUT);
            }
            if (!self->_failure && start + count > self->_audioCursor) {
                if (self->_audioBytes + bytes.length > 96000 * 8 ||
                    start > CMTimeConvertScale(self->_videoEnd, 48000,
                                               kCMTimeRoundingMethod_RoundTowardZero)
                                    .value +
                                96000) {
                    self->_failure = RWError(@"Pending audio exceeded two seconds", EOVERFLOW);
                } else {
                    ScapkitAudioChunk *chunk = [ScapkitAudioChunk new];
                    if (!chunk) {
                        self->_failure = RWError(@"Pending PCM allocation failed", ENOMEM);
                        failure = self->_failure;
                        return;
                    }
                    chunk.start = start;
                    chunk.bytes = bytes;
                    [self->_audio addObject:chunk];
                    self->_audioBytes += bytes.length;
                    [self->_audio sortUsingComparator:^NSComparisonResult(ScapkitAudioChunk *a,
                                                                          ScapkitAudioChunk *b) {
                        return a.start < b.start
                                   ? NSOrderedAscending
                                   : (a.start > b.start ? NSOrderedDescending : NSOrderedSame);
                    }];
                }
            }
            failure = self->_failure;
        });
        if (failure && error) {
            *error = failure;
        }
        return !failure;
    }
}
- (void)flushAudioOnQueue:(int64_t)end {
    while (_audioCursor < end && !_failure) {
        @autoreleasepool {
            int64_t count = MIN(1024, end - _audioCursor);
            NSMutableData *bytes = [NSMutableData dataWithLength:(NSUInteger)count * 8];
            if (!bytes || !bytes.mutableBytes) {
                _failure = RWError(@"PCM output allocation failed", ENOMEM);
                break;
            }
            for (ScapkitAudioChunk *chunk in _audio) {
                int64_t first = MAX(_audioCursor, chunk.start),
                        last = MIN(_audioCursor + count,
                                   chunk.start + (int64_t)chunk.bytes.length / 8);
                if (last > first) {
                    memcpy((char *)bytes.mutableBytes + (first - _audioCursor) * 8,
                           (const char *)chunk.bytes.bytes + (first - chunk.start) * 8,
                           (size_t)(last - first) * 8);
                }
            }
            AudioStreamBasicDescription asbd = {48000,
                                                kAudioFormatLinearPCM,
                                                kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
                                                8,
                                                1,
                                                8,
                                                2,
                                                32,
                                                0};
            CMAudioFormatDescriptionRef format = NULL;
            CMBlockBufferRef block = NULL;
            CMSampleBufferRef sample = NULL;
            OSStatus status =
                CMAudioFormatDescriptionCreate(NULL, &asbd, 0, NULL, 0, NULL, NULL, &format);
            if (status == noErr) {
                status = CMBlockBufferCreateWithMemoryBlock(NULL, NULL, bytes.length, NULL, NULL, 0,
                                                            bytes.length, 0, &block);
            }
            if (status == noErr) {
                status = CMBlockBufferReplaceDataBytes(bytes.bytes, block, 0, bytes.length);
            }
            CMSampleTimingInfo timing = {CMTimeMake(1, 48000), CMTimeMake(_audioCursor, 48000),
                                         kCMTimeInvalid};
            if (status == noErr) {
                status = CMSampleBufferCreateReady(NULL, block, format, count, 1, &timing, 0, NULL,
                                                   &sample);
            }
            if (status != noErr) {
                _failure = RWError(@"Creating aligned PCM sample failed", status);
            } else if ([self readyOnQueue:_audioInput] &&
                       ![_audioInput appendSampleBuffer:sample]) {
                _failure = _writer.error ?: RWError(@"AAC sample append failed", EIO);
            }
            if (sample) {
                CFRelease(sample);
            }
            if (block) {
                CFRelease(block);
            }
            if (format) {
                CFRelease(format);
            }
            _audioCursor += count;
            while (_audio.count &&
                   _audio.firstObject.start + (int64_t)_audio.firstObject.bytes.length / 8 <=
                       _audioCursor) {
                _audioBytes -= _audio.firstObject.bytes.length;
                [_audio removeObjectAtIndex:0];
            }
        }
    }
}
- (NSDictionary *)finishImplAt:(CMTime)endTime error:(NSError *__strong *)error {
    @autoreleasepool {
        if (_closed || !CMTIME_IS_NUMERIC(endTime) || CMTimeCompare(endTime, kCMTimeZero) <= 0 ||
            CMTimeCompare(endTime, _videoEnd) != 0) {
            if (error) {
                *error = RWError(@"Invalid finish time or closed writer", EINVAL);
            }
            [self cancel];
            return nil;
        }
        OSStatus status = VTCompressionSessionCompleteFrames(_encoder, kCMTimeInvalid);
        VTCompressionSessionInvalidate(_encoder);
        CFRelease(_encoder);
        _encoder = NULL;
        __block NSError *failure;
        dispatch_sync(_queue, ^{
            if (status != noErr && !self->_failure) {
                self->_failure = RWError(@"Encoder drain failed", status);
            }
            if (!self->_started && !self->_failure) {
                self->_failure = RWError(@"No encoded video frames", EINVAL);
            }
            if (!self->_failure) {
                [self flushAudioOnQueue:CMTimeConvertScale(endTime, 48000,
                                                           kCMTimeRoundingMethod_RoundTowardZero)
                                            .value];
            }
            if (!self->_failure) {
                [self->_writer endSessionAtSourceTime:endTime];
                [self->_videoInput markAsFinished];
                [self->_audioInput markAsFinished];
            }
            failure = self->_failure;
            self->_closed = YES;
        });
        if (!failure) {
            dispatch_semaphore_t finished = dispatch_semaphore_create(0);
            AVAssetWriter *writer = _writer;
            [writer finishWritingWithCompletionHandler:^{
                dispatch_semaphore_signal(finished);
            }];
            if (dispatch_semaphore_wait(finished,
                                        dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) != 0) {
                failure = RWError(@"MP4 finalization timed out", ETIMEDOUT);
                [writer cancelWriting];
            } else if (writer.status != AVAssetWriterStatusCompleted) {
                failure = writer.error ?: RWError(@"MP4 finalization failed", EIO);
            }
        }
        // Inspect and allocate metadata before publication: once the link is
        // visible, the committed immutable result must already be available.
        NSDictionary *preparedResult = nil;
        if (!failure) {
            struct stat st;
            if (stat(_temporary.fileSystemRepresentation, &st) != 0) {
                failure = RWError(@"Could not inspect completed MP4", errno);
            } else {
                preparedResult = @{
                    @"path" : _path,
                    @"size_bytes" : @(st.st_size),
                    @"duration_s" : @(CMTimeGetSeconds(endTime)),
                    @"width" : @(_width),
                    @"height" : @(_height),
                    @"fps" : @(_fps)
                };
                if (!preparedResult) {
                    failure = RWError(@"Recording result allocation failed", ENOMEM);
                }
            }
        }
        NSDictionary *result = nil;
        if (!failure) {
            // The gate, link and result share a lock. A timeout either prevents
            // publication or obtains the completed result, never a false failure
            // after the final file became visible. Codec cleanup stays outside.
            int publicationError = 0;
            const char *temporaryPath = _temporary.fileSystemRepresentation;
            const char *finalPath = _path.fileSystemRepresentation;
            os_unfair_lock_lock(&_publicationLock);
            if (_publicationPrevented) {
                publicationError = ECANCELED;
            } else if (link(temporaryPath, finalPath) != 0) {
                publicationError = errno;
            } else {
                _publishedResult = preparedResult;
                result = _publishedResult;
            }
            os_unfair_lock_unlock(&_publicationLock);
            if (publicationError) {
                failure = RWError(publicationError == ECANCELED
                                      ? @"Recording publication was prevented"
                                      : @"Could not publish MP4 without overwriting the target",
                                  publicationError);
            }
        }
        if (failure && error) {
            *error = failure;
        }
        [self cancel];
        return result;
    }
}
/* Public error writeback happens after draining the local pool. The strong local
 * prevents NSError autoreleasing out-parameters from escaping an inner pool. */
- (instancetype)initWithPath:(NSString *)path
                       width:(int)width
                      height:(int)height
                         fps:(int)fps
                       error:(NSError **)error {
    return [self initWithPath:path width:width height:height fps:fps videoQuality:0.75 error:error];
}
- (instancetype)initWithPath:(NSString *)path
                       width:(int)width
                      height:(int)height
                         fps:(int)fps
                videoQuality:(double)videoQuality
                       error:(NSError **)error {
    NSError *failure = nil;
    @autoreleasepool {
        self = [self initWriterWithPath:path width:width height:height fps:fps videoQuality:videoQuality error:&failure];
    }
    if (error) {
        *error = failure;
    }
    return self;
}
- (BOOL)appendVideo:(CVPixelBufferRef)frame
               time:(CMTime)time
           duration:(CMTime)duration
              error:(NSError **)error {
    NSError *failure = nil;
    BOOL result;
    @autoreleasepool {
        result = [self appendVideoImpl:frame time:time duration:duration error:&failure];
    }
    if (error) {
        *error = failure;
    }
    return result;
}
- (BOOL)appendAudio:(CMSampleBufferRef)sample origin:(CMTime)origin error:(NSError **)error {
    NSError *failure = nil;
    BOOL result;
    @autoreleasepool {
        result = [self appendAudioImpl:sample origin:origin error:&failure];
    }
    if (error) {
        *error = failure;
    }
    return result;
}
- (NSDictionary *)finishAt:(CMTime)endTime error:(NSError **)error {
    NSError *failure = nil;
    NSDictionary *result;
    @autoreleasepool {
        result = [self finishImplAt:endTime error:&failure];
    }
    if (error) {
        *error = failure;
    }
    return result;
}

- (NSDictionary *)preventPublication {
    os_unfair_lock_lock(&_publicationLock);
    _publicationPrevented = YES;
    NSDictionary *result = _publishedResult;
    os_unfair_lock_unlock(&_publicationLock);
    return result;
}
- (void)cancel {
    @autoreleasepool {
        [self preventPublication];
        if (_encoder) {
            VTCompressionSessionInvalidate(_encoder);
            CFRelease(_encoder);
            _encoder = NULL;
        }
        _encoderContext = nil;
        if (_queue) {
            // A queued output may release the last owner on this queue. Avoid
            // syncing to ourselves or retaining an object already in dealloc.
            __unsafe_unretained ScapkitRecordingWriter *owner = self;
            void (^cleanup)(void) = ^{
                owner->_closed = YES;
                if (owner->_writer.status == AVAssetWriterStatusWriting ||
                    owner->_writer.status == AVAssetWriterStatusUnknown) {
                    [owner->_writer cancelWriting];
                }
                owner->_videoInput = nil;
                owner->_audioInput = nil;
                owner->_writer = nil;
                [owner->_audio removeAllObjects];
                owner->_audioBytes = 0;
                if (owner->_temporary) {
                    unlink(owner->_temporary.fileSystemRepresentation);
                    owner->_temporary = nil;
                }
                // Invalidation may discard pending frames without output
                // callbacks. With the encoder stopped and queue fenced, no
                // token users remain; restore slots before semaphore disposal.
                if (owner->_videoSlots) {
                    while (dispatch_semaphore_wait(owner->_videoSlots, DISPATCH_TIME_NOW) == 0) {
                    }
                    dispatch_semaphore_signal(owner->_videoSlots);
                    dispatch_semaphore_signal(owner->_videoSlots);
                }
            };
            if (dispatch_get_specific(&RWQueueKey) == (__bridge void *)self) {
                cleanup();
            } else {
                dispatch_sync(_queue, cleanup);
            }
        }
    }
}
- (void)dealloc {
    [self cancel];
}
@end
