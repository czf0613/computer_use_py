/* Offline receiver for REAL CGEvents built by control.c. No event can reach
 * WindowServer: every post/warp is intercepted in this translation unit.
 * State behavior is based on the live macOS 26.6.2 probes: posted flags replace
 * session state; HID-tap posts ALSO replace hardware state. Private is not a
 * flags union. Delayed delivery is exposed through source event counters.
 * held is an offline observation of delivered constructor down/up intent,
 * independent of CF lifetime; it is NOT a WindowServer key-state model. */
#include <Python.h>
#include <CoreGraphics/CoreGraphics.h>
#include <pthread.h>
#include <string.h>
#include <stdlib.h>

typedef struct {
    int64_t source;
    CGEventFlags flags;
    bool keys[128];
    uint32_t counts[32];
} Source;
typedef struct {
    int64_t source;
    CGEventType type;
    CGEventFlags flags;
    int64_t key;
    CGEventTimestamp timestamp;
    CGEventTapLocation tap;
    CGPoint position;
    bool has_key_action;
    bool key_down;
} Entry;
/* FlagsChanged events do not distinguish a synthetic up from an externally
 * held modifier by flags alone. Preserve the real constructor's down/up intent
 * until posting copies it into Entry (including delayed deliveries). */
typedef struct KeyEvent {
    CGEventRef event;
    bool down;
    struct KeyEvent *next;
} KeyEvent;
static Source sources[512];
static Entry entries[4096];
static size_t source_count, entry_count;
static CGEventFlags hardware_flags, session_flags;
static Entry pending[4096];
static size_t pending_count;
static int delivery_polls, counter_polls;
static CGPoint cursor;
static pthread_mutex_t probe_mutex = PTHREAD_MUTEX_INITIALIZER;
static long live_objects;
static int fail_create;
static KeyEvent *key_events;
static int drop_main_up_remaining, dropped_main_up;
static bool overflowed;

/* All receiver globals are protected by probe_mutex. Never call Python while
 * holding it. Constructors reserve a live object before calling CoreGraphics,
 * so reset cannot mistake an acquisition in progress for a quiescent receiver. */
static void reserve_object(void)
{
    pthread_mutex_lock(&probe_mutex);
    live_objects++;
    pthread_mutex_unlock(&probe_mutex);
}

static void forget_object(void)
{
    pthread_mutex_lock(&probe_mutex);
    live_objects--;
    pthread_mutex_unlock(&probe_mutex);
}

/* Caller owns probe_mutex. Source/key history records delivered input, not CF
 * ownership: destroying a source must never hide an unmatched key-down. */
static void deliver(Entry entry)
{
    if (entry.type == kCGEventKeyUp && drop_main_up_remaining > 0) {
        drop_main_up_remaining--;
        dropped_main_up++;
        return;
    }
    if (entry.type == kCGEventMouseMoved || entry.type == kCGEventLeftMouseDragged ||
        entry.type == kCGEventRightMouseDragged || entry.type == kCGEventOtherMouseDragged) {
        cursor = entry.position;
    }
    int64_t id = entry.source;
    size_t index = 0;
    while (index < source_count && sources[index].source != id) { index++; }
    if (index == source_count && source_count < 512) {
        sources[source_count++].source = id;
    }
    if (index == 512) {
        overflowed = true;
        return;
    }
    if (index < 512) {
        CGEventType type = entry.type;
        CGEventFlags flags = entry.flags;
        int64_t key = entry.key;
        sources[index].flags = flags;
        if (type < 32) { sources[index].counts[type]++; }
        session_flags = flags;
        if (entry.tap == kCGHIDEventTap) { hardware_flags = flags; }
        if (key >= 0 && key < 128) {
            if (entry.has_key_action) {
                sources[index].keys[key] = entry.key_down;
            } else if (type == kCGEventKeyDown || type == kCGEventKeyUp ||
                       type == kCGEventFlagsChanged) {
                // Every production keyboard constructor must be intercepted.
                overflowed = true;
            }
        }
    }
}

static void receive_event(CGEventTapLocation tap, CGEventRef event)
{
    Entry entry = {
        .source = CGEventGetIntegerValueField(event, kCGEventSourceStateID),
        .type = CGEventGetType(event), .flags = CGEventGetFlags(event),
        .key = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode),
        .timestamp = CGEventGetTimestamp(event), .tap = tap,
        .position = CGEventGetLocation(event),
    };
    pthread_mutex_lock(&probe_mutex);
    for (KeyEvent *key_event = key_events; key_event; key_event = key_event->next) {
        if (key_event->event == event) {
            entry.has_key_action = true;
            entry.key_down = key_event->down;
            break;
        }
    }
    if (entry_count < 4096) { entries[entry_count++] = entry; }
    else { overflowed = true; }
    if (delivery_polls != 0) {
        if (pending_count < 4096) { pending[pending_count++] = entry; }
        else { overflowed = true; }
    } else { deliver(entry); }
    pthread_mutex_unlock(&probe_mutex);
}

