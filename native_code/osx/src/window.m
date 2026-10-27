#include "window.h"
#import "window_internal.h"
#include <libproc.h>
#include <pthread.h>
#include <time.h>
#include <math.h>
#include <limits.h>

static const char *CaptureName = "scapkit.window_ref.v1";
static const char *AccessibleName = "scapkit.accessible_window_ref.v1";
static const char *SessionName = "scapkit.window_session.v1";
static const char *ElementName = "scapkit.window_element.v1";
static CFRunLoopRef actorLoop;
static dispatch_semaphore_t actorReady;
static NSLock *actorLock;
static NSUInteger pendingJobs;
static BOOL quarantined;
static BOOL writesQuarantined;
static double currentDeadline;
static uint64_t nextGeneration = 1; // actor only
#ifdef SCAPKIT_TESTING
static atomic_llong liveTargets, liveSessions, liveElements;
#endif

@interface SWJob : NSObject
@property NSCondition *condition;
@property NSDictionary *result;
@property BOOL done, started, abandoned;
@property double deadline;
@end
@implementation SWJob
@end

@implementation SWTarget
- (instancetype)init
{
    self = [super init];
#ifdef SCAPKIT_TESTING
    if (self) { atomic_fetch_add(&liveTargets, 1); }
#endif
    return self;
}
- (void)dealloc
{
    // Last references can disappear during Python DTO allocation failures.
    // Move all CF teardown to the actor; the block owns these +1 references.
    AXUIElementRef oldRoot = root;
    AXObserverRef oldObserver = observer;
    id oldWatch = watch;
    root = NULL;
    observer = NULL;
    if (oldRoot || oldObserver) {
        CFRunLoopPerformBlock(actorLoop, kCFRunLoopDefaultMode, ^{
            @autoreleasepool {
                if (oldObserver) {
                    CFRunLoopRemoveSource(actorLoop, AXObserverGetRunLoopSource(oldObserver), kCFRunLoopDefaultMode);
                    CFRelease(oldObserver);
                }
                if (oldRoot) { CFRelease(oldRoot); }
                (void)oldWatch;
            }
        });
        CFRunLoopWakeUp(actorLoop);
    }
#ifdef SCAPKIT_TESTING
    atomic_fetch_sub(&liveTargets, 1);
#endif
}
@end
@implementation SWReference
@end
@implementation SWSession
- (instancetype)init
{
    self = [super init];
    if (self) {
        atomic_init(&closing, false);
#ifdef SCAPKIT_TESTING
        atomic_fetch_add(&liveSessions, 1);
#endif
    }
    return self;
}
#ifdef SCAPKIT_TESTING
- (void)dealloc { atomic_fetch_sub(&liveSessions, 1); }
#endif
@end
@implementation SWElement
- (instancetype)init
{
    self = [super init];
#ifdef SCAPKIT_TESTING
    if (self) { atomic_fetch_add(&liveElements, 1); }
#endif
    return self;
}
- (void)dealloc
{
    AXUIElementRef old = element;
    element = NULL;
    if (old) {
        CFRunLoopPerformBlock(actorLoop, kCFRunLoopDefaultMode, ^{ CFRelease(old); });
        CFRunLoopWakeUp(actorLoop);
    }
#ifdef SCAPKIT_TESTING
    atomic_fetch_sub(&liveElements, 1);
#endif
}
@end

static void actor_noop(void *info) { (void)info; }
static BOOL initialize_actor(void)
{
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        actorReady = dispatch_semaphore_create(0);
        actorLock = [NSLock new];
        NSThread *thread = [[NSThread alloc] initWithBlock:^{
            @autoreleasepool {
                CFRunLoopSourceContext context = {0};
                context.perform = actor_noop;
                CFRunLoopSourceRef keepalive = CFRunLoopSourceCreate(NULL, 0, &context);
                actorLoop = (CFRunLoopRef)CFRetain(CFRunLoopGetCurrent());
                CFRunLoopAddSource(actorLoop, keepalive, kCFRunLoopDefaultMode);
                CFRelease(keepalive);
                dispatch_semaphore_signal(actorReady);
                // This process-wide, native-only actor is never replaced after a timeout.
                for (;;) {
                    @autoreleasepool { CFRunLoopRunInMode(kCFRunLoopDefaultMode, 60, false); }
                }
            }
        }];
        thread.name = @"scapkit.window.actor";
        [thread start];
    });
    if (dispatch_semaphore_wait(actorReady, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC))) { return NO; }
    dispatch_semaphore_signal(actorReady);
    return YES;
}

