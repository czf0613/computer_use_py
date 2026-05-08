#include "capture.h"
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>
#import <CoreImage/CoreImage.h>
#import <ImageIO/ImageIO.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#include <dispatch/dispatch.h>

typedef struct scapkit_handle_t
{
    SCStream *stream;
    dispatch_queue_t queue;
    CVPixelBufferRef current_frame;
    id delegate;
} scapkit_handle;

@interface ScapkitStreamOutput : NSObject <SCStreamOutput>
@property (assign) scapkit_handle *handle;
@end

@implementation ScapkitStreamOutput

- (void)stream:(SCStream *)stream didOutputSampleBuffer:(CMSampleBufferRef)sampleBuffer ofType:(SCStreamOutputType)type
{
    if (type != SCStreamOutputTypeScreen)
    {
        return;
    }

    CVPixelBufferRef pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer);
    if (!pixelBuffer)
    {
        return;
    }

    CVPixelBufferRetain(pixelBuffer);
    CVPixelBufferRef old = self.handle->current_frame;
    self.handle->current_frame = pixelBuffer;
    if (old)
    {
        CVPixelBufferRelease(old);
    }
}

@end

static void capsule_destructor(PyObject *capsule)
{
    scapkit_handle *h = (scapkit_handle *)PyCapsule_GetPointer(capsule, "scapkit_handle");
    if (!h)
    {
        return;
    }

    if (h->stream)
    {
        dispatch_semaphore_t sem = dispatch_semaphore_create(0);
        [h->stream stopCaptureWithCompletionHandler:^(NSError *error) {
            dispatch_semaphore_signal(sem);
        }];
        dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
        h->stream = nil;
    }

    dispatch_sync(h->queue, ^{
        if (h->current_frame)
        {
            CVPixelBufferRelease(h->current_frame);
            h->current_frame = NULL;
        }
    });

    h->delegate = nil;
    free(h);
}

PyObject *scapkit_start_capture(PyObject *self, PyObject *args)
{
    unsigned long display_id;
    PyArg_ParseTuple(args, "k", &display_id);

    __block SCDisplay *target_display = nil;
    __block NSError *content_error = nil;

    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    [SCShareableContent getShareableContentExcludingDesktopWindows:NO
                                              onScreenWindowsOnly:NO
                                              completionHandler:^(SCShareableContent *content, NSError *error) {
        if (error)
        {
            content_error = error;
            dispatch_semaphore_signal(sem);
            return;
        }
        for (SCDisplay *d in content.displays)
        {
            if (d.displayID == (CGDirectDisplayID)display_id)
            {
                target_display = d;
                break;
            }
        }
        dispatch_semaphore_signal(sem);
    }];
    dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));

    if (content_error)
    {
        PyErr_Format(PyExc_OSError, "SCShareableContent failed: %s",
                     [[content_error localizedDescription] UTF8String]);
        return NULL;
    }

    if (!target_display)
    {
        PyErr_Format(PyExc_ValueError, "display %lu not found", display_id);
        return NULL;
    }

    SCContentFilter *filter = [[SCContentFilter alloc] initWithDisplay:target_display excludingWindows:@[]];

    CGDisplayModeRef mode = CGDisplayCopyDisplayMode((CGDirectDisplayID)display_id);
    size_t pixel_width = CGDisplayModeGetPixelWidth(mode);
    size_t pixel_height = CGDisplayModeGetPixelHeight(mode);
    CGDisplayModeRelease(mode);

    SCStreamConfiguration *config = [[SCStreamConfiguration alloc] init];
    config.width = pixel_width;
    config.height = pixel_height;
    config.pixelFormat = kCVPixelFormatType_32BGRA;
    config.showsCursor = YES;

    scapkit_handle *handle = calloc(1, sizeof(scapkit_handle));

    ScapkitStreamOutput *output = [[ScapkitStreamOutput alloc] init];
    output.handle = handle;
    handle->delegate = output;

    handle->queue = dispatch_queue_create("com.scapkit.capture", DISPATCH_QUEUE_SERIAL);

    SCStream *stream = [[SCStream alloc] initWithFilter:filter configuration:config delegate:nil];
    handle->stream = stream;

    NSError *addError = nil;
    [stream addStreamOutput:output type:SCStreamOutputTypeScreen sampleHandlerQueue:handle->queue error:&addError];
    if (addError)
    {
        free(handle);
        PyErr_Format(PyExc_OSError, "addStreamOutput failed: %s",
                     [[addError localizedDescription] UTF8String]);
        return NULL;
    }

    __block NSError *start_error = nil;
    dispatch_semaphore_t start_sem = dispatch_semaphore_create(0);
    [stream startCaptureWithCompletionHandler:^(NSError *error) {
        start_error = error;
        dispatch_semaphore_signal(start_sem);
    }];
    dispatch_semaphore_wait(start_sem, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));

    if (start_error)
    {
        free(handle);
        PyErr_Format(PyExc_OSError, "startCapture failed: %s",
                     [[start_error localizedDescription] UTF8String]);
        return NULL;
    }

    return PyCapsule_New(handle, "scapkit_handle", capsule_destructor);
}

