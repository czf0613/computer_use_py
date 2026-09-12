#import "recording_writer.h"
#import <AVFoundation/AVFoundation.h>
#import <AudioToolbox/AudioToolbox.h>
#import <math.h>
#import <objc/runtime.h>
#import <VideoToolbox/VideoToolbox.h>

@interface ScapkitRecordingWriter (ProbeOutput)
- (void)receivedVideo:(CMSampleBufferRef)sample status:(OSStatus)status flags:(VTEncodeInfoFlags)flags;
- (void)flushAudioOnQueue:(int64_t)end;
@end
static dispatch_semaphore_t outputEntered, outputReleased;
static dispatch_semaphore_t audioCommitted;
@interface AudioCommitWriter : ScapkitRecordingWriter
@end
@implementation AudioCommitWriter
- (void)flushAudioOnQueue:(int64_t)end {
    [super flushAudioOnQueue:end];
    dispatch_semaphore_signal(audioCommitted);
}
@end
@interface PendingOutputWriter : ScapkitRecordingWriter
@end
@implementation PendingOutputWriter
- (void)receivedVideo:(CMSampleBufferRef)sample status:(OSStatus)status flags:(VTEncodeInfoFlags)flags {
    dispatch_semaphore_signal(outputEntered);
    if (dispatch_semaphore_wait(outputReleased, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC))) {
        exit(20);
    }
    [super receivedVideo:sample status:status flags:flags];
}
@end
static BOOL refuseInput(id object, SEL selector) {
    (void)object;
    (void)selector;
    return NO;
}