double SWNow(void)
{
    // CPython's macOS time.monotonic uses mach_absolute_time, as does
    // CLOCK_UPTIME_RAW (CLOCK_MONOTONIC has a different suspend epoch).
    struct timespec ts;
    clock_gettime(CLOCK_UPTIME_RAW, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}
BOOL SWDeadlineExpired(void) { return SWNow() >= currentDeadline; }
double SWRemaining(void) { return MAX(0, currentDeadline - SWNow()); }

NSDictionary *SWError(NSString *code, NSString *domain, id nativeCode,
                      NSString *dispatch, NSString *retry)
{
    return @{@"ok": @NO, @"error": @{@"code": code, @"native_domain": domain ?: (id)NSNull.null,
        @"native_code": nativeCode ?: NSNull.null, @"dispatch_state": dispatch, @"retry_hint": retry}};
}
NSDictionary *SWFail(NSString *code)
{
    NSString *retry = [code isEqual:@"permission_denied"] ? @"after_permission" :
        ([code hasPrefix:@"stale"] || [code isEqual:@"geometry_changed"] ? @"refresh" : @"never");
    return SWError(code, @"scapkit.window", nil, @"not_dispatched", retry);
}
NSDictionary *SWOK(id value) { return @{@"ok": @YES, @"value": value ?: NSNull.null}; }

NSDictionary *SWRun(double timeout, BOOL write, NSDictionary *(^work)(void))
{
    if (!initialize_actor()) { return SWFail(@"backend_busy"); }
    [actorLock lock];
    if (quarantined || (write && writesQuarantined) || pendingJobs >= 64) {
        [actorLock unlock];
        return SWFail(@"backend_busy");
    }
    ++pendingJobs;
    [actorLock unlock];
    SWJob *job = [SWJob new];
    job.condition = [NSCondition new];
    job.deadline = SWNow() + timeout;
    CFRunLoopPerformBlock(actorLoop, kCFRunLoopDefaultMode, ^{
        @autoreleasepool {
            [job.condition lock];
            BOOL cancelled = job.abandoned || SWNow() >= job.deadline;
            job.started = !cancelled;
            [job.condition unlock];
            currentDeadline = job.deadline;
            NSDictionary *result = cancelled ? SWFail(@"backend_busy") : work();
            [job.condition lock];
            job.result = result;
            job.done = YES;
            [actorLock lock];
            --pendingJobs;
            if (job.abandoned && job.started) { quarantined = NO; }
            [actorLock unlock];
            [job.condition broadcast];
            [job.condition unlock];
        }
    });
    CFRunLoopWakeUp(actorLoop);
    [job.condition lock];
    while (!job.done && SWNow() < job.deadline) {
        [job.condition waitUntilDate:[NSDate dateWithTimeIntervalSinceNow:MIN(0.05, job.deadline - SWNow())]];
    }
    NSDictionary *result;
    if (job.done) {
        result = job.result;
    } else {
        job.abandoned = YES;
        if (job.started) {
            [actorLock lock];
            quarantined = YES;
            if (write) { writesQuarantined = YES; }
            [actorLock unlock];
        }
        result = write && job.started ? SWError(@"outcome_unknown", @"AX", nil, @"unknown", @"never") : SWFail(@"backend_busy");
    }
    [job.condition unlock];
    return result;
}

void SWDispose(id object)
{
    // Coalesce GC cleanup on the existing runloop, never create a worker. The
    // block keeps the object alive through removal of observer callbacks.
    // A capsule cannot exist until the actor has initialized successfully.
    CFRunLoopPerformBlock(actorLoop, kCFRunLoopDefaultMode, ^{
        @autoreleasepool {
            if ([object isKindOfClass:SWSession.class]) { SWClose(object); }
            (void)object;
        }
    });
    CFRunLoopWakeUp(actorLoop);
}
BOOL SWClose(SWSession *s)
{
    s->closed = YES;
    BOOL cleaned = SWCaptureCleanup(s, SWRemaining() * 0.95);
    NSArray *targets = @[(s->captureTarget ?: (id)NSNull.null), (s->semanticTarget ?: (id)NSNull.null),
        (s->captureTarget ? (s->captureTarget->lifetime ?: (id)NSNull.null) : (id)NSNull.null)];
    for (id object in targets) {
        if (object == NSNull.null) { continue; }
        SWTarget *t = object;
        t->stale = YES;
        if (t->observer) {
            CFRunLoopRemoveSource(actorLoop, AXObserverGetRunLoopSource(t->observer), kCFRunLoopDefaultMode);
            CFRelease(t->observer); t->observer = NULL;
        }
        t->watch = nil;
        if (t->root) { CFRelease(t->root); t->root = NULL; }
        t->window = nil;
    }
    s->captureTarget = nil;
    s->semanticTarget = nil;
    s->fingerprint = nil;
    return cleaned;
}

NSString *SWString(id value)
{
    if (![value isKindOfClass:NSString.class] || [value lengthOfBytesUsingEncoding:NSUTF8StringEncoding] > 4096 ||
        [value rangeOfString:[NSString stringWithFormat:@"%C", (unichar)0]].location != NSNotFound) { return nil; }
    return value;
}
NSString *SWProcess(pid_t pid)
{
    struct proc_bsdinfo info = {0};
    if (proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info)) != sizeof(info) ||
        (!info.pbi_start_tvsec && !info.pbi_start_tvusec)) { return nil; }
    return [NSString stringWithFormat:@"%llu.%06llu", (unsigned long long)info.pbi_start_tvsec,
            (unsigned long long)info.pbi_start_tvusec];
}
BOOL SWGoodRect(CGRect r)
{
    return isfinite(r.origin.x) && isfinite(r.origin.y) && isfinite(r.size.width) && isfinite(r.size.height) &&
        fabs(r.origin.x) <= 1e12 && fabs(r.origin.y) <= 1e12 && r.size.width > 0 && r.size.height > 0 &&
        r.size.width <= 1e12 && r.size.height <= 1e12;
}
NSDictionary *SWRect(CGRect r)
{
    return @{@"x": @(r.origin.x), @"y": @(r.origin.y), @"width": @(r.size.width), @"height": @(r.size.height)};
}
NSDictionary *SWIdentity(SWSession *s)
{
    return @{@"generation": @(s->generation), @"process_instance": s->process, @"geometry_revision": @(s->revision)};
}
NSDictionary *SWInfo(SWReference *ref, NSString *title, NSString *nativeID, id visible)
{
    SWTarget *t = ref->target;
    t->generation = nextGeneration++;
    t->epoch = 1;
    ref->issued = SWNow();
    return @{@"ref": ref, @"platform": @"macos", @"native_id": nativeID, @"pid": @(t->pid),
        @"process_instance": t->process, @"generation": @(t->generation), @"title": title ?: (id)NSNull.null,
        @"bounds": SWRect(t->bounds), @"coordinate_unit": @"points", @"visible": visible ?: NSNull.null,
        @"minimized": t->minimized ?: NSNull.null, @"capture_support": ref->capture ? @"supported" : @"unsupported",
        @"inspect_support": ref->capture ? @"unknown" : @"supported"};
}
void SWInvalidate(SWTarget *t, BOOL destroyed)
{
    ++t->epoch;
    if (destroyed) { t->stale = YES; }
}
NSDictionary *SWValidate(SWSession *s)
{
    if (s->closed || atomic_load(&s->closing)) { return SWFail(@"session_closed"); }
    NSDictionary *failure = nil;
    if (s->captureTarget) { failure = SWValidateTarget(s->captureTarget); }
    if (!failure && s->semanticTarget) { failure = SWValidateTarget(s->semanticTarget); }
    if (failure) { return failure; }
    SWTarget *t = s->captureTarget ?: s->semanticTarget;
    if (!t) { return SWFail(@"session_closed"); }
    if (!CGRectEqualToRect(s->lastBounds, t->bounds)) {
        ++s->revision;
        s->lastBounds = t->bounds;
    }
    return nil;
}