PyObject *scapkit_stop_capture(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    PyArg_ParseTuple(args, "O", &capsule);

    scapkit_handle *h = (scapkit_handle *)PyCapsule_GetPointer(capsule, "scapkit_handle");
    if (!h)
    {
        return NULL;
    }

    if (h->stream)
    {
        __block NSError *stop_error = nil;
        dispatch_semaphore_t sem = dispatch_semaphore_create(0);
        [h->stream stopCaptureWithCompletionHandler:^(NSError *error) {
            stop_error = error;
            dispatch_semaphore_signal(sem);
        }];
        dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));

        h->stream = nil;

        if (stop_error)
        {
            PyErr_Format(PyExc_OSError, "stopCapture failed: %s",
                         [[stop_error localizedDescription] UTF8String]);
            return NULL;
        }
    }

    dispatch_sync(h->queue, ^{
        if (h->current_frame)
        {
            CVPixelBufferRelease(h->current_frame);
            h->current_frame = NULL;
        }
    });

    PyCapsule_SetDestructor(capsule, NULL);
    free(h);

    Py_RETURN_NONE;
}

PyObject *scapkit_current_frame_bgra(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    PyArg_ParseTuple(args, "O", &capsule);

    scapkit_handle *h = (scapkit_handle *)PyCapsule_GetPointer(capsule, "scapkit_handle");
    if (!h)
    {
        return NULL;
    }

    __block CVPixelBufferRef frame = NULL;
    dispatch_sync(h->queue, ^{
        frame = h->current_frame;
        if (frame)
        {
            CVPixelBufferRetain(frame);
        }
    });

    if (!frame)
    {
        Py_RETURN_NONE;
    }

    CVPixelBufferLockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);

    size_t width = CVPixelBufferGetWidth(frame);
    size_t height = CVPixelBufferGetHeight(frame);
    size_t bytes_per_row = CVPixelBufferGetBytesPerRow(frame);
    void *base = CVPixelBufferGetBaseAddress(frame);

    size_t data_size = bytes_per_row * height;
    PyObject *result = Py_BuildValue("{s:y#, s:n, s:n, s:n}",
                                     "data", (const char *)base, (Py_ssize_t)data_size,
                                     "width", (Py_ssize_t)width,
                                     "height", (Py_ssize_t)height,
                                     "bytes_per_row", (Py_ssize_t)bytes_per_row);

    CVPixelBufferUnlockBaseAddress(frame, kCVPixelBufferLock_ReadOnly);
    CVPixelBufferRelease(frame);

    return result;
}

PyObject *scapkit_current_frame_jpg(PyObject *self, PyObject *args)
{
    PyObject *capsule;
    int quality = 80;
    PyArg_ParseTuple(args, "O|i", &capsule, &quality);

    scapkit_handle *h = (scapkit_handle *)PyCapsule_GetPointer(capsule, "scapkit_handle");
    if (!h)
    {
        return NULL;
    }

    __block CVPixelBufferRef frame = NULL;
    dispatch_sync(h->queue, ^{
        frame = h->current_frame;
        if (frame)
        {
            CVPixelBufferRetain(frame);
        }
    });

    if (!frame)
    {
        Py_RETURN_NONE;
    }

    CIImage *ciImage = [CIImage imageWithCVPixelBuffer:frame];
    CVPixelBufferRelease(frame);

    CIContext *ctx = [CIContext context];
    CGColorSpaceRef colorSpace = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);

    NSMutableData *jpegData = [NSMutableData data];
    CGImageDestinationRef dest = CGImageDestinationCreateWithData(
        (__bridge CFMutableDataRef)jpegData, (__bridge CFStringRef)UTTypeJPEG.identifier, 1, NULL);

    if (!dest)
    {
        CGColorSpaceRelease(colorSpace);
        PyErr_SetString(PyExc_OSError, "failed to create JPEG image destination");
        return NULL;
    }

    CGImageRef cgImage = [ctx createCGImage:ciImage fromRect:ciImage.extent];
    NSDictionary *props = @{(__bridge NSString *)kCGImageDestinationLossyCompressionQuality: @(quality / 100.0)};
    CGImageDestinationAddImage(dest, cgImage, (__bridge CFDictionaryRef)props);
    CGImageDestinationFinalize(dest);

    CGImageRelease(cgImage);
    CFRelease(dest);
    CGColorSpaceRelease(colorSpace);

    PyObject *result = PyBytes_FromStringAndSize((const char *)jpegData.bytes, (Py_ssize_t)jpegData.length);
    return result;
}
