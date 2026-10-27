#import "window_internal.h"
#import "window_fixture_identity.h"
#import <Security/Security.h>
#import <CommonCrypto/CommonDigest.h>
#include <libproc.h>
#include <sys/stat.h>
#include <sys/sysctl.h>
#include <sys/utsname.h>
#include <fcntl.h>
#include <unistd.h>
#include <dlfcn.h>
#include <math.h>
#ifdef SCAPKIT_TESTING
#include "window_ax_test_transport.h"
#endif

static id ax_copy(AXUIElementRef element, CFStringRef attribute)
{
    CFTypeRef value = NULL;
    if (!element || SWDeadlineExpired()) { return nil; }
    AXError error = AXUIElementCopyAttributeValue(element, attribute, &value);
    if (error != kAXErrorSuccess) { if (value) { CFRelease(value); } return nil; }
    return CFBridgingRelease(value);
}
static id ax_bool(AXUIElementRef e, CFStringRef attr)
{
    id value = ax_copy(e, attr);
    return value && CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID() ? value : (id)NSNull.null;
}
static CGRect ax_bounds(AXUIElementRef e)
{
    id position = ax_copy(e, kAXPositionAttribute), size = ax_copy(e, kAXSizeAttribute);
    CGPoint p = {0}; CGSize s = {0};
    if (!position || !size || CFGetTypeID((__bridge CFTypeRef)position) != AXValueGetTypeID() ||
        CFGetTypeID((__bridge CFTypeRef)size) != AXValueGetTypeID() ||
        !AXValueGetValue((__bridge AXValueRef)position, kAXValueCGPointType, &p) ||
        !AXValueGetValue((__bridge AXValueRef)size, kAXValueCGSizeType, &s)) { return CGRectNull; }
    return (CGRect){p, s};
}
static NSArray *ax_children(AXUIElementRef e, CFStringRef attribute, NSUInteger cap, BOOL *overflow)
{
    CFIndex count = 0;
    if (SWDeadlineExpired()) { return nil; }
    AXError error = AXUIElementGetAttributeValueCount(e, attribute, &count);
    // A leaf may legally have no AXChildren attribute. IPC/invalid-object
    // failures are not leaves, nor is a failed values read after a valid count.
    if (error == kAXErrorAttributeUnsupported && CFEqual(attribute, kAXChildrenAttribute)) { return @[]; }
    if (error != kAXErrorSuccess || count < 0) { return nil; }
    if ((NSUInteger)count > cap && overflow) { *overflow = YES; }
    count = MIN(count, (CFIndex)cap);
    if (!count) { return @[]; }
    CFArrayRef values = NULL;
    if (AXUIElementCopyAttributeValues(e, attribute, 0, count, &values) != kAXErrorSuccess) {
        if (values) { CFRelease(values); } return nil;
    }
    if (!values || CFGetTypeID(values) != CFArrayGetTypeID() || CFArrayGetCount(values) != count) {
        if (values) { CFRelease(values); } return nil;
    }
    NSArray *result = CFBridgingRelease(values);
    for (id value in result) {
        if (CFGetTypeID((__bridge CFTypeRef)value) != AXUIElementGetTypeID()) { return nil; }
    }
    return result;
}
static AXUIElementRef application(pid_t pid)
{
    AXUIElementRef app = AXUIElementCreateApplication(pid);
    if (app) { AXUIElementSetMessagingTimeout(app, 0.2f); }
    return app;
}

@interface SWWatch : NSObject
@property(weak) SWTarget *target;
@end
@implementation SWWatch
@end
static void observed(AXObserverRef observer, AXUIElementRef element, CFStringRef notification, void *context)
{
    (void)observer;
    @autoreleasepool {
        SWWatch *watch = (__bridge SWWatch *)context;
        SWTarget *t = watch.target;
        if (t) {
            BOOL destroyed = CFEqual(notification, kAXUIElementDestroyedNotification) && CFEqual(element, t->root);
            SWInvalidate(t, destroyed);
        }
    }
}
static BOOL watch_root(SWTarget *t)
{
    AXObserverRef obs = NULL;
    if (AXObserverCreate(t->pid, observed, &obs) != kAXErrorSuccess) { return NO; }
    SWWatch *watch = [SWWatch new];
    watch.target = t;
    if (AXObserverAddNotification(obs, t->root, kAXUIElementDestroyedNotification, (__bridge void *)watch) != kAXErrorSuccess) {
        CFRelease(obs); return NO;
    }
    const CFStringRef optional[] = {kAXMovedNotification, kAXResizedNotification, kAXLayoutChangedNotification,
        kAXWindowMiniaturizedNotification, kAXWindowDeminiaturizedNotification};
    for (NSUInteger i = 0; i < sizeof(optional) / sizeof(optional[0]); ++i) {
        AXObserverAddNotification(obs, t->root, optional[i], (__bridge void *)watch);
    }
    t->observer = obs;
    t->watch = watch;
    t->monitoring = YES;
    CFRunLoopAddSource(CFRunLoopGetCurrent(), AXObserverGetRunLoopSource(obs), kCFRunLoopDefaultMode);
    return YES;
}

