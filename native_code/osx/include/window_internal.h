#ifndef SCAPKIT_WINDOW_INTERNAL_H
#define SCAPKIT_WINDOW_INTERNAL_H
#import <Foundation/Foundation.h>
#import <ApplicationServices/ApplicationServices.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#include <stdint.h>
#include <stdatomic.h>

// All target/session/element fields and AX resources belong to the one bounded
// runloop actor. Python capsules retain immutable pointers to these objects.
// No interpreter objects, callbacks or reference counts cross this boundary.
@interface SWTarget : NSObject {
@public
    SCWindow *window;
    SWTarget *lifetime;
    AXUIElementRef root;
    AXObserverRef observer;
    id watch;
    pid_t pid;
    NSString *process, *identifier, *bridge;
    NSDictionary *subject;
    CGRect bounds;
    uint64_t generation, epoch;
    BOOL stale, monitoring, interfered;
    id minimized, modal;
#ifdef SCAPKIT_TESTING
    BOOL synthetic;
    NSString *liveProcess;
    NSArray *nodes;
#endif
}
@end

@interface SWReference : NSObject {
@public
    SWTarget *target;
    BOOL consumed, capture;
    double issued;
}
@end

@interface SWSession : NSObject {
@public
    SWTarget *captureTarget, *semanticTarget;
    uint64_t generation, revision, treeEpoch;
    NSString *process;
    CGRect lastBounds;
    BOOL closed, uncertain, revoked;
    atomic_bool closing;
    NSArray *fingerprint;
    id captureFlight; // native-only late-start/stop owner, retained until cleanup
    NSInteger depthLimit, nodeLimit;
    double snapshotTime;
}
@end

@interface SWElement : NSObject {
@public
    SWSession *owner;
    SWTarget *target;
    AXUIElementRef element;
    NSString *rawIdentifier;
    uint64_t epoch;
#ifdef SCAPKIT_TESTING
    NSDictionary *syntheticNode;
#endif
}
@end

NSDictionary *SWOK(id value);
NSDictionary *SWFail(NSString *code);
NSDictionary *SWError(NSString *code, NSString *domain, id nativeCode,
                      NSString *dispatch, NSString *retry);
double SWNow(void);
NSDictionary *SWRect(CGRect rect);
BOOL SWGoodRect(CGRect rect);
NSString *SWProcess(pid_t pid);
NSString *SWString(id value);
NSDictionary *SWIdentity(SWSession *session);
NSDictionary *SWValidate(SWSession *session);
NSDictionary *SWValidateTarget(SWTarget *target);
NSDictionary *SWCaptureEnumerate(pid_t pid, NSUInteger limit);
NSDictionary *SWAccessibleEnumerate(pid_t pid, NSUInteger limit);
NSDictionary *SWContext(SWSession *session);
NSDictionary *SWElements(SWSession *session, NSInteger depth, NSInteger count);
NSDictionary *SWValidateSnapshot(SWSession *session, uint64_t epoch, uint64_t revision);
NSDictionary *SWAttach(SWSession *session, SWTarget *target);
NSDictionary *SWAction(SWSession *session, SWElement *element, uint64_t epoch,
    uint64_t revision, NSString *action, NSString *value, NSDictionary *scope,
    NSString *operation, double deadline, BOOL candidate);
NSDictionary *SWCapture(SWSession *session, NSString *format, int quality, double timeout);
NSDictionary *SWMapping(NSDictionary *metadata, size_t width, size_t height, CGRect bounds);
NSDictionary *SWInfo(SWReference *reference, NSString *title, NSString *nativeID, id visible);
// Invoke with detached Python thread state. Expired jobs are never dispatched;
// an overdue in-flight job quarantines the actor until its native return.
NSDictionary *SWRun(double timeout, BOOL write, NSDictionary *(^work)(void));
void SWDispose(id object);
BOOL SWClose(SWSession *session);
BOOL SWCaptureCleanup(SWSession *session, double timeout);
BOOL SWDeadlineExpired(void);
double SWRemaining(void);
void SWInvalidate(SWTarget *target, BOOL destroyed);
BOOL SWFixtureBridge(SWTarget *target);
NSDictionary *SWAXRefresh(SWTarget *target);
BOOL SWCaptureRefresh(SWTarget *target);
#ifdef SCAPKIT_TESTING
NSDictionary *SWTestFrame(NSString *mode);
NSDictionary *SWTestAXTransport(NSString *mode, NSInteger depth, NSInteger count);
#endif
#endif