// Recursive native DTO conversion happens only on the attached Python entry
// thread. Cocoa objects in a completed result are immutable or actor-owned.
static void capsule_destructor(PyObject *capsule)
{
    const char *name = PyCapsule_GetName(capsule);
    void *ptr = name ? PyCapsule_GetPointer(capsule, name) : NULL;
    if (!ptr) { PyErr_Clear(); return; }
    @autoreleasepool {
        id object = CFBridgingRelease(ptr);
        Py_BEGIN_ALLOW_THREADS
        SWDispose(object);
        Py_END_ALLOW_THREADS
    }
}
static PyObject *to_python(id value)
{
    if (!value || value == NSNull.null) { Py_RETURN_NONE; }
    const char *name = NULL;
    if ([value isKindOfClass:SWReference.class]) { name = ((SWReference *)value)->capture ? CaptureName : AccessibleName; }
    if ([value isKindOfClass:SWSession.class]) { name = SessionName; }
    if ([value isKindOfClass:SWElement.class]) { name = ElementName; }
    if (name) {
        void *ptr = (void *)CFBridgingRetain(value);
        PyObject *capsule = PyCapsule_New(ptr, name, capsule_destructor);
        if (!capsule) {
            id owned = CFBridgingRelease(ptr);
            Py_BEGIN_ALLOW_THREADS
            SWDispose(owned);
            Py_END_ALLOW_THREADS
        }
        return capsule;
    }
    if ([value isKindOfClass:NSString.class]) {
        NSData *data = [value dataUsingEncoding:NSUTF8StringEncoding];
        return PyUnicode_DecodeUTF8(data.bytes, (Py_ssize_t)data.length, "strict");
    }
    if ([value isKindOfClass:NSData.class]) { return PyBytes_FromStringAndSize([value bytes], (Py_ssize_t)[value length]); }
    if ([value isKindOfClass:NSNumber.class]) {
        if (CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()) { return PyBool_FromLong([value boolValue]); }
        const char *type = [value objCType];
        if (strcmp(type, @encode(double)) == 0 || strcmp(type, @encode(float)) == 0) { return PyFloat_FromDouble([value doubleValue]); }
        return PyLong_FromLongLong([value longLongValue]);
    }
    if ([value isKindOfClass:NSArray.class]) {
        PyObject *list = PyList_New((Py_ssize_t)[value count]);
        if (!list) { return NULL; }
        for (NSUInteger i = 0; i < [value count]; ++i) {
            PyObject *item = to_python(value[i]);
            if (!item) { Py_DECREF(list); return NULL; }
            PyList_SET_ITEM(list, (Py_ssize_t)i, item);
        }
        return list;
    }
    if ([value isKindOfClass:NSDictionary.class]) {
        PyObject *dict = PyDict_New();
        if (!dict) { return NULL; }
        for (NSString *key in value) {
            PyObject *item = to_python(value[key]);
            if (!item || PyDict_SetItemString(dict, key.UTF8String, item) < 0) {
                Py_XDECREF(item); Py_DECREF(dict); return NULL;
            }
            Py_DECREF(item);
        }
        return dict;
    }
    PyErr_SetString(PyExc_RuntimeError, "unsupported internal window DTO");
    return NULL;
}
static id capsule(PyObject *value, const char *name)
{
    return (__bridge id)PyCapsule_GetPointer(value, name);
}
static NSString *text_arg(PyObject *value, Py_ssize_t max, BOOL empty)
{
    if (!PyUnicode_CheckExact(value)) { PyErr_SetString(PyExc_TypeError, "expected string"); return nil; }
    Py_ssize_t length;
    const char *s = PyUnicode_AsUTF8AndSize(value, &length);
    if (!s) { return nil; }
    if (length > max || (!empty && !length) || memchr(s, 0, (size_t)length)) {
        PyErr_SetString(PyExc_ValueError, "invalid string length or NUL"); return nil;
    }
    return [[NSString alloc] initWithBytes:s length:(NSUInteger)length encoding:NSUTF8StringEncoding];
}
static BOOL integer_arg(PyObject *value, long long min, long long max, long long *out)
{
    if (!PyLong_CheckExact(value)) { PyErr_SetString(PyExc_TypeError, "expected integer (not bool)"); return NO; }
    *out = PyLong_AsLongLong(value);
    if (PyErr_Occurred()) { return NO; }
    if (*out < min || *out > max) { PyErr_SetString(PyExc_ValueError, "integer out of range"); return NO; }
    return YES;
}
static BOOL timeout_arg(PyObject *value, double *out)
{
    if (!PyFloat_CheckExact(value) && !PyLong_CheckExact(value)) { PyErr_SetString(PyExc_TypeError, "expected timeout number"); return NO; }
    *out = PyFloat_AsDouble(value);
    if (PyErr_Occurred()) { return NO; }
    if (!isfinite(*out) || *out <= 0 || *out > 30) { PyErr_SetString(PyExc_ValueError, "timeout outside (0,30]"); return NO; }
    return YES;
}
static PyObject *run_python(double timeout, BOOL write, NSDictionary *(^work)(void))
{
    NSDictionary *result;
    Py_BEGIN_ALLOW_THREADS
    result = SWRun(timeout, write, work);
    Py_END_ALLOW_THREADS
    return to_python(result);
}