static uint32_t probe_counter(CGEventSourceStateID id, CGEventType type)
{
    pthread_mutex_lock(&probe_mutex);
    counter_polls++;
    if (pending_count && delivery_polls > 0 && --delivery_polls == 0) {
        for (size_t i = 0; i < pending_count; i++) { deliver(pending[i]); }
        pending_count = 0;
    }
    uint32_t value = 0;
    for (size_t i = 0; i < source_count; i++) {
        if (sources[i].source == id && type < 32) { value = sources[i].counts[type]; }
    }
    pthread_mutex_unlock(&probe_mutex);
    return value;
}

static CGEventFlags probe_flags(CGEventSourceStateID id)
{
    pthread_mutex_lock(&probe_mutex);
    CGEventFlags flags = id == kCGEventSourceStateHIDSystemState ? hardware_flags : session_flags;
    pthread_mutex_unlock(&probe_mutex);
    return flags;
}

static CGEventRef position_event(CGEventSourceRef source) CF_RETURNS_RETAINED;
static CGEventRef position_event(CGEventSourceRef source)
{
    reserve_object();
    CGEventRef event = CGEventCreate(source);
    if (event) {
        pthread_mutex_lock(&probe_mutex);
        CGPoint position = cursor;
        pthread_mutex_unlock(&probe_mutex);
        CGEventSetLocation(event, position);
    } else { forget_object(); }
    return event;
}

static CGEventRef probe_create_mouse(CGEventSourceRef source, CGEventType type,
                             CGPoint point, CGMouseButton button) CF_RETURNS_RETAINED;
static CGEventRef probe_create_mouse(CGEventSourceRef source, CGEventType type,
                             CGPoint point, CGMouseButton button)
{
    reserve_object();
    CGEventRef event = CGEventCreateMouseEvent(source, type, point, button);
    if (!event) { forget_object(); }
    if (event && !source) { CGEventSetFlags(event, probe_flags(kCGEventSourceStateCombinedSessionState)); }
    return event;
}

static CGEventRef scroll_event(CGEventSourceRef source, CGScrollEventUnit units,
                              uint32_t count, int32_t y, int32_t x) CF_RETURNS_RETAINED;
static CGEventRef scroll_event(CGEventSourceRef source, CGScrollEventUnit units,
                              uint32_t count, int32_t y, int32_t x)
{
    reserve_object();
    CGEventRef event = CGEventCreateScrollWheelEvent(source, units, count, y, x);
    if (!event) { forget_object(); }
    if (event && !source) { CGEventSetFlags(event, probe_flags(kCGEventSourceStateCombinedSessionState)); }
    return event;
}

static CGError warp(CGPoint point)
{
    pthread_mutex_lock(&probe_mutex);
    cursor = point;
    pthread_mutex_unlock(&probe_mutex);
    return kCGErrorSuccess;
}

static CGEventRef probe_create_key(CGEventSourceRef source, CGKeyCode key, bool down) CF_RETURNS_RETAINED;
static CGEventRef probe_create_key(CGEventSourceRef source, CGKeyCode key, bool down)
{
    pthread_mutex_lock(&probe_mutex);
    bool fail = fail_create > 0 && --fail_create == 0;
    pthread_mutex_unlock(&probe_mutex);
    if (fail) { return NULL; }
    KeyEvent *key_event = malloc(sizeof *key_event);
    if (!key_event) { return NULL; }
    reserve_object();
    CGEventRef event = CGEventCreateKeyboardEvent(source, key, down);
    if (!event) {
        forget_object();
        free(key_event);
        return NULL;
    }
    key_event->event = event;
    key_event->down = down;
    pthread_mutex_lock(&probe_mutex);
    key_event->next = key_events;
    key_events = key_event;
    pthread_mutex_unlock(&probe_mutex);
    return event;
}
static CGEventSourceRef probe_source(CGEventSourceStateID state) CF_RETURNS_RETAINED;
static CGEventSourceRef probe_source(CGEventSourceStateID state)
{
    reserve_object();
    CGEventSourceRef source = CGEventSourceCreate(state);
    if (!source) { forget_object(); }
    return source;
}
static void probe_release(CFTypeRef CF_CONSUMED value)
{
    KeyEvent *removed = NULL;
    pthread_mutex_lock(&probe_mutex);
    for (KeyEvent **entry = &key_events; *entry; entry = &(*entry)->next) {
        if ((*entry)->event == value) {
            removed = *entry;
            *entry = removed->next;
            break;
        }
    }
    pthread_mutex_unlock(&probe_mutex);
    free(removed);
    CFRelease(value);
    forget_object();
}

