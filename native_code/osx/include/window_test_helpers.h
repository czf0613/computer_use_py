// Included only by window.m in SCAPKIT_TESTING builds. No desktop access.
PyObject *scapkit_test_window_ax_transport(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *arg;
        int depth, count;
        if (!PyArg_ParseTuple(args, "Oii", &arg, &depth, &count)) { return NULL; }
        NSString *mode = text_arg(arg, 32, NO);
        if (!mode) { return NULL; }
        if (depth < 0 || depth > 32 || count < 1 || count > 4096) { PyErr_SetString(PyExc_ValueError, "invalid tree limit"); return NULL; }
        return run_python(5, NO, ^{ return SWTestAXTransport(mode, depth, count); });
    }
}
static NSArray *synthetic_nodes(void)
{
    NSDictionary *base = @{@"name": NSNull.null, @"enabled": @YES, @"editable": @NO, @"password": @NO,
        @"actions": @[], @"bounds": SWRect(CGRectMake(0, 0, 200, 100))};
    NSMutableDictionary *root = [base mutableCopy];
    [root addEntriesFromDictionary:@{@"identifier": @"root.instance", @"role": @"AXWindow", @"depth": @0, @"parent_index": NSNull.null}];
    NSMutableDictionary *text = [base mutableCopy];
    [text addEntriesFromDictionary:@{@"identifier": @"fixture.text", @"role": @"AXTextField", @"depth": @1, @"parent_index": @0,
        @"editable": @YES, @"actions": @[@"set_value"], @"bounds": SWRect(CGRectMake(10, 10, 80, 20))}];
    NSMutableDictionary *button = [base mutableCopy];
    [button addEntriesFromDictionary:@{@"identifier": @"fixture.increment", @"role": @"AXButton", @"depth": @1, @"parent_index": @0,
        @"actions": @[@"invoke"], @"bounds": SWRect(CGRectMake(10, 40, 80, 20))}];
    return @[root, text, button];
}
PyObject *scapkit_test_window(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *arg;
        if (!PyArg_ParseTuple(args, "O", &arg)) { return NULL; }
        NSString *kind = text_arg(arg, 32, NO);
        if (!kind) { return NULL; }
        return run_python(5, NO, ^{
            if ([kind isEqual:@"stats"]) {
                return SWOK(@{@"targets": @(atomic_load(&liveTargets)), @"sessions": @(atomic_load(&liveSessions)), @"elements": @(atomic_load(&liveElements))});
            }
            SWTarget *t = [SWTarget new];
            t->synthetic = YES; t->pid = 123; t->process = @"synthetic-start-1"; t->liveProcess = t->process;
            t->bounds = CGRectMake(-200, 30, 200, 100); t->minimized = @NO; t->modal = @NO; t->nodes = synthetic_nodes();
            t->subject = @{@"platform": @"macos", @"application_id": @"synthetic", @"application_version": @"1",
                @"application_build": @"synthetic-build", @"environment": @"offline", @"backend_build": @"test", @"window_identifier": @"fixture.window.v1"};
            if ([kind isEqual:@"unverified"]) { t->subject = nil; }
            SWReference *ref = [SWReference new]; ref->target = t; ref->capture = [kind isEqual:@"capture"];
            NSDictionary *info = SWInfo(ref, @"Synthetic only", @"synthetic-id", @YES);
            if ([kind isEqual:@"expired"]) { ref->issued -= 61; }
            return SWOK(@[info]);
        });
    }
}
PyObject *scapkit_test_window_mutate(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *arg, *kindArg;
        if (!PyArg_ParseTuple(args, "OO", &arg, &kindArg)) { return NULL; }
        SWSession *s = capsule(arg, SessionName);
        NSString *kind = text_arg(kindArg, 32, NO);
        if (!s || !kind) { return NULL; }
        double timeout = [kind isEqual:@"timeout"] ? 0.01 : 5;
        return run_python(timeout, NO, ^{
            SWTarget *t = s->semanticTarget ?: s->captureTarget;
            if (!t || !t->synthetic) { return SWFail(@"unsupported_background_operation"); }
            if ([kind isEqual:@"process"]) { t->liveProcess = @"synthetic-start-2"; }
            if ([kind isEqual:@"restore_process"]) { t->liveProcess = t->process; }
            if ([kind isEqual:@"destroy"]) { SWInvalidate(t, YES); }
            if ([kind isEqual:@"layout"]) { SWInvalidate(t, NO); }
            if ([kind isEqual:@"geometry"]) { t->bounds.origin.x += 10; }
            if ([kind isEqual:@"minimized"]) { t->minimized = @YES; }
            if ([kind isEqual:@"modal"]) { t->modal = @YES; }
            if ([kind isEqual:@"unknown_modal"]) { t->modal = NSNull.null; }
            if ([kind isEqual:@"expired"]) { s->snapshotTime -= 31; }
            if ([kind isEqual:@"revoke"]) { s->revoked = YES; }
            if ([kind isEqual:@"unknown"]) { s->uncertain = YES; }
            if ([kind isEqual:@"replace"]) {
                NSMutableDictionary *node = [t->nodes[1] mutableCopy]; node[@"name"] = @"new incarnation";
                t->nodes = @[t->nodes[0], node, t->nodes[2]];
            }
            if ([kind isEqual:@"password"] || [kind isEqual:@"disabled"]) {
                NSMutableDictionary *node = [t->nodes[1] mutableCopy];
                node[[kind isEqual:@"password"] ? @"password" : @"enabled"] = [kind isEqual:@"password"] ? @YES : @NO;
                t->nodes = @[t->nodes[0], node, t->nodes[2]];
            }
            if ([kind isEqual:@"timeout"]) { [NSThread sleepForTimeInterval:0.15]; }
            return SWOK(nil);
        });
    }
}
PyObject *scapkit_test_window_candidate_action(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool { return action_entry(args, YES); }
}
PyObject *scapkit_test_window_mapping(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        double x, y, w, h, scale, fit, imageW, imageH, windowW, windowH;
        if (!PyArg_ParseTuple(args, "dddddddddd", &x, &y, &w, &h, &scale, &fit, &imageW, &imageH, &windowW, &windowH)) { return NULL; }
        if (!isfinite(imageW) || !isfinite(imageH) || imageW < 1 || imageH < 1 || imageW > 16384 || imageH > 16384) {
            PyErr_SetString(PyExc_ValueError, "invalid image dimensions"); return NULL;
        }
        CGRect rect = CGRectMake(x, y, w, h);
        NSDictionary *r = CFBridgingRelease(CGRectCreateDictionaryRepresentation(rect));
        return to_python(SWMapping(@{SCStreamFrameInfoContentRect: r, SCStreamFrameInfoScaleFactor: @(scale), SCStreamFrameInfoContentScale: @(fit)},
            (size_t)imageW, (size_t)imageH, CGRectMake(-100, 30, windowW, windowH)));
    }
}
PyObject *scapkit_test_window_frame(PyObject *self, PyObject *args)
{
    (void)self;
    @autoreleasepool {
        PyObject *arg;
        if (!PyArg_ParseTuple(args, "O", &arg)) { return NULL; }
        NSString *mode = text_arg(arg, 32, NO);
        if (!mode) { return NULL; }
        return run_python(5, NO, ^{ return SWTestFrame(mode); });
    }
}