static NSString *hash_file(const char *path)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) { return nil; }
    struct stat before, after;
    if (fstat(fd, &before) || !S_ISREG(before.st_mode) || before.st_size < 1 || before.st_size > 64 * 1024 * 1024) { close(fd); return nil; }
    CC_SHA256_CTX hash;
    CC_SHA256_Init(&hash);
    unsigned char buffer[32768];
    off_t total = 0;
    BOOL valid = YES;
    for (;;) {
        ssize_t n = read(fd, buffer, sizeof(buffer));
        if (n < 0 && errno == EINTR) { continue; }
        if (n < 0) { valid = NO; break; }
        if (n == 0) { break; }
        total += n;
        if (total > before.st_size) { valid = NO; break; }
        CC_SHA256_Update(&hash, buffer, (CC_LONG)n);
    }
    if (fstat(fd, &after) || total != before.st_size || before.st_mtimespec.tv_sec != after.st_mtimespec.tv_sec ||
        before.st_mtimespec.tv_nsec != after.st_mtimespec.tv_nsec || before.st_size != after.st_size) { valid = NO; }
    close(fd);
    if (!valid) { return nil; }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &hash);
    NSMutableString *result = [NSMutableString stringWithCapacity:64];
    for (NSUInteger i = 0; i < sizeof(digest); ++i) { [result appendFormat:@"%02x", digest[i]]; }
    return result;
}
static NSDictionary *fixture_metadata(SWTarget *t)
{
    NSString *help = SWString(ax_copy(t->root, kAXHelpAttribute));
    if (!help) { return nil; }
    id m = [NSJSONSerialization JSONObjectWithData:[help dataUsingEncoding:NSUTF8StringEncoding] options:0 error:NULL];
    if (![m isKindOfClass:NSDictionary.class] || ![m[@"schema"] isEqual:@"scapkit.fixture.bridge.v1"] ||
        ![m[@"source"] isEqual:@SW_FIXTURE_SOURCE] || ![m[@"version"] isEqual:@"window-fixture-v1"] ||
        ![m[@"process_start"] isEqual:t->process]) { return nil; }
    for (NSString *key in @[@"pid", @"window_number"]) {
        id v = m[key];
        if (![v isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)v) == CFBooleanGetTypeID() ||
            [v doubleValue] != [v longLongValue] || [v longLongValue] <= 0 || [v unsignedLongLongValue] > UINT32_MAX) { return nil; }
    }
    if ([m[@"pid"] intValue] != t->pid) { return nil; }
    for (NSString *key in @[@"modal", @"closed"]) {
        if (!m[key] || CFGetTypeID((__bridge CFTypeRef)m[key]) != CFBooleanGetTypeID()) { return nil; }
    }
    if ([m[@"closed"] boolValue]) { return nil; }
    NSString *slot = m[@"slot"], *instance = SWString(m[@"instance"]), *processToken = SWString(m[@"process_token"]);
    if (![@[@"A1", @"A2", @"B"] containsObject:slot] || !instance || !processToken ||
        ![[NSUUID alloc] initWithUUIDString:instance] || ![[NSUUID alloc] initWithUUIDString:processToken]) { return nil; }
    NSString *expected = [NSString stringWithFormat:@"scapkit.fixture.%@.%@.window", slot, instance];
    if (![t->identifier isEqual:expected]) { return nil; }
    return m;
}
BOOL SWFixtureBridge(SWTarget *t)
{
    NSDictionary *m = fixture_metadata(t);
    if (!m) { return NO; }
    NSString *marker = [NSString stringWithFormat:@"%@:%@:%@", m[@"process_token"], m[@"instance"], m[@"window_number"]];
    if (t->bridge) {
        if (![t->bridge isEqual:marker]) { t->stale = YES; return NO; }
        t->modal = m[@"modal"];
        return YES;
    }
    char path[PROC_PIDPATHINFO_MAXSIZE];
    if (proc_pidpath(t->pid, path, sizeof(path)) <= 0) { return NO; }
    NSString *executable = hash_file(path);
    if (![executable isEqual:@SW_FIXTURE_EXECUTABLE]) { return NO; }
    SecCodeRef code = NULL;
    NSDictionary *attributes = @{(__bridge NSString *)kSecGuestAttributePid: @(t->pid)};
    OSStatus status = SecCodeCopyGuestWithAttributes(NULL, (__bridge CFDictionaryRef)attributes, kSecCSDefaultFlags, &code);
    if (status != errSecSuccess || !code) { if (code) { CFRelease(code); } return NO; }
    status = SecCodeCheckValidity(code, kSecCSDefaultFlags, NULL);
    CFRelease(code);
    if (status != errSecSuccess || ![SWProcess(t->pid) isEqual:t->process]) { return NO; }
    Dl_info image;
    if (!dladdr((const void *)SWAction, &image)) { return NO; }
    NSString *backend = hash_file(image.dli_fname);
    char os[128] = {0}; size_t length = sizeof(os);
    struct utsname arch;
    if (!backend || sysctlbyname("kern.osversion", os, &length, NULL, 0) || uname(&arch)) { return NO; }
    t->subject = @{@"platform": @"macos", @"application_id": @"org.scapkit.window-fixture",
        @"application_version": @"window-fixture-v1", @"application_build": [@"sha256:" stringByAppendingString:executable],
        @"environment": [NSString stringWithFormat:@"macos:%s:%s", os, arch.machine],
        @"backend_build": [@"sha256:" stringByAppendingString:backend], @"window_identifier": @"fixture.window.v1"};
    t->bridge = marker;
    t->modal = m[@"modal"];
    return YES;
}