#define CGEventPost receive_event
#define CGWarpMouseCursorPosition warp
#define CGEventCreate position_event
#define CGEventCreateMouseEvent probe_create_mouse
#define CGEventCreateScrollWheelEvent scroll_event
#define CGEventCreateKeyboardEvent probe_create_key
#define CGEventSourceCreate probe_source
#define CFRelease probe_release
#define CGEventSourceFlagsState probe_flags
#define CGEventSourceCounterForEventType probe_counter
#include "../../native_code/osx/src/control.c"
#undef CGEventPost
#undef CGWarpMouseCursorPosition
#undef CGEventCreate
#undef CGEventCreateMouseEvent
#undef CGEventCreateScrollWheelEvent
#undef CGEventCreateKeyboardEvent
#undef CGEventSourceCreate
#undef CFRelease
#undef CGEventSourceFlagsState
#undef CGEventSourceCounterForEventType

static PyObject *reset(PyObject *self, PyObject *args)
{
    /* Test setup only: callers must join operations before resetting. A timed
     * out stroke may leave pending entries while live_objects is already zero. */
    unsigned long long flags = 0x20000000;
    if (!PyArg_ParseTuple(args, "|K", &flags)) { return NULL; }
    long live;
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&probe_mutex);
    live = live_objects;
    if (!live) {
        memset(sources, 0, sizeof sources);
        source_count = entry_count = 0;
        fail_create = 0;
        delivery_polls = counter_polls = 0;
        drop_main_up_remaining = dropped_main_up = 0;
        overflowed = false;
        pending_count = 0;
        hardware_flags = session_flags = flags;
        cursor = CGPointMake(0, 0);
    }
    pthread_mutex_unlock(&probe_mutex);
    Py_END_ALLOW_THREADS
    if (live) {
        PyErr_SetString(PyExc_AssertionError, "previous operation leaked native objects or is still running");
        return NULL;
    }
    Py_RETURN_NONE;
}

typedef struct {
    Source sources[512];
    Entry entries[4096];
    size_t source_count, entry_count, pending_count;
    CGEventFlags hardware_flags, session_flags;
    CGPoint cursor;
    long live_objects;
    int counter_polls, dropped_main_up;
    bool overflowed;
} Snapshot;

static PyObject *state(PyObject *self, PyObject *args)
{
    /* Polling during worker-thread input is safe. Copy under the lock, then
     * construct all Python objects after unlocking; allocations can run GC. */
    Snapshot *snapshot = malloc(sizeof *snapshot);
    if (!snapshot) { return PyErr_NoMemory(); }
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&probe_mutex);
    snapshot->source_count = source_count;
    snapshot->entry_count = entry_count;
    snapshot->pending_count = pending_count;
    snapshot->hardware_flags = hardware_flags;
    snapshot->session_flags = session_flags;
    snapshot->cursor = cursor;
    snapshot->live_objects = live_objects;
    snapshot->counter_polls = counter_polls;
    snapshot->dropped_main_up = dropped_main_up;
    snapshot->overflowed = overflowed;
    memcpy(snapshot->sources, sources, source_count * sizeof *sources);
    memcpy(snapshot->entries, entries, entry_count * sizeof *entries);
    pthread_mutex_unlock(&probe_mutex);
    Py_END_ALLOW_THREADS
    PyObject *events = PyList_New(0), *held = PyList_New(0);
    if (!events || !held) { goto error; }
    for (size_t i = 0; i < snapshot->entry_count; i++) {
        Entry e = snapshot->entries[i];
        PyObject *value = Py_BuildValue("(LiKLK)", e.source, e.type, e.flags, e.key, e.timestamp);
        if (!value || PyList_Append(events, value) < 0) {
            Py_XDECREF(value);
            goto error;
        }
        Py_DECREF(value);
    }
    for (size_t i = 0; i < snapshot->source_count; i++) {
        for (size_t key = 0; key < 128; key++) {
            if (snapshot->sources[i].keys[key]) {
                PyObject *value = Py_BuildValue("(Li)", snapshot->sources[i].source, (int)key);
                if (!value || PyList_Append(held, value) < 0) {
                    Py_XDECREF(value);
                    goto error;
                }
                Py_DECREF(value);
            }
        }
    }
    PyObject *result = Py_BuildValue("{s:K,s:O,s:O,s:l,s:K,s:n,s:i,s:i,s:O,s:(dd)}",
        "flags", snapshot->session_flags, "events", events, "held", held,
        "live_objects", snapshot->live_objects, "hid_flags", snapshot->hardware_flags,
        "pending", (Py_ssize_t)snapshot->pending_count, "counter_polls", snapshot->counter_polls,
        "dropped_main_up", snapshot->dropped_main_up,
        "overflowed", snapshot->overflowed ? Py_True : Py_False,
        "cursor", snapshot->cursor.x, snapshot->cursor.y);
    Py_DECREF(events);
    Py_DECREF(held);
    free(snapshot);
    return result;

