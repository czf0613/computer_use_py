// Standalone AppKit fixture, macOS 13+. No library/product API dependency.
#import <AppKit/AppKit.h>
#import <libproc.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef FIXTURE_BUILD_SHA256
#define FIXTURE_BUILD_SHA256 "unversioned"
#endif

static const NSUInteger EventLimit = 4096;
static volatile sig_atomic_t stopRequested = 0;
static void requestStop(int signalNumber) { (void)signalNumber; stopRequested = 1; }
static uint64_t nowNS(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (uint64_t)value.tv_sec * 1000000000 + (uint64_t)value.tv_nsec;
}
static NSString *token(void) { return NSUUID.UUID.UUIDString.lowercaseString; }
static void die(const char *message) {
    fprintf(stderr, "window-fixture: %s\n", message);
    exit(1);
}

@class Fixture, FixtureWindow;
@interface Record : NSObject
@property NSString *slot, *token, *title, *text, *nativeID;
@property NSUInteger count;
@property BOOL closed, modal;
@property FixtureWindow *window;
@property NSTextField *edit, *counter;
@property NSTextView *fieldEditor;
@property NSMutableDictionary<NSString *, NSNumber *> *totals;
- (NSString *)identifier:(NSString *)part;
- (NSDictionary *)json;
@end

@interface Fixture : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property NSMutableArray<Record *> *records;
@property NSMutableArray<NSDictionary *> *events;
@property NSString *processToken, *role, *processStart;
@property BOOL offline, background, running, recreating;
@property uint64_t sequence, snapshotSequence, dropped;
@property int directoryFD;
@property NSTimer *timer;
- (Record *)newRecord:(NSString *)slot;
- (void)event:(NSString *)type record:(Record *)record data:(NSDictionary *)data;
- (void)publish;
- (void)createWindow:(NSString *)slot;
- (void)startWindows;
@end

@interface FixtureWindow : NSWindow
@property(weak) Fixture *fixture;
@property(weak) Record *record;
@end

@interface Canvas : NSView
@property(weak) Fixture *fixture;
@property(weak) Record *record;
@property NSMutableArray<NSValue *> *points;
@end

// Keep even manual copy/paste and drag-and-drop out of the fixture's data path.
// Typing and the native accessibility value remain ordinary AppKit editing.
@interface FixtureEditor : NSTextView
@end
@implementation FixtureEditor
- (void)copy:(id)sender { (void)sender; }
- (void)cut:(id)sender { (void)sender; }
- (void)paste:(id)sender { (void)sender; }
- (void)pasteAsPlainText:(id)sender { (void)sender; }
- (void)pasteAsRichText:(id)sender { (void)sender; }
- (NSArray<NSPasteboardType> *)readablePasteboardTypes { return @[]; }
- (NSArray<NSPasteboardType> *)writablePasteboardTypes { return @[]; }
- (BOOL)readSelectionFromPasteboard:(NSPasteboard *)pasteboard {
    (void)pasteboard; return NO;
}
- (BOOL)readSelectionFromPasteboard:(NSPasteboard *)pasteboard type:(NSPasteboardType)type {
    (void)pasteboard; (void)type; return NO;
}
- (BOOL)writeSelectionToPasteboard:(NSPasteboard *)pasteboard type:(NSPasteboardType)type {
    (void)pasteboard; (void)type; return NO;
}
- (BOOL)writeSelectionToPasteboard:(NSPasteboard *)pasteboard types:(NSArray<NSPasteboardType> *)types {
    (void)pasteboard; (void)types; return NO;
}
@end

@implementation Record
- (NSString *)identifier:(NSString *)part {
    return [NSString stringWithFormat:@"scapkit.fixture.%@.%@.%@", self.slot, self.token, part];
}
- (NSDictionary *)json {
    NSRect frame = self.window ? self.window.frame : NSZeroRect;
    BOOL live = self.window && !self.closed;
    return @{@"slot": self.slot, @"instance_token": self.token, @"title": self.title,
             @"native_id": self.nativeID, @"closed": @(self.closed), @"modal": @(self.modal),
             @"text": self.text, @"count": @(self.count),
             @"accessibility": @{@"api": @"AX", @"window": [self identifier:@"window"],
                                 @"text": [self identifier:@"text"],
                                 @"increment": [self identifier:@"increment"]},
             @"key": @(live && self.window.isKeyWindow),
             @"visible": @(live && self.window.isVisible),
             @"minimized": @(live && self.window.isMiniaturized),
             @"bounds": @[@(frame.origin.x), @(frame.origin.y),
                           @(frame.size.width), @(frame.size.height)],
             @"coordinate_unit": @"points", @"event_totals": self.totals};
}
@end