PyObject *scapkit_window_enumerate(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *kindArg, *pidArg, *limitArg;
        if (!PyArg_ParseTuple(args, "OOO", &kindArg, &pidArg, &limitArg)) { return NULL; }
        NSString *kind = text_arg(kindArg, 16, NO);
        long long pid = 0, limit;
        if (!kind || !integer_arg(limitArg, 1, 256, &limit)) { return NULL; }
        if (![kind isEqual:@"capture"] && ![kind isEqual:@"accessible"]) { PyErr_SetString(PyExc_ValueError, "invalid window kind"); return NULL; }
        if (pidArg != Py_None && !integer_arg(pidArg, 1, UINT32_MAX, &pid)) { return NULL; }
        if ([kind isEqual:@"accessible"] && !pid) { PyErr_SetString(PyExc_ValueError, "accessible enumeration requires pid"); return NULL; }
        return run_python(5, NO, ^{
            if (pid > INT_MAX) { return [kind isEqual:@"capture"] ? SWOK(@[]) : SWFail(@"identity_unverifiable"); }
            return [kind isEqual:@"capture"] ? SWCaptureEnumerate((pid_t)pid, (NSUInteger)limit) : SWAccessibleEnumerate((pid_t)pid, (NSUInteger)limit);
        });
    }
}
PyObject *scapkit_window_open(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *refArg, *kindArg;
        if (!PyArg_ParseTuple(args, "OO", &refArg, &kindArg)) { return NULL; }
        NSString *kind = text_arg(kindArg, 16, NO);
        if (!kind) { return NULL; }
        if (![kind isEqual:@"capture"] && ![kind isEqual:@"accessible"]) { PyErr_SetString(PyExc_ValueError, "invalid window kind"); return NULL; }
        SWReference *ref = capsule(refArg, [kind isEqual:@"capture"] ? CaptureName : AccessibleName);
        if (!ref) { return NULL; }
        return run_python(5, NO, ^{
            if (ref->consumed) { return SWFail(@"stale_window"); }
            ref->consumed = YES;
            if (SWNow() - ref->issued > 60) { return SWFail(@"stale_window"); }
            NSDictionary *failure = SWValidateTarget(ref->target);
            if (failure) { return failure; }
            SWSession *s = [SWSession new];
            s->captureTarget = ref->capture ? ref->target : nil;
            s->semanticTarget = ref->capture ? nil : ref->target;
            s->process = ref->target->process;
            s->generation = ref->target->generation;
            s->revision = 1;
            s->lastBounds = ref->target->bounds;
            return SWOK(@{@"handle": s, @"generation": @(s->generation), @"process_instance": s->process});
        });
    }
}
PyObject *scapkit_window_close(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *arg;
        if (!PyArg_ParseTuple(args, "O", &arg)) { return NULL; }
        SWSession *s = capsule(arg, SessionName);
        if (!s) { return NULL; }
        atomic_store(&s->closing, true);
        return run_python(5, NO, ^{ return SWClose(s) ? SWOK(nil) : SWFail(@"backend_busy"); });
    }
}
PyObject *scapkit_window_attach(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *sessionArg, *refArg;
        if (!PyArg_ParseTuple(args, "OO", &sessionArg, &refArg)) { return NULL; }
        SWSession *s = capsule(sessionArg, SessionName);
        SWReference *ref = capsule(refArg, AccessibleName);
        if (!s || !ref) { return NULL; }
        return run_python(5, NO, ^{
            if (ref->consumed) { return SWFail(@"stale_window"); }
            ref->consumed = YES;
            if (SWNow() - ref->issued > 60) { return SWFail(@"stale_window"); }
            return SWAttach(s, ref->target);
        });
    }
}
PyObject *scapkit_window_screenshot(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *sessionArg, *formatArg, *qualityArg, *timeoutArg;
        if (!PyArg_ParseTuple(args, "OOOO", &sessionArg, &formatArg, &qualityArg, &timeoutArg)) { return NULL; }
        SWSession *s = capsule(sessionArg, SessionName);
        NSString *format = text_arg(formatArg, 4, NO);
        long long quality;
        double timeout;
        if (!s || !format || !integer_arg(qualityArg, 0, 100, &quality) || !timeout_arg(timeoutArg, &timeout)) { return NULL; }
        if (![format isEqual:@"jpeg"] && ![format isEqual:@"bgra"]) { PyErr_SetString(PyExc_ValueError, "invalid format"); return NULL; }
        return run_python(timeout, NO, ^{ return SWCapture(s, format, (int)quality, timeout); });
    }
}
PyObject *scapkit_window_inspect(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *sessionArg, *queryArg, *options;
        if (!PyArg_ParseTuple(args, "OOO", &sessionArg, &queryArg, &options)) { return NULL; }
        SWSession *s = capsule(sessionArg, SessionName);
        NSString *query = text_arg(queryArg, 32, NO);
        if (!s || !query) { return NULL; }
        if (!PyDict_CheckExact(options)) { PyErr_SetString(PyExc_TypeError, "expected options dict"); return NULL; }
        BOOL elements = [query isEqual:@"elements"], validate = [query isEqual:@"validate_snapshot"];
        if (!elements && !validate && ![query isEqual:@"capabilities"]) { PyErr_SetString(PyExc_ValueError, "invalid query"); return NULL; }
        long long first = 0, second = 0;
        if (PyDict_Size(options) != (elements || validate ? 2 : 0)) { PyErr_SetString(PyExc_ValueError, "invalid options"); return NULL; }
        if (elements || validate) {
            // Strong refs protect against direct callers mutating their dict on 3.14t.
            PyObject *a = PyMapping_GetItemString(options, elements ? "max_depth" : "tree_epoch");
            if (!a) { return NULL; }
            PyObject *b = PyMapping_GetItemString(options, elements ? "max_nodes" : "geometry_revision");
            if (!b) { Py_DECREF(a); return NULL; }
            BOOL valid = integer_arg(a, elements ? 0 : 1, elements ? 32 : LLONG_MAX, &first) &&
                         integer_arg(b, 1, elements ? 4096 : LLONG_MAX, &second);
            Py_DECREF(a); Py_DECREF(b);
            if (!valid) { return NULL; }
        }
        return run_python(5, NO, ^{
            if (elements) { return SWElements(s, (NSInteger)first, (NSInteger)second); }
            if (validate) { return SWValidateSnapshot(s, (uint64_t)first, (uint64_t)second) ?: SWOK(nil); }
            return SWContext(s);
        });
    }
}