NSDictionary *SWAXRefresh(SWTarget *t)
{
    if (!AXIsProcessTrusted()) { return SWFail(@"permission_denied"); }
    if (!t->monitoring || !t->root || t->stale) { t->stale = YES; return SWFail(@"stale_window"); }
    AXUIElementRef app = application(t->pid);
    BOOL overflow = NO;
    NSArray *windows = app ? ax_children(app, kAXWindowsAttribute, 256, &overflow) : nil;
    if (app) { CFRelease(app); }
    if (!windows || overflow || ![windows containsObject:(__bridge id)t->root]) {
        t->stale = YES; return SWFail(@"stale_window");
    }
    NSString *identifier = SWString(ax_copy(t->root, kAXIdentifierAttribute));
    if (![(identifier ?: @"") isEqual:(t->identifier ?: @"")]) { t->stale = YES; return SWFail(@"stale_window"); }
    CGRect r = ax_bounds(t->root);
    if (!SWGoodRect(r)) { return SWFail(@"identity_unverifiable"); }
    if (!CGRectEqualToRect(t->bounds, r)) { SWInvalidate(t, NO); t->bounds = r; }
    t->minimized = ax_bool(t->root, kAXMinimizedAttribute);
    t->modal = ax_bool(t->root, kAXModalAttribute);
    if (t->bridge && !SWFixtureBridge(t)) { t->stale = YES; return SWFail(@"stale_window"); }
    return nil;
}
NSDictionary *SWValidateTarget(SWTarget *t)
{
    if (t->stale) { return SWFail(@"stale_window"); }
    NSString *now;
#ifdef SCAPKIT_TESTING
    if (t->synthetic) { now = t->liveProcess; }
    else
#endif
    { now = SWProcess(t->pid); }
    if (!now || ![now isEqual:t->process]) { t->stale = YES; return SWFail(@"stale_window"); }
#ifdef SCAPKIT_TESTING
    if (t->synthetic) { return nil; }
#endif
    if (t->window && !CGPreflightScreenCaptureAccess()) { return SWFail(@"permission_denied"); }
    if (t->window && !SWCaptureRefresh(t)) { t->stale = YES; return SWFail(@"stale_window"); }
    if (t->root) { return SWAXRefresh(t); }
    return nil;
}
NSDictionary *SWAccessibleEnumerate(pid_t pid, NSUInteger limit)
{
    if (!AXIsProcessTrusted()) { return SWFail(@"permission_denied"); }
    NSString *process = SWProcess(pid);
    if (!process) { return SWFail(@"identity_unverifiable"); }
    AXUIElementRef app = application(pid);
    BOOL overflow = NO;
    NSArray *roots = app ? ax_children(app, kAXWindowsAttribute, limit, &overflow) : nil;
    if (app) { CFRelease(app); }
    if (overflow) { return SWFail(@"resource_limit"); }
    if (!roots) { return SWFail(@"identity_unverifiable"); }
    NSMutableArray *result = [NSMutableArray array];
    for (id root in roots) {
        if (SWDeadlineExpired()) { return SWFail(@"backend_busy"); }
        SWTarget *t = [SWTarget new];
        t->root = (AXUIElementRef)CFRetain((__bridge CFTypeRef)root);
        AXUIElementSetMessagingTimeout(t->root, 0.2f);
        t->pid = pid; t->process = process;
        t->bounds = ax_bounds(t->root);
        t->identifier = SWString(ax_copy(t->root, kAXIdentifierAttribute));
        t->minimized = ax_bool(t->root, kAXMinimizedAttribute);
        t->modal = ax_bool(t->root, kAXModalAttribute);
        if (!SWGoodRect(t->bounds) || ![ax_copy(t->root, kAXRoleAttribute) isEqual:(__bridge NSString *)kAXWindowRole]) { continue; }
        if (!watch_root(t)) { return SWFail(@"identity_unverifiable"); }
        SWFixtureBridge(t); // no subject for ordinary applications; reads remain independent
        SWReference *ref = [SWReference new]; ref->target = t;
        [result addObject:SWInfo(ref, SWString(ax_copy(t->root, kAXTitleAttribute)), t->identifier ?: @"AXWindow", nil)];
    }
    return SWOK(result);
}
NSDictionary *SWAttach(SWSession *s, SWTarget *t)
{
    NSDictionary *failure = SWValidate(s) ?: SWValidateTarget(t);
    if (failure) { return failure; }
    if (!s->captureTarget || s->semanticTarget) { return SWFail(@"identity_unverifiable"); }
    if (s->captureTarget->pid != t->pid || ![s->process isEqual:t->process] || !SWFixtureBridge(t)) { return SWFail(@"identity_unverifiable"); }
    NSDictionary *m = fixture_metadata(t);
    if (!m || [m[@"window_number"] unsignedIntValue] != s->captureTarget->window.windowID) { return SWFail(@"identity_unverifiable"); }
    if (s->captureTarget->lifetime && ![s->captureTarget->lifetime->bridge isEqual:t->bridge]) { return SWFail(@"identity_unverifiable"); }
    // Metadata is unique to the exact root; never search by title, PID or bounds.
    s->semanticTarget = t;
    s->captureTarget->lifetime = t;
    return SWOK(nil);
}
NSDictionary *SWContext(SWSession *s)
{
    NSDictionary *failure = SWValidate(s);
    if (failure) { return failure; }
    SWTarget *t = s->semanticTarget;
    SWTarget *current = t ?: s->captureTarget;
    if (!current) { return SWFail(@"session_closed"); }
    NSMutableDictionary *d = [SWIdentity(s) mutableCopy];
    [d addEntriesFromDictionary:@{@"identity_status": @"verified", @"capture": s->captureTarget ? @"supported" : @"unsupported",
        @"inspect_elements": t ? @"supported" : @"unsupported", @"set_value": t ? @"supported" : @"unsupported",
        @"invoke": t ? @"supported" : @"unsupported", @"minimized": current->minimized ?: (id)NSNull.null,
        @"modal": t ? (t->modal ?: (id)NSNull.null) : (id)NSNull.null,
        @"focus_policy": t && t->subject ? @"no_activation" : @"unverified", @"subject": t ? (t->subject ?: (id)NSNull.null) : (id)NSNull.null}];
    return SWOK(d);
}