@implementation FixtureWindow
- (void)sendEvent:(NSEvent *)event {
    // Only events dispatched to this NSWindow; never a local/global event monitor.
    if (event.window == self && self.record) {
        NSString *kind = nil;
        if (event.type == NSEventTypeKeyDown) { kind = @"key_down"; }
        if (event.type == NSEventTypeKeyUp) { kind = @"key_up"; }
        if (event.type == NSEventTypeFlagsChanged) { kind = @"modifiers"; }
        if (kind) {
            [self.fixture event:kind record:self.record
                           data:@{@"key_code": @(event.keyCode),
                                  @"modifiers": @(event.modifierFlags),
                                  @"repeat": @(event.type == NSEventTypeKeyDown && event.isARepeat)}];
        }
    }
    [super sendEvent:event];
}
- (BOOL)makeFirstResponder:(NSResponder *)responder {
    NSResponder *previous = self.firstResponder;
    BOOL result = [super makeFirstResponder:responder];
    if (result && previous != self.firstResponder && self.record) {
        [self.fixture event:@"focus_changed" record:self.record data:@{}];
    }
    return result;
}
@end

@implementation Canvas
- (void)drawRect:(NSRect)dirtyRect {
    (void)dirtyRect;
    [NSColor.whiteColor setFill];
    NSRectFill(self.bounds);
    [NSColor.darkGrayColor setStroke];
    NSFrameRect(self.bounds);
    NSBezierPath *path = [NSBezierPath bezierPath];
    path.lineWidth = 2;
    for (NSUInteger i = 0; i < self.points.count; ++i) {
        NSPoint point = self.points[i].pointValue;
        if (i == 0) { [path moveToPoint:point]; } else { [path lineToPoint:point]; }
    }
    [path stroke];
}
- (void)recordMouse:(NSEvent *)event kind:(NSString *)kind {
    NSPoint point = [self convertPoint:event.locationInWindow fromView:nil];
    if (self.points.count == 512) { [self.points removeObjectAtIndex:0]; }
    [self.points addObject:[NSValue valueWithPoint:point]];
    [self.fixture event:kind record:self.record data:@{@"x": @(point.x), @"y": @(point.y)}];
    self.needsDisplay = YES;
}
- (void)mouseDown:(NSEvent *)event {
    [self.points removeAllObjects];
    [self recordMouse:event kind:@"canvas_down"];
}
- (void)mouseDragged:(NSEvent *)event { [self recordMouse:event kind:@"canvas_drag"]; }
- (void)mouseUp:(NSEvent *)event { [self recordMouse:event kind:@"canvas_up"]; }
@end

