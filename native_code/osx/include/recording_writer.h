#ifndef SCAPKIT_RECORDING_WRITER_H
#define SCAPKIT_RECORDING_WRITER_H
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
/* Calls are serial, with Python thread state detached. Accepted buffers are retained
 * or copied until asynchronous work completes. At most two video requests are in
 * flight; finishAt: drains them before publishing. This unit never calls Python. */
@interface ScapkitRecordingWriter : NSObject
- (nullable instancetype)initWithPath:(NSString *)path
                                width:(int)width
                               height:(int)height
                                  fps:(int)fps
                                error:(NSError **)error;
/* videoQuality: -1 leaves encoder defaults; 0..1 requests supported quality mode. */
- (nullable instancetype)initWithPath:(NSString *)path
                                width:(int)width
                               height:(int)height
                                  fps:(int)fps
                         videoQuality:(double)videoQuality
                                error:(NSError **)error;
- (BOOL)appendVideo:(CVPixelBufferRef)frame
               time:(CMTime)time
           duration:(CMTime)duration
              error:(NSError **)error;
- (BOOL)appendAudio:(CMSampleBufferRef)sample origin:(CMTime)origin error:(NSError **)error;
- (nullable NSDictionary *)finishAt:(CMTime)endTime error:(NSError **)error;
/* Thread-safe with append/finish. Returns the immutable committed result if
 * publication already won; otherwise closes the gate and returns nil. After
 * this returns, no new final file can be published. Does not call codec APIs. */
- (nullable NSDictionary *)preventPublication;
- (void)cancel;
@end
NS_ASSUME_NONNULL_END
#endif