static NSDictionary *node_info(SWTarget *t, AXUIElementRef e)
{
    NSString *raw = SWString(ax_copy(e, kAXIdentifierAttribute));
    NSString *role = SWString(ax_copy(e, kAXRoleAttribute));
    if (!role || !role.length) { return nil; }
    NSString *identifier = raw;
    if (t->subject && raw && [t->identifier hasSuffix:@".window"]) {
        NSString *prefix = [t->identifier substringToIndex:t->identifier.length - @"window".length];
        // Only these exact instance-scoped identifiers may acquire stable semantics.
        if ([raw isEqual:[prefix stringByAppendingString:@"text"]]) { identifier = @"fixture.text"; }
        if ([raw isEqual:[prefix stringByAppendingString:@"increment"]]) { identifier = @"fixture.increment"; }
    }
    NSString *subrole = SWString(ax_copy(e, kAXSubroleAttribute));
    BOOL secure = [subrole isEqual:(__bridge NSString *)kAXSecureTextFieldSubrole];
    // Unknown roles/subroles cannot assert non-password state. The exact pinned
    // adapter proves its ordinary AXTextField and AXButton controls are not secrets.
    id password = secure ? @YES : (id)NSNull.null;
    if (t->subject && (([identifier isEqual:@"fixture.text"] && [role isEqual:(__bridge NSString *)kAXTextFieldRole]) ||
                      ([identifier isEqual:@"fixture.increment"] && [role isEqual:(__bridge NSString *)kAXButtonRole]))) { password = @NO; }
    Boolean settable = false;
    AXError writable = AXUIElementIsAttributeSettable(e, kAXValueAttribute, &settable);
    id editable = writable == kAXErrorSuccess ? (settable ? @YES : @NO) : (id)NSNull.null;
    CFArrayRef names = NULL;
    BOOL press = AXUIElementCopyActionNames(e, &names) == kAXErrorSuccess && names &&
        CFArrayContainsValue(names, CFRangeMake(0, CFArrayGetCount(names)), kAXPressAction);
    if (names) { CFRelease(names); }
    NSMutableArray *actions = [NSMutableArray array];
    if (writable == kAXErrorSuccess && settable && !secure) { [actions addObject:@"set_value"]; }
    if (press && !secure) { [actions addObject:@"invoke"]; }
    CGRect bounds = ax_bounds(e);
    id local = NSNull.null;
    if (SWGoodRect(bounds)) {
        bounds.origin.x -= t->bounds.origin.x; bounds.origin.y -= t->bounds.origin.y;
        local = SWRect(bounds);
    }
    return @{@"identifier": identifier ?: (id)NSNull.null, @"raw_identifier": raw ?: (id)NSNull.null,
        @"role": role, @"name": secure ? (id)NSNull.null : (SWString(ax_copy(e, kAXTitleAttribute)) ?: (id)NSNull.null),
        @"bounds": local, @"enabled": ax_bool(e, kAXEnabledAttribute), @"editable": editable,
        @"password": password, @"actions": actions};
}