@implementation Fixture
- (instancetype)init {
    self = [super init];
    if (self) {
        _records = [NSMutableArray array];
        _events = [NSMutableArray array];
        _processToken = token();
        _directoryFD = -1;
        _running = YES;
        struct proc_bsdinfo info = {0};
        if (proc_pidinfo(getpid(), PROC_PIDTBSDINFO, 0, &info, sizeof(info)) != sizeof(info)) {
            die("cannot read own process start time");
        }
        _processStart = [NSString stringWithFormat:@"%llu.%06llu",
                         (unsigned long long)info.pbi_start_tvsec,
                         (unsigned long long)info.pbi_start_tvusec];
    }
    return self;
}
- (Record *)newRecord:(NSString *)slot {
    if (self.records.count >= 64) { die("64 window instances reached; start a new fixture run"); }
    Record *record = [Record new];
    record.slot = slot;
    record.token = token();
    record.title = [@"SCAPKIT Fixture " stringByAppendingString:slot];
    record.text = [slot stringByAppendingString:@" initial - synthetic only"];
    record.nativeID = @"0";
    record.totals = [NSMutableDictionary dictionary];
    [self.records addObject:record];
    return record;
}
- (void)event:(NSString *)type record:(Record *)record data:(NSDictionary *)data {
    if (record) { record.totals[type] = @(record.totals[type].unsignedLongLongValue + 1); }
    [self.events addObject:@{@"seq": @(++self.sequence), @"monotonic_ns": @(nowNS()),
                            @"window_token": record ? record.token : (id)NSNull.null,
                            @"type": type, @"data": data}];
    if (self.events.count > EventLimit) {
        [self.events removeObjectAtIndex:0];
        ++self.dropped;
    }
}
- (void)publish {
    NSMutableArray *windows = [NSMutableArray array];
    for (Record *record in self.records) {
        if (record.edit && !record.closed) {
            NSString *value = record.edit.currentEditor ? record.edit.currentEditor.string : record.edit.stringValue;
            // Preserve the real value. No truncation that could masquerade as a successful readback.
            if (![record.text isEqualToString:value]) {
                record.text = value;
                [self event:@"text_changed" record:record data:@{}];
            }
        }
        [windows addObject:record.json];
    }
    NSDictionary *state = @{@"schema_version": @1, @"fixture_version": @"window-fixture-v1",
        @"build_sha256": @FIXTURE_BUILD_SHA256, @"platform": @"macos", @"pid": @(getpid()),
        @"process_token": self.processToken, @"process_start": self.processStart,
        @"role": self.role, @"background": @(self.background), @"running": @(self.running),
        @"execution": self.offline ? @"offline_probe" : @"desktop", @"desktop_validation": @"not_run",
        @"snapshot_sequence": @(++self.snapshotSequence), @"observed_monotonic_ns": @(nowNS()),
        @"active": @(!self.offline && NSApp.isActive), @"windows": windows,
        @"event_limit": @(EventLimit), @"events_dropped": @(self.dropped),
        @"event_sequence": @(self.sequence), @"events": self.events};
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:state options:NSJSONWritingSortedKeys error:&error];
    if (!data) { die("JSON encoding failed"); }
    NSString *temporary = [@"snapshot-" stringByAppendingString:token()];
    int fd = openat(self.directoryFD, temporary.UTF8String,
                    O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) { die("cannot create private snapshot"); }
    const char *bytes = data.bytes;
    size_t offset = 0;
    while (offset < data.length) {
        ssize_t written = write(fd, bytes + offset, data.length - offset);
        if (written < 0 && errno == EINTR) { continue; }
        if (written <= 0) { die("snapshot write failed"); }
        offset += (size_t)written;
    }
    if (fsync(fd) != 0 || close(fd) != 0 ||
        renameat(self.directoryFD, temporary.UTF8String, self.directoryFD, "state.json") != 0) {
        die("snapshot publish failed");
    }
}
- (NSButton *)button:(NSString *)title action:(SEL)action record:(Record *)record frame:(NSRect)frame {
    NSButton *button = [NSButton buttonWithTitle:title target:self action:action];
    button.frame = frame;
    button.identifier = record.token;
    return button;
}
- (Record *)recordForSender:(NSControl *)sender {
    for (Record *record in self.records) {
        if ([record.token isEqualToString:sender.identifier] && !record.closed) { return record; }
    }
    return nil;
}
- (void)createWindow:(NSString *)slot {
    for (Record *existing in self.records) {
        if ([existing.slot isEqualToString:slot] && !existing.closed) { return; }
    }
    Record *record = [self newRecord:slot];
    BOOL second = [slot isEqualToString:@"A2"];
    FixtureWindow *window = [[FixtureWindow alloc]
        initWithContentRect:NSMakeRect(second ? 650 : 80, 180, 540, 370)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                  NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
        backing:NSBackingStoreBuffered defer:NO];
    record.window = window;
    window.fixture = self;
    window.record = record;
    window.delegate = self;
    window.releasedWhenClosed = NO;
    window.title = record.title;
    window.accessibilityIdentifier = [record identifier:@"window"];
    window.backgroundColor = second ? [NSColor colorWithSRGBRed:1 green:.82 blue:.58 alpha:1] :
        ([slot isEqualToString:@"B"] ? [NSColor colorWithSRGBRed:.72 green:1 blue:.74 alpha:1] :
         [NSColor colorWithSRGBRed:.65 green:.83 blue:1 alpha:1]);
    NSView *view = window.contentView;
    NSTextField *label = [NSTextField labelWithString:[NSString stringWithFormat:
        @"%@ | %@\nSynthetic text only. Click manually to focus.", slot, record.token]];
    label.frame = NSMakeRect(16, 302, 508, 56);
    [view addSubview:label];
    record.edit = [[NSTextField alloc] initWithFrame:NSMakeRect(16, 260, 508, 30)];
    record.edit.stringValue = record.text;
    record.edit.accessibilityIdentifier = [record identifier:@"text"];
    record.edit.accessibilityLabel = [slot stringByAppendingString:@" test text"];
    [view addSubview:record.edit];
    NSButton *increment = [self button:@"Increment this window" action:@selector(increment:)
                               record:record frame:NSMakeRect(16, 215, 210, 32)];
    increment.accessibilityIdentifier = [record identifier:@"increment"];
    [view addSubview:increment];
    record.counter = [NSTextField labelWithString:@"Count: 0"];
    record.counter.frame = NSMakeRect(240, 220, 180, 24);
    [view addSubview:record.counter];
    NSArray *titles = @[@"Move", @"Resize", @"Minimize", @"Modal", @"Recreate"];
    SEL actions[] = {@selector(move:), @selector(resize:), @selector(minimize:),
                     @selector(modal:), @selector(recreate:)};
    for (NSUInteger i = 0; i < titles.count; ++i) {
        [view addSubview:[self button:titles[i] action:actions[i] record:record
                                frame:NSMakeRect(12 + 104 * i, 170, 100, 32)]];
    }
    Canvas *canvas = [[Canvas alloc] initWithFrame:NSMakeRect(16, 60, 508, 95)];
    canvas.fixture = self;
    canvas.record = record;
    canvas.points = [NSMutableArray array];
    canvas.accessibilityLabel = @"Synthetic mouse drawing area";
    [view addSubview:canvas];
    if ([self.role isEqualToString:@"a"]) {
        [view addSubview:[self button:@"Reopen other A window" action:@selector(reopenOther:)
                                record:record frame:NSMakeRect(16, 15, 235, 32)]];
    }
    [view addSubview:[self button:@"Quit this fixture process" action:@selector(quit:)
                            record:record frame:NSMakeRect(270, 15, 254, 32)]];
    // Deliberately no activate, makeKeyAndOrderFront, or focus assignment.
    [window orderBack:nil];
    record.nativeID = [NSString stringWithFormat:@"%ld", (long)window.windowNumber];
    [self event:@"created" record:record data:@{}];
    [self publish];
}
- (void)increment:(NSControl *)sender {
    Record *record = [self recordForSender:sender];
    if (!record || record.modal) { return; }
    record.count += 1;
    record.counter.stringValue = [NSString stringWithFormat:@"Count: %lu", (unsigned long)record.count];
    [self event:@"increment" record:record data:@{}];
    [self publish];
}
- (void)move:(NSControl *)sender {
    Record *record = [self recordForSender:sender];
    if (!record) { return; }
    NSPoint origin = record.window.frame.origin;
    [record.window setFrameOrigin:NSMakePoint(origin.x + 30, origin.y + 20)];
}
- (void)resize:(NSControl *)sender {
    Record *record = [self recordForSender:sender];
    if (!record) { return; }
    NSRect frame = record.window.frame;
    frame.size.width = frame.size.width > 560 ? 540 : 600;
    [record.window setFrame:frame display:YES];
}
- (void)minimize:(NSControl *)sender {
    [[self recordForSender:sender].window miniaturize:nil];
}
- (void)modal:(NSControl *)sender {
    Record *record = [self recordForSender:sender];
    if (!record || record.modal) { return; }
    record.modal = YES;
    record.edit.enabled = NO;
    [self event:@"modal_opened" record:record data:@{}];
    [self publish];
    NSAlert *alert = [NSAlert new];
    alert.messageText = [record.slot stringByAppendingString:@" manual modal"];
    alert.informativeText = @"Close this sheet manually before background action acceptance.";
    [alert addButtonWithTitle:@"Close modal"];
    [alert beginSheetModalForWindow:record.window completionHandler:^(NSModalResponse response) {
        (void)response;
        record.modal = NO;
        record.edit.enabled = YES;
        [self event:@"modal_closed" record:record data:@{}];
        [self publish];
    }];
}
- (void)recreate:(NSControl *)sender {
    Record *record = [self recordForSender:sender];
    if (!record || record.modal) { return; }
    self.recreating = YES;
    [record.window close];
    [self createWindow:record.slot];
    self.recreating = NO;
}
- (void)reopenOther:(NSControl *)sender {
    Record *record = [self recordForSender:sender];
    if (record) { [self createWindow:[record.slot isEqualToString:@"A1"] ? @"A2" : @"A1"]; }
}
- (void)quit:(id)sender { (void)sender; [NSApp terminate:nil]; }
- (id)windowWillReturnFieldEditor:(NSWindow *)sender toObject:(id)client {
    Record *record = ((FixtureWindow *)sender).record;
    if (!record || client != record.edit) { return nil; }
    if (!record.fieldEditor) {
        FixtureEditor *editor = [FixtureEditor new];
        editor.fieldEditor = YES;
        editor.richText = NO;
        editor.continuousSpellCheckingEnabled = NO;
        editor.grammarCheckingEnabled = NO;
        editor.automaticSpellingCorrectionEnabled = NO;
        editor.automaticQuoteSubstitutionEnabled = NO;
        editor.automaticDashSubstitutionEnabled = NO;
        editor.automaticTextReplacementEnabled = NO;
        [editor unregisterDraggedTypes];
        record.fieldEditor = editor;
    }
    return record.fieldEditor;
}
- (void)windowWillClose:(NSNotification *)notification {
    Record *record = ((FixtureWindow *)notification.object).record;
    if (!record) { return; }
    [self publish];
    record.closed = YES;
    [self event:@"closed" record:record data:@{}];
    record.edit = nil;
    record.counter = nil;
    record.fieldEditor = nil;
    record.window = nil;
    [self publish];
}
- (void)windowEvent:(NSNotification *)notification type:(NSString *)type {
    Record *record = ((FixtureWindow *)notification.object).record;
    if (record) { [self event:type record:record data:@{}]; }
}
- (void)windowDidBecomeKey:(NSNotification *)n { [self windowEvent:n type:@"key_gained"]; }
- (void)windowDidResignKey:(NSNotification *)n { [self windowEvent:n type:@"key_lost"]; }
- (void)windowDidBecomeMain:(NSNotification *)n { [self windowEvent:n type:@"main_gained"]; }
- (void)windowDidResignMain:(NSNotification *)n { [self windowEvent:n type:@"main_lost"]; }
- (void)windowDidMove:(NSNotification *)n { [self windowEvent:n type:@"moved"]; }
- (void)windowDidResize:(NSNotification *)n { [self windowEvent:n type:@"resized"]; }
- (void)windowDidMiniaturize:(NSNotification *)n { [self windowEvent:n type:@"minimized"]; }
- (void)windowDidDeminiaturize:(NSNotification *)n { [self windowEvent:n type:@"restored"]; }
- (void)applicationDidBecomeActive:(NSNotification *)n {
    (void)n; [self event:@"app_activated" record:nil data:@{}];
}
- (void)applicationDidResignActive:(NSNotification *)n {
    (void)n; [self event:@"app_deactivated" record:nil data:@{}];
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)app {
    (void)app; return !self.recreating;
}
- (void)applicationWillTerminate:(NSNotification *)notification {
    (void)notification;
    [self.timer invalidate];
    self.running = NO;
    [self event:@"process_stopping" record:nil data:@{}];
    [self publish];
}
- (void)startWindows {
    NSArray *slots = [self.role isEqualToString:@"a"] ? @[@"A1", @"A2"] : @[@"B"];
    for (NSString *slot in slots) { [self createWindow:slot]; }
    self.timer = [NSTimer timerWithTimeInterval:.1 target:self selector:@selector(tick:)
                                      userInfo:nil repeats:YES];
    [NSRunLoop.mainRunLoop addTimer:self.timer forMode:NSRunLoopCommonModes];
}
- (void)tick:(NSTimer *)timer {
    (void)timer;
    if (stopRequested) { [NSApp terminate:nil]; return; }
    [self publish];
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *directory = nil, *role = @"a";
        BOOL offline = NO, background = NO;
        for (int i = 1; i < argc; ++i) {
            NSString *arg = @(argv[i]);
            if ([arg isEqualToString:@"--help"]) {
                puts("window-fixture --state-dir NEW_ABSOLUTE_DIRECTORY [--role a|b] [--background] [--offline-probe]");
                return 0;
            } else if ([arg isEqualToString:@"--state-dir"] && i + 1 < argc) {
                directory = @(argv[++i]);
            } else if ([arg isEqualToString:@"--role"] && i + 1 < argc) {
                role = @(argv[++i]);
            } else if ([arg isEqualToString:@"--background"]) {
                background = YES;
            } else if ([arg isEqualToString:@"--offline-probe"]) {
                offline = YES;
            } else { die("invalid arguments; use --help"); }
        }
        if (!directory.isAbsolutePath || !([role isEqualToString:@"a"] || [role isEqualToString:@"b"])) {
            die("require --state-dir NEW_ABSOLUTE_DIRECTORY and --role a|b");
        }
        umask(0077);
        if (mkdir(directory.fileSystemRepresentation, 0700) != 0) {
            die("state directory must not exist and its parent must exist");
        }
        Fixture *fixture = [Fixture new];
        fixture.directoryFD = open(directory.fileSystemRepresentation, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        struct stat status;
        if (fixture.directoryFD < 0 || fstat(fixture.directoryFD, &status) != 0 ||
            status.st_uid != getuid() || (status.st_mode & 0777) != 0700) {
            die("state directory is not private");
        }
        fixture.role = role;
        fixture.background = background;
        fixture.offline = offline;
        if (offline) {
            // Serialization/lifecycle probe only. Never initialize NSApplication or create controls.
            Record *first = [fixture newRecord:[role isEqualToString:@"a"] ? @"A1" : @"B"];
            first.nativeID = @"4294967294";
            first.text = @"Synthetic \"quotes\" \\ slash\n\tUnicode: 窗口 🧪";
            first.count = 2;
            [fixture event:@"created" record:first data:@{}];
            if ([role isEqualToString:@"a"]) {
                Record *second = [fixture newRecord:@"A2"];
                second.nativeID = @"4294967293";
                [fixture event:@"created" record:second data:@{}];
                first.closed = YES;
                [fixture event:@"closed" record:first data:@{}];
                Record *replacement = [fixture newRecord:@"A1"];
                replacement.nativeID = first.nativeID; // Explicit synthetic native-ID reuse.
                [fixture event:@"created" record:replacement data:@{}];
            }
            for (NSUInteger i = 0; i < EventLimit + 8; ++i) {
                [fixture event:@"probe_event" record:first data:@{}];
            }
            [fixture publish];
            fixture.running = NO;
            [fixture publish];
            puts("offline probe passed (no NSApplication, windows, AX, input, or clipboard)");
        } else {
            signal(SIGTERM, requestStop);
            signal(SIGINT, requestStop);
            [NSApplication sharedApplication];
            NSApp.delegate = fixture;
            // finishLaunching can activate a regular app. Finish it while activation
            // is prohibited, then allow manual activation of our accessory windows.
            // B uses the same safe startup; humans focus it by clicking its text box.
            if (![NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited]) {
                die("cannot prohibit startup activation");
            }
            [NSApp finishLaunching];
            if (![NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory]) {
                die("cannot enable accessory windows");
            }
            [fixture startWindows];
            [NSApp run];
        }
        close(fixture.directoryFD);
    }
    return 0;
}
