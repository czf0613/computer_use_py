// Included only in SCAPKIT_TESTING accessibility.m. Replace IPC, not tree logic.
// AXUIElementCreateApplication creates local typed proxies without contacting apps.
@interface SWAXTransport : NSObject {
@public
    AXUIElementRef app, root, child, replacement;
    NSString *mode;
    NSUInteger pass;
    BOOL validating;
}
@end
@implementation SWAXTransport
- (instancetype)init
{
    self = [super init];
    if (self) {
        app = AXUIElementCreateApplication(1000001);
        root = AXUIElementCreateApplication(1000002);
        child = AXUIElementCreateApplication(1000003);
        replacement = AXUIElementCreateApplication(1000004);
    }
    return self;
}
- (void)dealloc
{
    if (app) { CFRelease(app); }
    if (root) { CFRelease(root); }
    if (child) { CFRelease(child); }
    if (replacement) { CFRelease(replacement); }
}
@end
static SWAXTransport *axTransport; // scoped to one actor job; never a Python pointer
static BOOL ax_test_middle(void) { return axTransport->pass == 2; }
static AXError ax_test_copy(AXUIElementRef e, CFStringRef attribute, CFTypeRef *out)
{
    if (!axTransport) { return AXUIElementCopyAttributeValue(e, attribute, out); }
    *out = NULL;
    BOOL root = CFEqual(e, axTransport->root);
    NSString *m = axTransport->mode;
    id value = nil;
    if (CFEqual(attribute, kAXRoleAttribute)) {
        if (root) { ++axTransport->pass; }
        value = root ? @"AXWindow" : @"AXTextField";
    } else if (CFEqual(attribute, kAXIdentifierAttribute)) {
        value = root ? @"transport.root" : @"transport.child";
        if (!root && ax_test_middle() && [m isEqual:@"middle_identifier"]) { value = @"transport.changed"; }
    } else if (CFEqual(attribute, kAXPositionAttribute)) {
        CGPoint p = root ? CGPointZero : CGPointMake(10, 10);
        if (!root && ax_test_middle() && [m isEqual:@"middle_geometry"]) { p.x = 80; }
        *out = AXValueCreate(kAXValueCGPointType, &p); return kAXErrorSuccess;
    } else if (CFEqual(attribute, kAXSizeAttribute)) {
        CGSize size = root ? CGSizeMake(200, 100) : CGSizeMake(40, 20);
        *out = AXValueCreate(kAXValueCGSizeType, &size); return kAXErrorSuccess;
    } else if (CFEqual(attribute, kAXEnabledAttribute)) {
        value = !root && ax_test_middle() && [m isEqual:@"middle_enabled"] ? @NO : @YES;
    } else if (CFEqual(attribute, kAXMinimizedAttribute) || CFEqual(attribute, kAXModalAttribute)) {
        value = @NO;
    } else if (CFEqual(attribute, kAXWindowAttribute) || CFEqual(attribute, kAXParentAttribute)) {
        value = (__bridge id)axTransport->root;
    }
    if (!value) { return kAXErrorAttributeUnsupported; }
    *out = CFRetain((__bridge CFTypeRef)value);
    return kAXErrorSuccess;
}
static AXError ax_test_count(AXUIElementRef e, CFStringRef attribute, CFIndex *count)
{
    if (!axTransport) { return AXUIElementGetAttributeValueCount(e, attribute, count); }
    *count = 0;
    if (CFEqual(attribute, kAXWindowsAttribute)) { *count = 1; return kAXErrorSuccess; }
    if (!CFEqual(attribute, kAXChildrenAttribute)) { return kAXErrorAttributeUnsupported; }
    NSString *m = axTransport->mode;
    if (CFEqual(e, axTransport->root)) {
        if ([m isEqual:@"children_count_error"] || (axTransport->validating && [m isEqual:@"validation_count_error"])) { return kAXErrorCannotComplete; }
        if ([m isEqual:@"children_invalid"]) { return kAXErrorInvalidUIElement; }
        *count = 1;
    } else if ([m isEqual:@"leaf_unsupported"]) { return kAXErrorAttributeUnsupported; }
    return kAXErrorSuccess;
}
static AXError ax_test_values(AXUIElementRef e, CFStringRef attribute, CFIndex index, CFIndex count, CFArrayRef *out)
{
    if (!axTransport) { return AXUIElementCopyAttributeValues(e, attribute, index, count, out); }
    *out = NULL;
    NSString *m = axTransport->mode;
    if (CFEqual(attribute, kAXChildrenAttribute) &&
        ([m isEqual:@"children_values_error"] || (axTransport->validating && [m isEqual:@"validation_values_error"]))) { return kAXErrorCannotComplete; }
    id item = CFEqual(attribute, kAXWindowsAttribute) ? (__bridge id)axTransport->root :
        (__bridge id)((ax_test_middle() && [m isEqual:@"middle_object"]) ? axTransport->replacement : axTransport->child);
    *out = (CFArrayRef)CFRetain((__bridge CFTypeRef)@[item]);
    return kAXErrorSuccess;
}
static AXError ax_test_timeout(AXUIElementRef e, float seconds)
{
    return axTransport ? kAXErrorSuccess : AXUIElementSetMessagingTimeout(e, seconds);
}
static AXError ax_test_pid(AXUIElementRef e, pid_t *pid)
{
    if (!axTransport) { return AXUIElementGetPid(e, pid); }
    *pid = getpid(); return kAXErrorSuccess;
}
static AXError ax_test_settable(AXUIElementRef e, CFStringRef attribute, Boolean *out)
{
    if (!axTransport) { return AXUIElementIsAttributeSettable(e, attribute, out); }
    *out = !CFEqual(e, axTransport->root);
    if (ax_test_middle() && [axTransport->mode isEqual:@"middle_editable"]) { *out = false; }
    return kAXErrorSuccess;
}
static AXError ax_test_actions(AXUIElementRef e, CFArrayRef *out)
{
    if (!axTransport) { return AXUIElementCopyActionNames(e, out); }
    NSArray *actions = ax_test_middle() && [axTransport->mode isEqual:@"middle_actions"] ? @[@"AXPress"] : @[];
    *out = (CFArrayRef)CFRetain((__bridge CFTypeRef)actions); return kAXErrorSuccess;
}
static AXUIElementRef ax_test_application(pid_t pid)
{
    return axTransport ? (AXUIElementRef)CFRetain(axTransport->app) : AXUIElementCreateApplication(pid);
}
static Boolean ax_test_trusted(void) { return axTransport ? true : AXIsProcessTrusted(); }
#define AXUIElementCopyAttributeValue ax_test_copy
#define AXUIElementGetAttributeValueCount ax_test_count
#define AXUIElementCopyAttributeValues ax_test_values
#define AXUIElementSetMessagingTimeout ax_test_timeout
#define AXUIElementGetPid ax_test_pid
#define AXUIElementIsAttributeSettable ax_test_settable
#define AXUIElementCopyActionNames ax_test_actions
#define AXUIElementCreateApplication ax_test_application
#define AXIsProcessTrusted ax_test_trusted