static BOOL descends_from(AXUIElementRef element, AXUIElementRef root);
static BOOL belongs_to(AXUIElementRef element, AXUIElementRef root)
{
    if (CFEqual(element, root)) { return YES; }
    // AXChildren and AXParent need not be inverse for ignored AppKit groups.
    // AXWindow is the public retained root identity, not a numeric window-ID API.
    id window = ax_copy(element, kAXWindowAttribute);
    if (window) {
        return CFGetTypeID((__bridge CFTypeRef)window) == AXUIElementGetTypeID() && CFEqual((__bridge CFTypeRef)window, root);
    }
    return descends_from(element, root);
}
static NSString *treeFailure; // actor only; short symbolic diagnostics, never values
static NSArray *read_tree(SWSession *s, NSInteger depth, NSInteger cap, BOOL references)
{
    treeFailure = nil;
    SWTarget *t = s->semanticTarget;
#ifdef SCAPKIT_TESTING
    if (t->synthetic) {
        NSMutableArray *result = [NSMutableArray array];
        for (NSDictionary *node in t->nodes) {
            if ([node[@"depth"] integerValue] > depth || result.count >= (NSUInteger)cap) { continue; }
            NSMutableDictionary *n = [node mutableCopy];
            if (references) {
                SWElement *ref = [SWElement new]; ref->owner = s; ref->target = t; ref->epoch = t->epoch;
                ref->rawIdentifier = node[@"identifier"]; ref->syntheticNode = node;
                n[@"ref"] = ref;
            }
            [result addObject:n];
        }
        return result;
    }
#endif
    NSMutableArray *result = [NSMutableArray array];
    NSMutableArray *stack = [NSMutableArray arrayWithObject:@{@"element": (__bridge id)t->root, @"depth": @0, @"parent": NSNull.null}];
    NSMutableSet *seen = [NSMutableSet set];
    NSUInteger bytes = 0;
    while (stack.count && result.count < (NSUInteger)cap) {
        if (SWDeadlineExpired()) { treeFailure = @"tree_deadline"; return nil; }
        NSDictionary *pending = stack.lastObject; [stack removeLastObject];
        id object = pending[@"element"];
        if ([seen containsObject:object]) { treeFailure = @"tree_cycle"; return nil; }
        [seen addObject:object];
        AXUIElementRef e = (__bridge AXUIElementRef)object;
        AXUIElementSetMessagingTimeout(e, 0.2f);
        pid_t pid = 0;
        if (AXUIElementGetPid(e, &pid) != kAXErrorSuccess || pid != t->pid) { treeFailure = @"tree_pid"; return nil; }
        NSDictionary *info = node_info(t, e);
        if (!info) { treeFailure = @"tree_role"; return nil; }
        for (NSString *key in @[@"identifier", @"role", @"name"]) {
            if ([info[key] isKindOfClass:NSString.class]) { bytes += [info[key] lengthOfBytesUsingEncoding:NSUTF8StringEncoding]; }
        }
        if (bytes > 1024 * 1024) { treeFailure = @"tree_bytes"; return nil; }
        NSMutableDictionary *node = [info mutableCopy];
        node[@"depth"] = pending[@"depth"]; node[@"parent_index"] = pending[@"parent"];
        if (references) {
            SWElement *ref = [SWElement new];
            ref->owner = s; ref->target = t; ref->element = (AXUIElementRef)CFRetain(e);
            ref->rawIdentifier = SWString(info[@"raw_identifier"]); ref->epoch = t->epoch;
            node[@"ref"] = ref;
        } else {
            // Retaining the actual AX object in the fingerprint detects replacement
            // by a same-name/same-geometry sibling; actions never look it up by name.
            node[@"object"] = object;
        }
        if (references) { [node removeObjectForKey:@"raw_identifier"]; }
        NSUInteger index = result.count;
        [result addObject:node];
        NSInteger level = [pending[@"depth"] integerValue];
        if (level < depth && result.count < (NSUInteger)cap) {
            NSArray *children = ax_children(e, kAXChildrenAttribute, (NSUInteger)cap - result.count, NULL);
            if (!children) { treeFailure = @"tree_children"; return nil; }
            for (id child in [children reverseObjectEnumerator]) {
                // Explicit AXWindow boundaries must not enter a sibling/popup root.
                NSString *role = SWString(ax_copy((__bridge AXUIElementRef)child, kAXRoleAttribute));
                if ([role isEqual:(__bridge NSString *)kAXWindowRole]) { continue; }
                // Require membership in the retained root, including AppKit's
                // ignored-container presentation of the bounded child graph.
                if (!belongs_to((__bridge AXUIElementRef)child, t->root)) { treeFailure = @"tree_ancestry"; return nil; }
                [stack addObject:@{@"element": child, @"depth": @(level + 1), @"parent": @(index)}];
            }
            // Retain only the next bounded DFS prefix; discard farthest siblings.
            while (stack.count > (NSUInteger)cap - result.count) { [stack removeObjectAtIndex:0]; }
        }
    }
    return result;
}
static NSArray *fingerprint_from_nodes(NSArray *nodes)
{
    NSMutableArray *result = [NSMutableArray arrayWithCapacity:nodes.count];
    for (NSDictionary *node in nodes) {
        SWElement *ref = node[@"ref"];
        NSMutableDictionary *entry = [node mutableCopy];
        [entry removeObjectForKey:@"ref"];
        if (ref->element) {
            entry[@"object"] = (__bridge id)ref->element;
            entry[@"raw_identifier"] = ref->rawIdentifier ?: (id)NSNull.null;
        }
        [result addObject:entry];
    }
    return result;
}
NSDictionary *SWElements(SWSession *s, NSInteger depth, NSInteger cap)
{
    NSDictionary *failure = SWValidate(s);
    if (failure) { return failure; }
    SWTarget *t = s->semanticTarget;
    if (!t) { return SWFail(@"unsupported_background_operation"); }
    ++t->epoch; // every native snapshot invalidates preceding native refs too
    s->treeEpoch = t->epoch;
    uint64_t epoch = t->epoch;
    // DTO attributes and retained identity must describe the SAME observation.
    // Validate that observation once; never hide a detected race with retries.
    NSArray *nodes = read_tree(s, depth, cap, YES);
    NSArray *fingerprint = nodes ? fingerprint_from_nodes(nodes) : nil;
    NSArray *after = nodes ? read_tree(s, depth, cap, NO) : nil;
    if (!nodes || t->epoch != epoch || ![fingerprint isEqual:after]) {
        ++t->epoch;
        return SWError(@"stale_element", @"AX", treeFailure ?: @"tree_changed", @"not_dispatched", @"refresh");
    }
    s->fingerprint = fingerprint; s->depthLimit = depth; s->nodeLimit = cap; s->snapshotTime = SWNow();
    NSMutableDictionary *d = [SWIdentity(s) mutableCopy];
    BOOL mapping = !s->captureTarget || CGRectEqualToRect(s->captureTarget->bounds, t->bounds);
    [d addEntriesFromDictionary:@{@"tree_epoch": @(t->epoch), @"coordinate_unit": @"points", @"mapping_status": mapping ? @"verified" : @"unavailable", @"elements": nodes}];
    return SWOK(d);
}
NSDictionary *SWValidateSnapshot(SWSession *s, uint64_t epoch, uint64_t revision)
{
    NSDictionary *failure = SWValidate(s);
    if (failure) { return failure; }
    if (s->revision != revision) { return SWFail(@"stale_geometry"); }
    SWTarget *t = s->semanticTarget;
    if (!t || t->epoch != epoch || s->treeEpoch != epoch || !s->fingerprint || SWNow() - s->snapshotTime > 30) { return SWFail(@"stale_element"); }
    NSArray *live = read_tree(s, s->depthLimit, s->nodeLimit, NO);
    if (!live || ![live isEqual:s->fingerprint]) { ++t->epoch; return SWFail(@"stale_element"); }
    return nil;
}
static BOOL known_bool(id value, BOOL expected) { return [value isEqual:expected ? @YES : @NO]; }
#ifdef SCAPKIT_TESTING
NSDictionary *SWTestAXTransport(NSString *mode, NSInteger depth, NSInteger count)
{
    axTransport = [SWAXTransport new]; axTransport->mode = mode;
    @try {
        SWTarget *t = [SWTarget new]; // deliberately NOT synthetic: use real read_tree
        t->pid = getpid(); t->process = SWProcess(t->pid);
        t->root = (AXUIElementRef)CFRetain(axTransport->root); t->monitoring = YES;
        t->identifier = @"transport.root"; t->bounds = CGRectMake(0, 0, 200, 100);
        SWSession *s = [SWSession new]; s->semanticTarget = t; s->process = t->process;
        s->lastBounds = t->bounds; s->revision = 1;
        NSDictionary *snapshot = SWElements(s, depth, count);
        NSArray *nodes = snapshot[@"value"][@"elements"];
        axTransport->validating = YES;
        NSDictionary *validation = SWValidateSnapshot(s, s->treeEpoch, s->revision);
        NSMutableDictionary *result = [@{@"snapshot_ok": snapshot[@"ok"], @"validate_ok": @(!validation),
            @"node_count": @(nodes.count), @"error": snapshot[@"error"] ?: (id)NSNull.null,
            @"snapshot_child_x": nodes.count > 1 ? nodes[1][@"bounds"][@"x"] : (id)NSNull.null,
            @"fingerprint_child_x": s->fingerprint.count > 1 ? s->fingerprint[1][@"bounds"][@"x"] : (id)NSNull.null} mutableCopy];
        SWClose(s);
        return SWOK(result);
    } @finally { axTransport = nil; }
}
#endif
static NSMutableSet *revokedProfiles; // actor only, shared across sessions
static BOOL descends_from(AXUIElementRef element, AXUIElementRef root)
{
    id current = (__bridge id)element;
    NSMutableSet *seen = [NSMutableSet set];
    for (NSUInteger i = 0; i <= 32 && current; ++i) {
        if (CFEqual((__bridge CFTypeRef)current, root)) { return YES; }
        if ([seen containsObject:current]) { return NO; }
        [seen addObject:current];
        current = ax_copy((__bridge AXUIElementRef)current, kAXParentAttribute);
        if (current && CFGetTypeID((__bridge CFTypeRef)current) != AXUIElementGetTypeID()) { return NO; }
    }
    return NO;
}
NSDictionary *SWAction(SWSession *s, SWElement *e, uint64_t epoch, uint64_t revision,
    NSString *action, NSString *value, NSDictionary *scope, NSString *operation, double deadline, BOOL candidate)
{
    if (s->uncertain) { return SWFail(@"outcome_unknown"); }
    NSString *profile = scope[@"profile_id"];
    if (profile && [revokedProfiles containsObject:profile]) { s->revoked = YES; }
    if (s->revoked) { return SWFail(@"interference_detected"); }
    if (e->owner != s || e->target != s->semanticTarget || e->epoch != epoch) { return SWFail(@"stale_element"); }
    NSDictionary *failure = SWValidateSnapshot(s, epoch, revision);
    if (failure) { return failure; }
    SWTarget *t = s->semanticTarget;
    if (t->interfered) {
        s->revoked = YES;
        if (profile) {
            if (!revokedProfiles) { revokedProfiles = [NSMutableSet set]; }
            [revokedProfiles addObject:profile];
        }
        return SWFail(@"interference_detected");
    }
    if (!t->subject) { return SWFail(@"background_not_verified"); }
    if (!known_bool(t->minimized, NO)) { return SWFail(known_bool(t->minimized, YES) ? @"window_minimized" : @"unsupported_background_operation"); }
    if (!known_bool(t->modal, NO)) { return SWFail(@"modal_blocked"); }
    NSDictionary *node;
#ifdef SCAPKIT_TESTING
    if (t->synthetic) { node = e->syntheticNode; }
    else
#endif
    {
        if (!belongs_to(e->element, t->root)) { return SWFail(@"stale_element"); }
        node = node_info(t, e->element);
        if (!node || ![(SWString(node[@"raw_identifier"]) ?: @"") isEqual:(e->rawIdentifier ?: @"")]) { return SWFail(@"stale_element"); }
    }
    if (!known_bool(node[@"enabled"], YES) || !known_bool(node[@"password"], NO) ||
        ![node[@"actions"] containsObject:action] || ([action isEqual:@"set_value"] && !known_bool(node[@"editable"], YES))) {
        return SWFail(@"element_not_editable");
    }
    if (candidate) {
#ifdef SCAPKIT_TESTING
        // Candidate path is limited to the exact pinned self-owned adapter and
        // two stable control rules. It does not register/pin a production profile.
        BOOL allowed = ([node[@"identifier"] isEqual:@"fixture.text"] && [node[@"role"] isEqual:@"AXTextField"] && [action isEqual:@"set_value"]) ||
            ([node[@"identifier"] isEqual:@"fixture.increment"] && [node[@"role"] isEqual:@"AXButton"] && [action isEqual:@"invoke"]);
        if (!allowed) { return SWFail(@"background_not_verified"); }
#else
        return SWFail(@"background_not_verified");
#endif
    } else {
        for (NSString *key in t->subject) {
            if (![t->subject[key] isEqual:scope[key]]) { return SWFail(@"background_not_verified"); }
        }
        NSDictionary *rule = @{@"identifier": node[@"identifier"], @"role": node[@"role"], @"action": action};
        if (![scope[@"actions"] isKindOfClass:NSArray.class] || ![scope[@"actions"] containsObject:rule]) { return SWFail(@"background_not_verified"); }
    }
    if (SWDeadlineExpired() || SWNow() >= deadline) { return SWFail(@"backend_busy"); }
#ifdef SCAPKIT_TESTING
    if (t->synthetic) { return SWOK(@{@"operation_id": operation, @"dispatch_state": @"accepted", @"effect_state": @"unknown"}); }
#endif
    AXUIElementSetMessagingTimeout(e->element, (float)MIN(0.5, MAX(0.001, deadline - SWNow())));
    AXUIElementRef app = application(t->pid);
    id frontBefore = app ? ax_bool(app, kAXFrontmostAttribute) : (id)NSNull.null;
    id focusBefore = ax_bool(t->root, kAXFocusedAttribute);
    if (SWDeadlineExpired()) { if (app) { CFRelease(app); } return SWFail(@"backend_busy"); }
    AXError error;
    if ([action isEqual:@"set_value"]) { error = AXUIElementSetAttributeValue(e->element, kAXValueAttribute, (__bridge CFStringRef)value); }
    else { error = AXUIElementPerformAction(e->element, kAXPressAction); }
    id frontAfter = app ? ax_bool(app, kAXFrontmostAttribute) : (id)NSNull.null;
    id focusAfter = ax_bool(t->root, kAXFocusedAttribute);
    if (app) { CFRelease(app); }
    if ((known_bool(frontBefore, NO) && known_bool(frontAfter, YES)) ||
        (known_bool(focusBefore, NO) && known_bool(focusAfter, YES))) {
        t->interfered = YES; s->revoked = YES;
        if (profile) {
            if (!revokedProfiles) { revokedProfiles = [NSMutableSet set]; }
            [revokedProfiles addObject:profile];
        }
        return SWError(@"interference_detected", @"AX", nil, @"accepted", @"never");
    }
    if (error != kAXErrorSuccess) {
        // Once an IPC write is issued, even provider refusal can race an effect.
        s->uncertain = YES;
        return SWError(@"outcome_unknown", @"AX", @(error), @"unknown", @"never");
    }
    BOOL confirmed = NO;
    if ([action isEqual:@"set_value"]) {
        id readback = ax_copy(e->element, kAXValueAttribute); // exact safe, non-password fixture only
        confirmed = [readback isKindOfClass:NSString.class] && [readback isEqual:value];
    }
    if (SWDeadlineExpired()) { s->uncertain = YES; return SWError(@"outcome_unknown", @"AX", nil, @"unknown", @"never"); }
    // No application effect is inferred from AXPress success.
    return SWOK(@{@"operation_id": operation, @"dispatch_state": @"accepted", @"effect_state": confirmed ? @"verified" : @"unknown"});
}