static NSDictionary *copy_scope(PyObject *scope)
{
    // Copy via exact JSON scalar containers returned by the trusted validator.
    // JSON decoding is on this entry thread, never the actor/runloop.
    PyObject *json = PyImport_ImportModule("json");
    if (!json) { return nil; }
    PyObject *encoded = PyObject_CallMethod(json, "dumps", "O", scope);
    Py_DECREF(json);
    if (!encoded) { return nil; }
    NSString *text = text_arg(encoded, 65536, NO);
    Py_DECREF(encoded);
    if (!text) { return nil; }
    id result = [NSJSONSerialization JSONObjectWithData:[text dataUsingEncoding:NSUTF8StringEncoding] options:0 error:NULL];
    return [result isKindOfClass:NSDictionary.class] ? result : nil;
}
static PyObject *action_entry(PyObject *args, BOOL candidate)
{
    PyObject *sArg, *eArg, *epochArg, *revisionArg, *actionArg, *valueArg, *authArg, *opArg, *timeoutArg;
    if (!PyArg_ParseTuple(args, "OOOOOOOOO", &sArg, &eArg, &epochArg, &revisionArg, &actionArg, &valueArg, &authArg, &opArg, &timeoutArg)) { return NULL; }
    SWSession *s = capsule(sArg, SessionName);
    SWElement *e = capsule(eArg, ElementName);
    long long epoch, revision;
    double timeout;
    NSString *action = text_arg(actionArg, 16, NO), *op = text_arg(opArg, 128, NO);
    if (!s || !e || !action || !op || !integer_arg(epochArg, 1, LLONG_MAX, &epoch) ||
        !integer_arg(revisionArg, 1, LLONG_MAX, &revision) || !timeout_arg(timeoutArg, &timeout)) { return NULL; }
    NSString *value = nil;
    if ([action isEqual:@"set_value"]) { value = text_arg(valueArg, 65536, YES); if (!value) { return NULL; } }
    else if (![action isEqual:@"invoke"] || valueArg != Py_None) { PyErr_SetString(PyExc_ValueError, "invalid action/value"); return NULL; }
    NSDictionary *scope = nil;
    if (!candidate) {
        PyObject *module = PyImport_ImportModule("scapkit_computer_use.window_profiles");
        if (!module) { return NULL; }
        PyObject *authorized = PyObject_CallMethod(module, "_validate_authorization", "O", authArg);
        Py_DECREF(module);
        if (!authorized) {
            if (PyErr_ExceptionMatches(PyExc_MemoryError)) { return NULL; }
            PyErr_Clear(); return to_python(SWFail(@"background_not_verified"));
        }
        scope = copy_scope(authorized);
        Py_DECREF(authorized);
        if (!scope) { if (!PyErr_Occurred()) { PyErr_SetString(PyExc_ValueError, "invalid validated scope"); } return NULL; }
    }
    return run_python(timeout, YES, ^{ return SWAction(s, e, (uint64_t)epoch, (uint64_t)revision, action, value, scope, op, SWNow() + timeout, candidate); });
}
PyObject *scapkit_window_action(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool { return action_entry(args, NO); }
}

#ifdef SCAPKIT_TESTING
#include "window_test_helpers.h"
#endif