error:
    Py_XDECREF(events);
    Py_XDECREF(held);
    free(snapshot);
    return NULL;
}

static PyObject *fail_event(PyObject *self, PyObject *args)
{
    int fail_at;
    if (!PyArg_ParseTuple(args, "i", &fail_at)) { return NULL; }
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&probe_mutex);
    fail_create = fail_at;
    pthread_mutex_unlock(&probe_mutex);
    Py_END_ALLOW_THREADS
    Py_RETURN_NONE;
}

static PyObject *delay_delivery(PyObject *self, PyObject *args)
{
    int polls;
    if (!PyArg_ParseTuple(args, "i", &polls)) { return NULL; }
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&probe_mutex);
    delivery_polls = polls;
    pthread_mutex_unlock(&probe_mutex);
    Py_END_ALLOW_THREADS
    Py_RETURN_NONE;
}

/* Drop the next count ordinary key-up deliveries, not modifier releases.
 * events still logs posts; dropped entries do not update counters or held.
 * held proves that the ordinary key-up was never delivered, even after source
 * disposal. A counter acknowledgment covering key-up must report a timeout. */
static PyObject *drop_main_up(PyObject *self, PyObject *args)
{
    int count = 1;
    if (!PyArg_ParseTuple(args, "|i:drop_main_up", &count)) { return NULL; }
    if (count < 0) {
        PyErr_SetString(PyExc_ValueError, "drop count must be nonnegative");
        return NULL;
    }
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&probe_mutex);
    drop_main_up_remaining = count;
    pthread_mutex_unlock(&probe_mutex);
    Py_END_ALLOW_THREADS
    Py_RETURN_NONE;
}

static PyObject *change_hardware(PyObject *self, PyObject *args)
{
    unsigned long long flags;
    if (!PyArg_ParseTuple(args, "K", &flags)) { return NULL; }
    Py_BEGIN_ALLOW_THREADS
    pthread_mutex_lock(&probe_mutex);
    hardware_flags = session_flags = flags;
    pthread_mutex_unlock(&probe_mutex);
    Py_END_ALLOW_THREADS
    Py_RETURN_NONE;
}

static PyMethodDef methods[] = {
    {"reset", reset, METH_VARARGS, NULL},
    {"state", state, METH_NOARGS, NULL},
    {"fail_event", fail_event, METH_VARARGS, NULL},
    {"delay_delivery", delay_delivery, METH_VARARGS, NULL},
    {"drop_main_up", drop_main_up, METH_VARARGS, NULL},
    {"hardware", change_hardware, METH_VARARGS, NULL},
    {"keyboard_click", scapkit_keyboard_click, METH_VARARGS, NULL},
    {"keyboard_begin", scapkit_keyboard_begin, METH_VARARGS, NULL},
    {"keyboard_end", scapkit_keyboard_end, METH_VARARGS, NULL},
    {"mouse_click", scapkit_mouse_click, METH_VARARGS, NULL},
    {"mouse_scroll", scapkit_mouse_scroll, METH_VARARGS, NULL},
    {"move_mouse", scapkit_move_mouse, METH_VARARGS, NULL},
    {"drag_mouse", scapkit_drag_mouse, METH_VARARGS, NULL},
    {"get_mouse_position", scapkit_get_mouse_position, METH_NOARGS, NULL},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_input_probe", NULL, -1, methods};
PyMODINIT_FUNC PyInit__input_probe(void)
{
    PyObject *result = PyModule_Create(&module);
#ifdef Py_GIL_DISABLED
    if (result && PyUnstable_Module_SetGIL(result, Py_MOD_GIL_NOT_USED) < 0) {
        Py_DECREF(result); return NULL;
    }
#endif
    return result;
}