static IMP originalFinish;
static dispatch_semaphore_t finishReached, finishReleased;
static void delayedFinish(id object, SEL selector, void (^completion)(void)) {
    ((void (*)(id, SEL, void (^)(void)))originalFinish)(object, selector, ^{
        dispatch_semaphore_signal(finishReached);
        if (dispatch_semaphore_wait(finishReleased,
                                    dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC))) {
            exit(15);
        }
        completion();
    });
}
static void die(NSError *error) {
    fprintf(stderr, "%s\n", error.description.UTF8String);
    exit(1);
}
static void emit(NSDictionary *info) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:info options:0 error:nil];
    puts([[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding].UTF8String);
}
static CMSampleBufferRef pcm(double start, double length, double origin) {
    int count = (int)llround(length * 48000);
    AudioStreamBasicDescription asbd = {48000,
                                        kAudioFormatLinearPCM,
                                        kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
                                        8,
                                        1,
                                        8,
                                        2,
                                        32,
                                        0};
    CMAudioFormatDescriptionRef fmt = NULL;
    CMAudioFormatDescriptionCreate(NULL, &asbd, 0, NULL, 0, NULL, NULL, &fmt);
    CMBlockBufferRef block = NULL;
    CMBlockBufferCreateWithMemoryBlock(NULL, NULL, count * 8, NULL, NULL, 0, count * 8, 0, &block);
    CMBlockBufferAssureBlockMemory(block);
    char *bytes = NULL;
    CMBlockBufferGetDataPointer(block, 0, NULL, NULL, &bytes);
    float *samples = (float *)bytes;
    for (int i = 0; i < count; i++) {
        samples[2 * i] = samples[2 * i + 1] = .5 * sin(2 * M_PI * 440 * i / 48000);
    }
    CMSampleBufferRef sample = NULL;
    CMSampleBufferCreateReady(
        NULL, block, fmt, count, 1,
        &(CMSampleTimingInfo){CMTimeMake(1, 48000),
                              CMTimeMake(llround((origin + start) * 48000), 48000), kCMTimeInvalid},
        0, NULL, &sample);
    CFRelease(block);
    CFRelease(fmt);
    return sample;
}
static CMSampleBufferRef planarPCM(void) {
    int count = 16800;
    AudioStreamBasicDescription asbd = {48000,
                                        kAudioFormatLinearPCM,
                                        kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked |
                                            kAudioFormatFlagIsNonInterleaved,
                                        4,
                                        1,
                                        4,
                                        2,
                                        32,
                                        0};
    CMAudioFormatDescriptionRef fmt = NULL;
    CMAudioFormatDescriptionCreate(NULL, &asbd, 0, NULL, 0, NULL, NULL, &fmt);
    CMSampleBufferRef sample = NULL;
    CMSampleBufferCreate(NULL, NULL, false, NULL, NULL, fmt, count, 1,
                         &(CMSampleTimingInfo){CMTimeMake(1, 48000),
                                               CMTimeMake(1234 * 48000 + 12000, 48000),
                                               kCMTimeInvalid},
                         0, NULL, &sample);
    struct {
        AudioBufferList list;
        AudioBuffer extra;
    } storage = {0};
    AudioBufferList *list = &storage.list;
    float *data = malloc(count * 4);
    for (int i = 0; i < count; i++) {
        data[i] = .5 * sin(2 * M_PI * 440 * i / 48000);
    }
    list->mNumberBuffers = 2;
    list->mBuffers[0] = (AudioBuffer){1, count * 4, data};
    list->mBuffers[1] = (AudioBuffer){1, count * 4, data};
    OSStatus status = CMSampleBufferSetDataBufferFromAudioBufferList(sample, NULL, NULL, 0, list);
    free(data);
    CFRelease(fmt);
    if (status != noErr) {
        exit(8);
    }
    return sample;
}
static NSString *fourcc(FourCharCode code) {
    char text[5] = {(char)(code >> 24), (char)(code >> 16), (char)(code >> 8), (char)code, 0};
    return [NSString stringWithUTF8String:text];
}
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc != 3) {
            return 2;
        }
        NSString *path = [NSString stringWithUTF8String:argv[1]],
                 *mode = [NSString stringWithUTF8String:argv[2]];
        NSError *error = nil;
        if ([mode isEqual:@"missing-parent"]) {
            ScapkitRecordingWriter *missing = [[ScapkitRecordingWriter alloc]
                initWithPath:[path stringByAppendingPathComponent:@"missing/file.mp4"]
                       width:64
                      height:48
                         fps:30
                       error:&error];
            if (missing || !error) {
                return 11;
            }
            emit(@{@"ok" : @YES});
            return 0;
        }
        if ([mode isEqual:@"existing"]) {
            [@"KEEP" writeToFile:path atomically:NO encoding:NSUTF8StringEncoding error:&error];
        }
        Class writerClass = ([mode isEqual:@"pending-output"] || [mode isEqual:@"drop-pending-output"]) ? [PendingOutputWriter class] : [ScapkitRecordingWriter class];
        if ([mode isEqual:@"late-audio"]) {
            writerClass = [AudioCommitWriter class];
            audioCommitted = dispatch_semaphore_create(0);
        }
        BOOL qualityMode = [mode hasPrefix:@"quality-"];
        double videoQuality = [mode isEqual:@"quality-low"] ? 0.2 : 0.8;
        ScapkitRecordingWriter *writer =
            [[writerClass alloc] initWithPath:path
                                                   width:64
                                                  height:48
                                                     fps:[mode isEqual:@"invalid"] ? 0 : 30
                                            videoQuality:qualityMode ? videoQuality : -1
                                                   error:&error];
        if ([mode isEqual:@"existing"] || [mode isEqual:@"invalid"]) {
            if (writer || !error) {
                return 3;
            }
            emit(@{@"ok" : @YES});
            return 0;
        }
        if (!writer) {
            if ([error.domain isEqual:@"ScapkitRecordingHardware"]) {
                fprintf(stderr, "%s\n", error.description.UTF8String);
                return 77;
            }
            die(error);
        }
        if ([mode isEqual:@"drop"]) {
            writer = nil;
            emit(@{@"ok" : @YES});
            return 0;
        }
        if ([mode isEqual:@"finish-empty"]) {
            if ([writer finishAt:kCMTimeZero error:&error] || !error) {
                return 9;
            }
            emit(@{@"ok" : @YES});
            return 0;
        }
        if ([mode isEqual:@"cancel"]) {
            [writer cancel];
            [writer cancel];
            emit(@{@"ok" : @YES});
            return 0;
        }
        if ([mode isEqual:@"bad-audio"]) {
            CMSampleBufferRef invalidSample = NULL;
            if ([writer appendAudio:invalidSample origin:kCMTimeZero error:&error] || !error) {
                return 4;
            }
            [writer cancel];
            emit(@{@"ok" : @YES});
            return 0;
        }
        double duration = [mode isEqual:@"short"] ? .007 : ([mode isEqual:@"boundary"] ? 1. : 1.05);
        CVPixelBufferRef frame = NULL;
        CVPixelBufferCreate(
            NULL, 64, 48, kCVPixelFormatType_32BGRA,
            (__bridge CFDictionaryRef)
                @{(id)kCVPixelBufferIOSurfacePropertiesKey : @{}},
            &frame);
        CVPixelBufferLockBaseAddress(frame, 0);
        memset(CVPixelBufferGetBaseAddress(frame), 0x80, CVPixelBufferGetBytesPerRow(frame) * 48);
        CVPixelBufferUnlockBaseAddress(frame, 0);
        if ([mode isEqual:@"drop-pending-output"]) {
            outputEntered = dispatch_semaphore_create(0);
            outputReleased = dispatch_semaphore_create(0);
            if (![writer appendVideo:frame time:kCMTimeZero duration:CMTimeMake(1, 30) error:&error]) {
                die(error);
            }
            CFRelease(frame);
            if (dispatch_semaphore_wait(outputEntered, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC))) {
                exit(25);
            }
            __weak ScapkitRecordingWriter *observer = writer;
            writer = nil;
            dispatch_semaphore_signal(outputReleased);
            BOOL cleaned = NO;
            double deadline = NSProcessInfo.processInfo.systemUptime + 3;
            while (!cleaned && NSProcessInfo.processInfo.systemUptime < deadline) {
                @autoreleasepool {
                    cleaned = !observer && [[NSFileManager defaultManager]
                        contentsOfDirectoryAtPath:path.stringByDeletingLastPathComponent error:&error].count == 0;
                }
                [NSThread sleepForTimeInterval:0.001];
            }
            emit(@{@"cleaned": @(cleaned)});
            return 0;
        }
        if ([mode isEqual:@"pending-output"]) {
            outputEntered = dispatch_semaphore_create(0);
            outputReleased = dispatch_semaphore_create(0);
            dispatch_semaphore_t returned = dispatch_semaphore_create(0);
            __block BOOL accepted = NO;
            __block NSError *appendError = nil;
            dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
                accepted = [writer appendVideo:frame time:kCMTimeZero duration:CMTimeMake(1, 30) error:&appendError];
                dispatch_semaphore_signal(returned);
            });
            if (dispatch_semaphore_wait(outputEntered, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC))) {
                exit(21);
            }
            BOOL asynchronous = dispatch_semaphore_wait(returned, dispatch_time(DISPATCH_TIME_NOW, 100 * NSEC_PER_MSEC)) == 0;
            dispatch_semaphore_signal(outputReleased);
            if (!asynchronous && dispatch_semaphore_wait(returned, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC))) {
                exit(22);
            }
            CFRelease(frame);
            if (!accepted) {
                die(appendError);
            }
            NSDictionary *result = [writer finishAt:CMTimeMake(1, 30) error:&error];
            if (!result) {
                die(error);
            }
            emit(@{@"returned_while_encoder_pending": @(asynchronous)});
            return 0;
        }
        if ([mode isEqual:@"bad-time"]) {
            CMSampleBufferRef input = pcm(0, .1, 0);
            CMSampleTimingInfo timing = {CMTimeMake(1, 48000), CMTimeMake(INT64_MAX, 1),
                                         kCMTimeInvalid};
            CMSampleBufferRef invalid = NULL;
            CMSampleBufferCreateCopyWithNewTiming(NULL, input, 1, &timing, &invalid);
            CFRelease(input);
            BOOL accepted = [writer appendAudio:invalid origin:kCMTimeZero error:&error];
            CFRelease(invalid);
            CFRelease(frame);
            if (accepted || !error) {
                fprintf(stderr, "Unrepresentable audio timestamp accepted\n");
                return 12;
            }
            [writer cancel];
            emit(@{@"ok" : @YES});
            return 0;
        }
        if ([mode isEqual:@"drop-after-frame"] || [mode isEqual:@"backpressure"]) {
            if ([mode isEqual:@"backpressure"]) {
                method_setImplementation(
                    class_getInstanceMethod([AVAssetWriterInput class],
                                            @selector(isReadyForMoreMediaData)),
                    (IMP)refuseInput);
            }
            BOOL accepted = [writer appendVideo:frame
                                           time:kCMTimeZero
                                       duration:CMTimeMake(1, 30)
                                          error:&error];
            CFRelease(frame);
            if ([mode isEqual:@"backpressure"]) {
                // An asynchronous encoder can discover writer failure after
                // submission succeeds. Finalization must still report it.
                NSDictionary *result = accepted ? [writer finishAt:CMTimeMake(1, 30) error:&error] : nil;
                if (result || !error) {
                    return 13;
                }
            }
            writer = nil;
            emit(@{@"ok" : @YES});
            return 0;
        }
        if ([mode isEqual:@"late-audio"] || [mode isEqual:@"overflow"]) {
            if ([mode isEqual:@"late-audio"] && ![writer appendVideo:frame
                                                                time:kCMTimeZero
                                                            duration:CMTimeMake(2, 1)
                                                               error:&error]) {
                die(error);
            }
            if ([mode isEqual:@"late-audio"] &&
                dispatch_semaphore_wait(audioCommitted, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC))) {
                exit(23);
            }
            CMSampleBufferRef input = pcm([mode isEqual:@"overflow"] ? 3. : 0., .1, 1234.);
            BOOL accepted = [writer appendAudio:input origin:CMTimeMake(1234, 1) error:&error];
            CFRelease(input);
            CFRelease(frame);
            if (accepted || !error) {
                fprintf(stderr, "Late/excessively future audio was not rejected\n");
                return 10;
            }
            [writer cancel];
            emit(@{@"ok" : @YES});
            return 0;
        }
        if ([mode isEqual:@"pre-origin"] || [mode isEqual:@"planar"]) {
            CMSampleBufferRef input = [mode isEqual:@"planar"] ? planarPCM() : pcm(-.1, .3, 1234.);
            if (![writer appendAudio:input origin:CMTimeMake(1234, 1) error:&error]) {
                die(error);
            }
            CFRelease(input);
        }
        if ([mode isEqual:@"tone"]) {
            CMSampleBufferRef audio = pcm(.25, .35, 1234.);
            if (![writer appendAudio:audio origin:CMTimeMake(1234, 1) error:&error]) {
                die(error);
            }
            CFRelease(audio);
            audio = pcm(1.02, .2, 1234.);
            if (![writer appendAudio:audio origin:CMTimeMake(1234, 1) error:&error]) {
                die(error);
            }
            CFRelease(audio);
        }
        for (int i = 0; i / 30. < duration - .0000001; i++) {
            if (qualityMode) {
                // Every input has its own storage until asynchronous encoding
                // releases it. Deterministic noise exercises actual rate control.
                CFRelease(frame);
                if (CVPixelBufferCreate(NULL, 64, 48, kCVPixelFormatType_32BGRA,
                                        (__bridge CFDictionaryRef)@{(id)kCVPixelBufferIOSurfacePropertiesKey:@{}},
                                        &frame) != kCVReturnSuccess ||
                    CVPixelBufferLockBaseAddress(frame, 0) != kCVReturnSuccess) {
                    exit(24);
                }
                uint32_t state = (uint32_t)i + 1;
                for (int y = 0; y < 48; y++) {
                    unsigned char *row = (unsigned char *)CVPixelBufferGetBaseAddress(frame) + y * CVPixelBufferGetBytesPerRow(frame);
                    for (int x = 0; x < 64; x++) {
                        state ^= state << 13;
                        state ^= state >> 17;
                        state ^= state << 5;
                        row[x * 4] = (unsigned char)state;
                        row[x * 4 + 1] = (unsigned char)(state >> 8);
                        row[x * 4 + 2] = (unsigned char)(state >> 16);
                        row[x * 4 + 3] = 255;
                    }
                }
                CVPixelBufferUnlockBaseAddress(frame, 0);
            }
            if (![writer appendVideo:frame
                                time:CMTimeMake(i, 30)
                            duration:CMTimeMake(llround(fmin(1. / 30., duration - i / 30.) * 48000),
                                                48000)
                               error:&error]) {
                die(error);
            }
        }
        CFRelease(frame);
        if ([mode isEqual:@"race"]) {
            [@"KEEP" writeToFile:path atomically:NO encoding:NSUTF8StringEncoding error:&error];
        }
        if ([mode isEqual:@"prevent-during-finish"]) {
            finishReached = dispatch_semaphore_create(0);
            finishReleased = dispatch_semaphore_create(0);
            dispatch_semaphore_t done = dispatch_semaphore_create(0);
            originalFinish = method_setImplementation(
                class_getInstanceMethod([AVAssetWriter class],
                                        @selector(finishWritingWithCompletionHandler:)),
                (IMP)delayedFinish);
            __block NSDictionary *result = nil;
            __block NSError *failure = nil;
            dispatch_async(dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^{
                @autoreleasepool {
                    result = [writer finishAt:CMTimeMake(llround(duration * 48000), 48000)
                                        error:&failure];
                }
                dispatch_semaphore_signal(done);
            });
            if (dispatch_semaphore_wait(finishReached,
                                        dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC))) {
                return 16;
            }
            [writer preventPublication];
            dispatch_semaphore_signal(finishReleased);
            if (dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)) ||
                result || !failure) {
                return 17;
            }
            emit(@{@"ok" : @YES});
            return 0;
        }
        if ([mode isEqual:@"prevent-publication"]) {
            [writer preventPublication];
            [writer preventPublication];
            if ([writer finishAt:CMTimeMake(llround(duration * 48000), 48000) error:&error] ||
                !error) {
                return 14;
            }
            emit(@{@"ok" : @YES});
            return 0;
        }
        NSDictionary *result = [writer finishAt:CMTimeMake(llround(duration * 48000), 48000)
                                          error:&error];
        if ([mode isEqual:@"race"]) {
            if (result || !error) {
                return 5;
            }
            emit(@{@"ok" : @YES});
            return 0;
        }
        if (!result) {
            die(error);
        }
        if ([mode isEqual:@"published-result"]) {
            NSDictionary *committed = [writer preventPublication];
            [writer cancel];
            if (committed != result || [writer preventPublication] != result ||
                [committed isKindOfClass:[NSMutableDictionary class]]) {
                fprintf(stderr, "Publication gate did not retain the original immutable result\n");
                return 18;
            }
            emit(@{@"ok" : @YES, @"result" : committed});
            return 0;
        }
        AVURLAsset *asset = [AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:path] options:nil];
        AVAssetTrack *video = [asset tracksWithMediaType:AVMediaTypeVideo].firstObject,
                     *audio = [asset tracksWithMediaType:AVMediaTypeAudio].firstObject;
        AVAssetReader *reader = [[AVAssetReader alloc] initWithAsset:asset error:&error];
        AVAssetReaderTrackOutput *vout = [[AVAssetReaderTrackOutput alloc] initWithTrack:video
                                                                          outputSettings:nil];
        [reader addOutput:vout];
        [reader startReading];
        NSMutableArray *pts = [NSMutableArray array];
        BOOL firstKey = NO;
        double last = 0, end = 0;
        CMSampleBufferRef sample;
        while ((sample = [vout copyNextSampleBuffer])) {
            if (!CMSampleBufferGetNumSamples(sample)) {
                CFRelease(sample);
                continue;
            }
            if (!pts.count) {
                CFArrayRef attachments = CMSampleBufferGetSampleAttachmentsArray(sample, false);
                firstKey =
                    !attachments || !CFDictionaryContainsKey(CFArrayGetValueAtIndex(attachments, 0),
                                                             kCMSampleAttachmentKey_NotSync);
            }
            double t = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample));
            last = CMTimeGetSeconds(CMSampleBufferGetDuration(sample));
            end = t + last;
            [pts addObject:@(t)];
            CFRelease(sample);
        }
        if (reader.status == AVAssetReaderStatusFailed) {
            die(reader.error);
        }
        reader = [[AVAssetReader alloc] initWithAsset:asset error:&error];
        AVAssetReaderTrackOutput *decoded = [[AVAssetReaderTrackOutput alloc]
             initWithTrack:video
            outputSettings:@{
                (id)kCVPixelBufferPixelFormatTypeKey : @(kCVPixelFormatType_32BGRA)
            }];
        [reader addOutput:decoded];
        [reader startReading];
        int decodedFrames = 0;
        while ((sample = [decoded copyNextSampleBuffer])) {
            if (CMSampleBufferGetImageBuffer(sample)) {
                decodedFrames++;
            }
            CFRelease(sample);
        }
        if (reader.status == AVAssetReaderStatusFailed) {
            die(reader.error);
        }
        reader = [[AVAssetReader alloc] initWithAsset:asset error:&error];
        AVAssetReaderTrackOutput *aout =
            [[AVAssetReaderTrackOutput alloc] initWithTrack:audio
                                             outputSettings:@{
                                                 AVFormatIDKey : @(kAudioFormatLinearPCM),
                                                 AVLinearPCMIsFloatKey : @YES,
                                                 AVLinearPCMBitDepthKey : @32,
                                                 AVLinearPCMIsNonInterleaved : @NO
                                             }];
        [reader addOutput:aout];
        [reader startReading];
        double decodedAudioEnd = 0;
        double energy[3] = {0};
        long counts[3] = {0};
        while ((sample = [aout copyNextSampleBuffer])) {
            CMBlockBufferRef block = CMSampleBufferGetDataBuffer(sample);
            size_t length = CMBlockBufferGetDataLength(block);
            float *data = malloc(length);
            CMBlockBufferCopyDataBytes(block, 0, length, data);
            double start = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample));
            decodedAudioEnd = start + CMTimeGetSeconds(CMSampleBufferGetDuration(sample));
            for (size_t i = 0; i < length / 8; i++) {
                double t = start + i / 48000.;
                int bin = t < .15 ? 0 : ((t > .35 && t < .5) ? 1 : ((t > .75 && t < .9) ? 2 : -1));
                if (bin >= 0) {
                    energy[bin] += fabs(data[2 * i]);
                    counts[bin]++;
                }
            }
            free(data);
            CFRelease(sample);
        }
        if (reader.status == AVAssetReaderStatusFailed) {
            die(reader.error);
        }
        emit(@{
            @"video_codec" : fourcc(CMFormatDescriptionGetMediaSubType(
                (__bridge CMFormatDescriptionRef)video.formatDescriptions.firstObject)),
            @"audio_codec" : fourcc(CMFormatDescriptionGetMediaSubType(
                (__bridge CMFormatDescriptionRef)audio.formatDescriptions.firstObject)),
            @"width" : @(video.naturalSize.width),
            @"height" : @(video.naturalSize.height),
            @"decoded_frames" : @(decodedFrames),
            @"first_keyframe" : @(firstKey),
            @"audio_duration" : @(CMTimeGetSeconds(audio.timeRange.duration)),
            @"decoded_audio_end" : @(decodedAudioEnd),
            @"frames" : @(pts.count),
            @"pts" : pts,
            @"video_end" : @(end),
            @"last_duration" : @(last),
            @"duration" : @(CMTimeGetSeconds(asset.duration)),
            @"size_bytes" : result[@"size_bytes"],
            @"early_energy" : @(energy[0] / MAX(1, counts[0])),
            @"tone_energy" : @(energy[1] / MAX(1, counts[1])),
            @"late_energy" : @(energy[2] / MAX(1, counts[2]))
        });
    }
    return 0;
}
