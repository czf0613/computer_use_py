#include "window_backend.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <sstream>
#include <utility>

namespace scap::window
{
void Token::consume()
{
    if (creator != GetCurrentProcessId() || consumed.exchange(true) || Clock::now() - issued >= std::chrono::seconds(60))
    { fail("stale_window"); }
}
void Work::checkpoint()
{
    if (abandoned || phase == 3) { fail("backend_busy"); }
    if (Clock::now() >= deadline) { fail(timeout_code); }
}
void Work::dispatch()
{
    checkpoint();
    int expected = 1;
    if (!phase.compare_exchange_strong(expected, 2)) { fail("backend_busy"); }
}
Ref Registry::insert(Kind kind, Entry value)
{
    if (entries.size() >= max_entries) { fail("resource_limit"); }
    auto token = std::make_shared<Token>(next++, kind);
    value.token = token;
    entries.emplace(token->id, std::move(value));
    return token;
}
Entry &Registry::get(const Ref &ref, Kind kind)
{
    if (!ref || ref->creator != GetCurrentProcessId() || ref->kind != kind) { fail("identity_unverifiable"); }
    if (kind == Kind::session && ref->closed) { fail("session_closed"); }
    auto item = entries.find(ref->id);
    if (item == entries.end()) { fail(kind == Kind::element ? "stale_element" : "stale_window"); }
    if (item->second.token.lock() != ref) { fail("identity_unverifiable"); }
    return item->second;
}
void Registry::reap()
{
    std::erase_if(entries, [](const auto &pair)
    {
        auto token = pair.second.token.lock();
        if (!token) { return true; }
        if (token->kind == Kind::capture_ref || token->kind == Kind::accessible_ref)
        { return Clock::now() - token->issued > std::chrono::seconds(60); }
        // Drop closed-session elements first; explicit close still owns its Token
        // and cleanup report. A GC'd session can now lose its last native owner.
        return pair.second.owner && pair.second.owner->closed;
    });
    std::erase_if(entries, [](const auto &pair) { return pair.second.token.expired(); });
}
void Registry::identity(Result &out, Entry &entry)
{
    auto primary = entry.primary();
    if (!primary) { fail("session_closed"); }
    primary->refresh();
    if (entry.semantic && entry.semantic != primary) { entry.semantic->refresh(); }
    out.generation = primary->generation;
    out.process_instance = primary->process_instance;
    out.revision = primary->revision;
    out.geometry = primary->geometry;
}
Result Registry::list(bool accessible, DWORD pid, size_t limit)
{
    auto targets = enumerate(accessible, pid, limit);
    Result out; out.type = Result::Type::infos;
    for (auto &target : targets)
    {
        Entry value;
        (accessible ? value.semantic : value.capture) = target;
        auto ref = insert(accessible ? Kind::accessible_ref : Kind::capture_ref, std::move(value));
        std::ostringstream id;
        id << "0x" << std::hex << reinterpret_cast<uintptr_t>(target->hwnd);
        out.infos.push_back({ref, target->generation, target->pid, target->process_instance,
                            id.str(), target->title, target->geometry, !accessible, accessible});
    }
    return out;
}
Result Registry::open(const Ref &candidate, bool accessible)
{
    Entry value = get(candidate, accessible ? Kind::accessible_ref : Kind::capture_ref);
    auto target = value.primary();
    target->validate();
    auto session = insert(Kind::session, std::move(value));
    entries.erase(candidate->id);
    Result out; out.type = Result::Type::opened; out.handle = session;
    identity(out, get(session, Kind::session));
    return out;
}
Result Registry::close(const Ref &session)
{
    // closed was set atomically by the entry point, even if the actor is hung.
    std::vector<std::shared_ptr<Life>> lives;
    auto found = entries.find(session->id);
    if (found != entries.end())
    {
        if (found->second.capture) { lives.push_back(found->second.capture->life); }
        if (found->second.semantic) { lives.push_back(found->second.semantic->life); }
    }
    std::erase_if(entries, [&](const auto &pair)
    { return pair.first == session->id || pair.second.owner == session; });
    for (const auto &life : lives)
    { if (life->cleanup_failed) { session->cleanup_failed = true; } }
    if (session->cleanup_failed) { fail("backend_busy"); }
    return {};
}
Result Registry::attach(const Ref &session, const Ref &candidate)
{
    auto &current = get(session, Kind::session);
    auto &proposed = get(candidate, Kind::accessible_ref);
    if (!current.capture || current.semantic) { fail("identity_unverifiable"); }
    auto capture = current.capture;
    auto semantic = proposed.semantic;
    capture->validate(); semantic->validate();
    if (capture->hwnd != semantic->hwnd || capture->pid != semantic->pid ||
        capture->process_instance != semantic->process_instance || capture->synthetic != semantic->synthetic)
    { fail("identity_unverifiable"); }
    // The explicit root has already proved its HWND, process mark and retained RuntimeId.
    // The capture target is the original WGC item, with uninterrupted destruction monitoring.
    if (!semantic->semantic->same_root(*semantic->semantic)) { fail("identity_unverifiable"); }
    current.semantic = semantic;
    entries.erase(candidate->id);
    return {};
}
void Registry::validate_snapshot(Entry &session, uint64_t epoch, uint64_t revision)
{
    Result current;
    identity(current, session);
    if (!session.semantic || session.snapshot.empty() || session.epoch != epoch ||
        session.semantic->life->epoch != epoch || Clock::now() - session.snapshot_time >= std::chrono::seconds(30))
    { fail("stale_element"); }
    if (current.revision != revision || session.revision != revision) { fail("stale_geometry"); }
    for (auto id : session.snapshot)
    {
        auto it = entries.find(id);
        if (it == entries.end()) { fail("stale_element"); }
        auto &element = it->second;
        if (session.semantic->semantic->validate_node(element.node, current.geometry) != element.data)
        { session.semantic->life->changed(); fail("stale_element"); }
    }
    if (session.semantic->life->epoch != epoch) { fail("stale_element"); }
}
Result Registry::inspect(const Ref &handle, const std::string &query, int depth, int nodes,
                         uint64_t epoch, uint64_t revision)
{
    auto &entry = get(handle, Kind::session);
    Result out;
    identity(out, entry);
    if (query == "capabilities")
    {
        out.type = Result::Type::context;
        out.capture = bool(entry.capture); out.inspect = bool(entry.semantic);
        out.minimized = out.geometry.minimized;
        if (entry.semantic)
        {
            out.no_focus = entry.semantic->semantic->no_focus();
            out.modal = entry.semantic->semantic->modal();
            out.subject = entry.semantic->semantic->subject();
        }
        return out;
    }
    if (!entry.semantic) { fail("unsupported_background_operation"); }
    if (query == "validate_snapshot")
    {
        validate_snapshot(entry, epoch, revision);
        return {};
    }
    for (auto id : entry.snapshot) { entries.erase(id); }
    entry.snapshot.clear();
    auto target = entry.semantic;
    target->life->changed();
    out.epoch = target->life->epoch.load();
    auto geometry = out.geometry;
    auto rows = target->semantic->tree(depth, nodes, geometry);
    identity(out, entry);
    if (geometry != out.geometry) { fail("geometry_changed"); }
    if (out.epoch != target->life->epoch) { fail("stale_element"); }
    for (auto &row : rows)
    {
        Entry item;
        item.owner = handle; item.node = row.node; item.data = row.data;
        item.epoch = out.epoch; item.revision = out.revision;
        auto ref = insert(Kind::element, std::move(item));
        out.rows.push_back({ref, row.data, row.parent, row.depth});
        entry.snapshot.push_back(ref->id);
    }
    entry.epoch = out.epoch; entry.revision = out.revision; entry.snapshot_time = Clock::now();
    out.type = Result::Type::snapshot;
    return out;
}
Result Registry::screenshot(const Ref &handle, const std::string &format, int quality, Clock::time_point deadline)
{
    auto &entry = get(handle, Kind::session);
    if (!entry.capture) { fail("unsupported_background_operation"); }
    Result out;
    identity(out, entry);
    if (out.geometry.minimized) { fail("window_minimized"); }
    auto geometry = out.geometry;
    auto revision = out.revision;
    if (Clock::now() >= deadline) { fail("capture_timeout"); }
    out.pixels = entry.capture->capture->capture(geometry, deadline, out.captured_at);
    if (Clock::now() >= deadline) { fail("capture_timeout"); }
    identity(out, entry);
    if (geometry != out.geometry || out.revision != revision) { fail("geometry_changed"); }
    if (Clock::now() >= deadline) { fail("capture_timeout"); }
    if (!out.pixels.width || !out.pixels.height || out.pixels.width > 16384 || out.pixels.height > 16384 ||
        static_cast<uint64_t>(out.pixels.width) * out.pixels.height * 4 > max_bytes ||
        out.pixels.stride < out.pixels.width * 4 ||
        static_cast<uint64_t>(out.pixels.stride) * out.pixels.height != out.pixels.data.size() ||
        out.pixels.data.size() > max_bytes) { fail("capture_unavailable"); }
    if (format == "jpeg") { out.encoded = encode_window_jpeg(out.pixels, quality); }
    identity(out, entry);
    if (geometry != out.geometry || out.revision != revision) { fail("geometry_changed"); }
    if (Clock::now() >= deadline) { fail("capture_timeout"); }
    out.mapping = out.pixels.width == out.geometry.bounds.width && out.pixels.height == out.geometry.bounds.height;
    out.format = format;
    static uint64_t next_frame = 1;
    out.frame_id = std::to_string(handle->id) + ":" + std::to_string(next_frame++);
    out.type = Result::Type::frame;
    return out;
}
Result Registry::action(const Ref &handle, const Ref &element, uint64_t epoch, uint64_t revision,
                       const std::string &action, const std::wstring &replacement, const Grant &grant,
                       const std::string &operation, Work &work)
{
    auto &entry = get(handle, Kind::session);
    auto &item = get(element, Kind::element);
    if (entry.uncertain) { fail("outcome_unknown"); }
    if (item.owner != handle || item.epoch != epoch || item.revision != revision) { fail("stale_element"); }
    validate_snapshot(entry, epoch, revision);
    auto target = entry.semantic;
    auto &semantic = *target->semantic;
    if (target->geometry.minimized) { fail("window_minimized"); }
    auto modal = semantic.modal();
    if (!modal || *modal) { fail("modal_blocked"); }
    if (!semantic.no_focus()) { fail("unsupported_background_operation"); }
    if (revoked.contains(grant.profile) || grant.subject.empty() || semantic.subject() != grant.subject)
    { fail("background_not_verified"); }
    auto data = semantic.validate_node(item.node, entry.primary()->geometry);
    bool permitted = std::any_of(grant.rules.begin(), grant.rules.end(), [&](const Rule &rule)
    { return rule.identifier == data.identifier && rule.role == data.role && rule.action == action; });
    if (!permitted) { fail("background_not_verified"); }
    if (data.enabled != true || data.password != false ||
        (action == "set_value" && (data.editable != true || !data.value))) { fail("element_not_editable"); }
    if (action == "invoke" && !data.invoke) { fail("unsupported_background_operation"); }
    if (target->life->epoch != epoch || handle->closed) { fail("stale_element"); }
    HWND foreground = target->synthetic ? nullptr : GetForegroundWindow();
    work.dispatch(); // Atomic with the caller's timeout suppression; never auto retry.
    Result out; out.type = Result::Type::receipt; out.operation = operation;
    try
    {
        out.effect = semantic.act(item.node, action, replacement);
        if (!target->synthetic && foreground != GetForegroundWindow() && GetForegroundWindow() == target->hwnd)
        {
            revoked.insert(grant.profile); target->life->invalidate();
            throw Failure{"interference_detected", "UIA", 0, "unknown"};
        }
        if (work.abandoned || handle->closed || target->life->stale)
        { throw Failure{"outcome_unknown", "UIA", 0, "unknown"}; }
        target->life->changed(); // Writes invalidate this snapshot, even for a silent provider.
        return out;
    }
    catch (const Failure &failure)
    {
        entry.uncertain = true;
        if (std::string(failure.code) == "interference_detected") { throw; }
        throw Failure{"outcome_unknown", failure.domain, failure.native, "unknown"};
    }
    catch (...)
    {
        entry.uncertain = true;
        throw Failure{"outcome_unknown", "UIA", 0, "unknown"};
    }
}
Actor::Actor()
{
    // Detached work owns no interpreter objects. Pin code before allowing late callbacks.
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                           reinterpret_cast<LPCWSTR>(&actor), &module)) { fail("backend_unavailable"); }
    std::thread([this] { run(); }).detach();
}
void Actor::run() noexcept
{
    HRESULT initialized_com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    {
        std::lock_guard lock(mutex);
        if (FAILED(initialized_com)) { startup_error = Failure{"backend_unavailable", "COM", initialized_com}; }
        initialized = true;
    }
    changed.notify_all();
    if (FAILED(initialized_com)) { return; }
    Registry registry;
    for (;;)
    {
        Job job;
        {
            std::unique_lock lock(mutex);
            changed.wait_for(lock, std::chrono::milliseconds(50), [&] { return !jobs.empty(); });
            if (!jobs.empty()) { job = std::move(jobs.front()); jobs.pop_front(); }
        }
        if (job.first)
        {
            auto &work = *job.first;
            try
            {
                int expected = 0;
                if (!work.phase.compare_exchange_strong(expected, 1)) { fail("backend_busy"); }
                work.checkpoint();
                work.result = job.second(registry, work);
            }
            catch (const Failure &failure) { work.result.error = failure; }
            catch (const winrt::hresult_error &error)
            {
                work.result.error = Failure{error.code() == E_ACCESSDENIED ? "permission_denied" : "capture_unavailable",
                                            "HRESULT", error.code().value};
            }
            catch (...) { work.result.error = Failure{"resource_limit"}; }
            {
                std::lock_guard lock(work.mutex);
                work.done = true;
            }
            work.changed.notify_all();
        }
        // Sweep only on the owning MTA. If a provider blocks release, the next caller
        // times out and quarantines this same actor instead of spawning another one.
        try { registry.reap(); } catch (...) { std::lock_guard lock(mutex); quarantine = true; }
    }
}
Result Actor::call(std::function<Result(Registry &, Work &)> function, double timeout, bool cleanup,
                   bool write, bool capture_wait)
{
    auto work = std::make_shared<Work>();
    work->deadline = Clock::now() + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(timeout));
    work->timeout_code = capture_wait ? "capture_timeout" : "backend_busy";
    // WGC must observe the SAME deadline, then close its frame/session/pool.
    // This fixed grace is only for completion/cleanup, never another frame budget.
    // Native creation, copy, encoding or cleanup that does not return within it
    // still quarantines this actor; no replacement actor or unbounded wait.
    const auto completion_deadline = work->deadline + std::chrono::seconds(capture_wait ? 1 : 0);
    {
        std::unique_lock lock(mutex);
        if (!changed.wait_until(lock, completion_deadline, [&] { return initialized; }))
        { quarantine = true; throw Failure{"backend_busy"}; }
        if (startup_error) { throw *startup_error; }
        if ((quarantine && !cleanup) || jobs.size() >= 64) { fail("backend_busy"); }
        jobs.emplace_back(work, std::move(function));
    }
    changed.notify_one();
    std::unique_lock lock(work->mutex);
    if (!work->changed.wait_until(lock, completion_deadline, [&] { return work->done; }))
    {
        work->abandoned = true;
        int phase = work->phase.load();
        while (phase != 2 && phase != 3 && !work->phase.compare_exchange_weak(phase, 3)) {}
        { std::lock_guard actor_lock(mutex); quarantine = true; }
        const char *domain = cleanup ? "actor.cleanup_incomplete" :
                             capture_wait ? "actor.cleanup_unknown" : "actor";
        throw Failure{write && phase == 2 ? "outcome_unknown" : "backend_busy", domain, WAIT_TIMEOUT,
                       write && phase == 2 ? "unknown" : "not_dispatched", "never"};
    }
    return std::move(work->result);
}
Actor &actor()
{
    static auto *value = new Actor(); // Never replace a timed-out actor or destroy COM on an executor.
    return *value;
}
namespace
{
constexpr const char *names[] = {"scapkit.window_ref.v1", "scapkit.accessible_window_ref.v1",
                                "scapkit.window_session.v1", "scapkit.window_element.v1"};
struct PythonFailure {};
struct Py
{
    PyObject *value = nullptr;
    explicit Py(PyObject *v) : value(v) { if (!value) { throw PythonFailure{}; } }
    ~Py() { Py_XDECREF(value); }
    Py(const Py &) = delete;
    PyObject *release() { return std::exchange(value, nullptr); }
};
PyObject *none() { Py_INCREF(Py_None); return Py_None; }
PyObject *string(const std::string &value) { return PyUnicode_FromStringAndSize(value.data(), static_cast<Py_ssize_t>(value.size())); }
void put(PyObject *dict, const char *key, PyObject *value)
{
    Py owned(value);
    if (PyDict_SetItemString(dict, key, owned.value) < 0) { throw PythonFailure{}; }
}
void append(PyObject *list, PyObject *value)
{
    Py owned(value);
    if (PyList_Append(list, owned.value) < 0) { throw PythonFailure{}; }
}
PyObject *boolean(std::optional<bool> value)
{ return value ? PyBool_FromLong(*value) : none(); }
PyObject *rect(const Rect &value)
{
    return Py_BuildValue("{s:d,s:d,s:d,s:d}", "x", value.x, "y", value.y, "width", value.width, "height", value.height);
}
void destroy_ref(PyObject *capsule) noexcept
{
    const char *name = PyCapsule_GetName(capsule);
    if (!name) { PyErr_Clear(); return; }
    auto *ref = static_cast<Ref *>(PyCapsule_GetPointer(capsule, name));
    if (!ref) { PyErr_Clear(); return; }
    // Only plain native Token state is released. COM is reaped by the owning actor.
    if ((*ref)->kind == Kind::session) { (*ref)->closed = true; }
    delete ref;
}
PyObject *capsule(const Ref &ref)
{
    auto value = std::make_unique<Ref>(ref);
    PyObject *result = PyCapsule_New(value.get(), names[static_cast<int>(ref->kind)], destroy_ref);
    if (result) { value.release(); }
    return result;
}
Ref parse_ref(PyObject *obj, Kind kind)
{
    auto *value = static_cast<Ref *>(PyCapsule_GetPointer(obj, names[static_cast<int>(kind)]));
    if (!value) { throw PythonFailure{}; }
    if ((*value)->creator != GetCurrentProcessId() || (*value)->kind != kind) { fail("identity_unverifiable"); }
    return *value;
}
void identity_fields(PyObject *out, const Result &value)
{
    put(out, "generation", PyLong_FromUnsignedLongLong(value.generation));
    put(out, "process_instance", string(value.process_instance));
}
void revision_fields(PyObject *out, const Result &value)
{
    identity_fields(out, value);
    put(out, "geometry_revision", PyLong_FromUnsignedLongLong(value.revision));
}
PyObject *subject_object(const Subject &subject)
{
    if (subject.empty()) { return none(); }
    Py result(PyDict_New());
    for (auto &[key, value] : subject) { put(result.value, key.c_str(), string(value)); }
    return result.release();
}
PyObject *value_object(const Result &value)
{
    if (value.type == Result::Type::none) { return none(); }
    if (value.type == Result::Type::infos)
    {
        Py rows(PyList_New(0));
        for (auto &info : value.infos)
        {
            Py row(PyDict_New());
            put(row.value, "ref", capsule(info.ref));
            put(row.value, "platform", string("windows"));
            put(row.value, "native_id", string(info.native_id));
            put(row.value, "pid", PyLong_FromUnsignedLong(info.pid));
            put(row.value, "generation", PyLong_FromUnsignedLongLong(info.generation));
            put(row.value, "process_instance", string(info.process_instance));
            put(row.value, "title", string(info.title));
            put(row.value, "bounds", rect(info.geometry.bounds));
            put(row.value, "coordinate_unit", string("pixels"));
            put(row.value, "visible", boolean(info.geometry.visible));
            put(row.value, "minimized", boolean(info.geometry.minimized));
            put(row.value, "capture_support", string(info.capture ? "supported" : "unknown"));
            put(row.value, "inspect_support", string(info.inspect ? "supported" : "unknown"));
            append(rows.value, row.release());
        }
        return rows.release();
    }
    Py out(PyDict_New());
    if (value.type == Result::Type::opened)
    {
        identity_fields(out.value, value);
        put(out.value, "handle", capsule(value.handle));
    }
    else if (value.type == Result::Type::context)
    {
        revision_fields(out.value, value);
        put(out.value, "identity_status", string("verified"));
        put(out.value, "capture", string(value.capture ? "supported" : "unsupported"));
        put(out.value, "inspect_elements", string(value.inspect ? "supported" : "unsupported"));
        // Global support means a provider may offer a pattern; per-control inspection is authoritative.
        put(out.value, "set_value", string(value.inspect ? "supported" : "unsupported"));
        put(out.value, "invoke", string(value.inspect ? "supported" : "unsupported"));
        put(out.value, "minimized", boolean(value.minimized));
        put(out.value, "modal", boolean(value.modal));
        put(out.value, "focus_policy", string(value.no_focus ? "no_activation" : "unverified"));
        put(out.value, "subject", subject_object(value.subject));
    }
    else if (value.type == Result::Type::snapshot)
    {
        revision_fields(out.value, value);
        put(out.value, "tree_epoch", PyLong_FromUnsignedLongLong(value.epoch));
        put(out.value, "coordinate_unit", string("pixels"));
        put(out.value, "mapping_status", string("verified"));
        Py rows(PyList_New(0));
        for (auto &item : value.rows)
        {
            Py row(PyDict_New());
            put(row.value, "ref", capsule(item.ref));
            put(row.value, "identifier", string(item.data.identifier));
            put(row.value, "name", string(item.data.name));
            put(row.value, "role", string(item.data.role));
            put(row.value, "bounds", item.data.bounds ? rect(*item.data.bounds) : none());
            put(row.value, "enabled", boolean(item.data.enabled));
            put(row.value, "editable", boolean(item.data.editable));
            put(row.value, "password", boolean(item.data.password));
            put(row.value, "depth", PyLong_FromLong(item.depth));
            put(row.value, "parent_index", item.parent < 0 ? none() : PyLong_FromLong(item.parent));
            Py actions(PyList_New(0));
            if (item.data.value) { append(actions.value, string("set_value")); }
            if (item.data.invoke) { append(actions.value, string("invoke")); }
            put(row.value, "actions", actions.release());
            append(rows.value, row.release());
        }
        put(out.value, "elements", rows.release());
    }
    else if (value.type == Result::Type::frame)
    {
        revision_fields(out.value, value);
        const auto &pixels = value.pixels;
        const auto &bytes = value.format == "bgra" ? pixels.data : value.encoded;
        put(out.value, "frame_id", string(value.frame_id));
        put(out.value, "captured_at", PyFloat_FromDouble(value.captured_at));
        put(out.value, "state", string("complete"));
        put(out.value, "format", string(value.format));
        put(out.value, "data", PyBytes_FromStringAndSize(reinterpret_cast<const char *>(bytes.data()), static_cast<Py_ssize_t>(bytes.size())));
        put(out.value, "image_width", PyLong_FromUnsignedLong(pixels.width));
        put(out.value, "image_height", PyLong_FromUnsignedLong(pixels.height));
        put(out.value, "stride", value.format == "bgra" ? PyLong_FromUnsignedLong(pixels.stride) : none());
        put(out.value, "window_bounds", rect(value.geometry.bounds));
        put(out.value, "local_rect", value.mapping ? rect({0, 0, value.geometry.bounds.width, value.geometry.bounds.height}) : none());
        put(out.value, "image_content_rect_px", value.mapping ? rect({0, 0, double(pixels.width), double(pixels.height)}) : none());
        put(out.value, "client_rect_local", value.geometry.client ? rect(*value.geometry.client) : none());
        put(out.value, "coordinate_unit", string("pixels"));
        put(out.value, "image_to_local", value.mapping ? Py_BuildValue("(dddddd)", 1.0, 0.0, 0.0, 1.0, 0.0, 0.0) : none());
        put(out.value, "mapping_status", string(value.mapping ? "verified" : "unavailable"));
    }
    else if (value.type == Result::Type::receipt)
    {
        put(out.value, "operation_id", string(value.operation));
        put(out.value, "dispatch_state", string("accepted"));
        put(out.value, "effect_state", string(value.effect ? "verified" : "unknown"));
    }
    return out.release();
}
PyObject *failure_object(const Failure &failure)
{
    Py error(Py_BuildValue("{s:s,s:s,s:L,s:s,s:s}", "code", failure.code, "native_domain", failure.domain,
                          "native_code", static_cast<long long>(failure.native), "dispatch_state", failure.dispatch,
                          "retry_hint", failure.retry));
    return Py_BuildValue("{s:O,s:O}", "ok", Py_False, "error", error.value);
}
template<class F> PyObject *boundary(F function) noexcept
{
    try { return function(); }
    catch (const PythonFailure &) { return nullptr; }
    catch (const std::bad_alloc &) { return PyErr_NoMemory(); }
    catch (const Failure &failure)
    {
        try { return failure_object(failure); } catch (...) { return nullptr; }
    }
    catch (...)
    {
        try { return failure_object({"backend_unavailable"}); } catch (...) { return nullptr; }
    }
}
std::string parse_string(PyObject *obj, size_t limit = 4096, bool empty = false)
{
    if (!PyUnicode_CheckExact(obj)) { PyErr_SetString(PyExc_TypeError, "expected str"); throw PythonFailure{}; }
    Py_ssize_t size = 0;
    const char *data = PyUnicode_AsUTF8AndSize(obj, &size);
    if (!data) { throw PythonFailure{}; }
    if (size < 0 || static_cast<size_t>(size) > limit || (!empty && !size) || std::memchr(data, 0, size))
    { PyErr_SetString(PyExc_ValueError, "invalid string size or NUL"); throw PythonFailure{}; }
    return std::string(data, size);
}
uint64_t integer(PyObject *obj, uint64_t low, uint64_t high)
{
    if (!PyLong_CheckExact(obj)) { PyErr_SetString(PyExc_TypeError, "expected int, not bool"); throw PythonFailure{}; }
    auto value = PyLong_AsUnsignedLongLong(obj);
    if (PyErr_Occurred()) { throw PythonFailure{}; }
    if (value < low || value > high) { PyErr_SetString(PyExc_ValueError, "integer outside bounds"); throw PythonFailure{}; }
    return value;
}
double seconds(PyObject *obj)
{
    if (!PyLong_CheckExact(obj) && !PyFloat_CheckExact(obj))
    { PyErr_SetString(PyExc_TypeError, "expected finite timeout"); throw PythonFailure{}; }
    double value = PyFloat_AsDouble(obj);
    if (PyErr_Occurred()) { throw PythonFailure{}; }
    if (!std::isfinite(value) || value <= 0 || value > 30)
    { PyErr_SetString(PyExc_ValueError, "timeout outside (0,30]"); throw PythonFailure{}; }
    return value;
}
void choice(const std::string &value, const char *a, const char *b)
{
    if (value != a && value != b) { PyErr_SetString(PyExc_ValueError, "invalid option"); throw PythonFailure{}; }
}
PyObject *field(PyObject *obj, const char *name)
{
    if (!PyDict_CheckExact(obj)) { PyErr_SetString(PyExc_TypeError, "expected dict"); throw PythonFailure{}; }
    PyObject *value = PyDict_GetItemString(obj, name); // private copied dictionary only
    if (!value) { PyErr_SetString(PyExc_ValueError, "missing field"); throw PythonFailure{}; }
    return value;
}
Grant grant_from(PyObject *authorization)
{
    Py module(PyImport_ImportModule("scapkit_computer_use.window_profiles"));
    Py validator(PyObject_GetAttrString(module.value, "_validate_authorization"));
    PyObject *validated = PyObject_CallFunctionObjArgs(validator.value, authorization, nullptr);
    if (!validated)
    {
        if (PyErr_ExceptionMatches(PyExc_MemoryError)) { throw PythonFailure{}; }
        PyErr_Clear(); fail("background_not_verified");
    }
    Py scope(validated);
    Grant grant;
    for (const char *key : {"platform", "application_id", "application_version", "application_build",
                             "environment", "backend_build", "window_identifier"})
    { grant.subject[key] = parse_string(field(scope.value, key)); }
    grant.profile = parse_string(field(scope.value, "profile_id"));
    PyObject *actions = field(scope.value, "actions");
    if (!PyList_CheckExact(actions) || PyList_Size(actions) < 1 || PyList_Size(actions) > 64)
    { fail("background_not_verified"); }
    for (Py_ssize_t i = 0; i < PyList_Size(actions); ++i)
    {
        auto *rule = PyList_GetItem(actions, i);
        Rule value{parse_string(field(rule, "identifier")), parse_string(field(rule, "role")), parse_string(field(rule, "action"))};
        choice(value.action, "set_value", "invoke");
        grant.rules.push_back(std::move(value));
    }
    return grant;
}
} // namespace
PyObject *result_object(const Result &result)
{
    if (result.error) { return failure_object(*result.error); }
    Py value(value_object(result));
    return Py_BuildValue("{s:O,s:O}", "ok", Py_True, "value", value.value);
}
#ifdef SCAPKIT_TESTING
PyObject *py_test_window(PyObject *, PyObject *args)
{
    return boundary([&]() -> PyObject *
    {
        PyObject *command, *obj = Py_None;
        if (!PyArg_ParseTuple(args, "O|O", &command, &obj)) { return nullptr; }
        auto name = parse_string(command);
        Ref ref;
        if (obj != Py_None) { ref = parse_ref(obj, Kind::session); }
        Result result;
        {
            NoPython detached;
            // Test-only observation/release of a deliberately blocked synthetic
            // capture. It cannot submit work or bypass a quarantined actor.
            auto control = test_capture_control(name);
            result = control ? std::move(*control) :
                actor().call([=](Registry &registry, Work &) { return registry.synthetic(name, ref); });
        }
        return result_object(result);
    });
}
#endif
} // namespace scap::window
namespace scap
{
using namespace window;
PyObject *py_window_enumerate(PyObject *, PyObject *args)
{
    return window::boundary([&]() -> PyObject *
    {
        PyObject *kind, *pid, *limit;
        if (!PyArg_ParseTuple(args, "OOO", &kind, &pid, &limit)) { return nullptr; }
        auto name = parse_string(kind); choice(name, "capture", "accessible");
        if (name == "accessible" && pid == Py_None) { PyErr_SetString(PyExc_TypeError, "accessible enumeration requires pid"); return nullptr; }
        DWORD process = pid == Py_None ? 0 : static_cast<DWORD>(integer(pid, 1, UINT32_MAX));
        auto count = integer(limit, 1, 256);
        Result result;
        { NoPython detached; result = actor().call([=](Registry &r, Work &) { return r.list(name == "accessible", process, count); }); }
        return result_object(result);
    });
}
PyObject *py_window_open(PyObject *, PyObject *args)
{
    return window::boundary([&]() -> PyObject *
    {
        PyObject *obj, *kind;
        if (!PyArg_ParseTuple(args, "OO", &obj, &kind)) { return nullptr; }
        auto name = parse_string(kind); choice(name, "capture", "accessible");
        auto ref = parse_ref(obj, name == "capture" ? Kind::capture_ref : Kind::accessible_ref);
        ref->consume();
        Result result;
        { NoPython detached; result = actor().call([=](Registry &r, Work &) { return r.open(ref, name == "accessible"); }); }
        return result_object(result);
    });
}
PyObject *py_window_close(PyObject *, PyObject *args)
{
    return window::boundary([&]() -> PyObject *
    {
        PyObject *obj;
        if (!PyArg_ParseTuple(args, "O", &obj)) { return nullptr; }
        auto ref = parse_ref(obj, Kind::session); ref->closed = true;
        Result result;
        { NoPython detached; result = actor().call([=](Registry &r, Work &) { return r.close(ref); }, 5, true); }
        return result_object(result);
    });
}
PyObject *py_window_attach(PyObject *, PyObject *args)
{
    return window::boundary([&]() -> PyObject *
    {
        PyObject *obj, *candidate;
        if (!PyArg_ParseTuple(args, "OO", &obj, &candidate)) { return nullptr; }
        auto ref = parse_ref(obj, Kind::session); auto semantic = parse_ref(candidate, Kind::accessible_ref);
        semantic->consume();
        Result result;
        { NoPython detached; result = actor().call([=](Registry &r, Work &) { return r.attach(ref, semantic); }); }
        return result_object(result);
    });
}
PyObject *py_window_screenshot(PyObject *, PyObject *args)
{
    return window::boundary([&]() -> PyObject *
    {
        PyObject *obj, *format, *quality, *timeout;
        if (!PyArg_ParseTuple(args, "OOOO", &obj, &format, &quality, &timeout)) { return nullptr; }
        auto ref = parse_ref(obj, Kind::session); auto encoding = parse_string(format); choice(encoding, "jpeg", "bgra");
        int compression = static_cast<int>(integer(quality, 0, 100)); double duration = seconds(timeout);
        Result result;
        {
            NoPython detached;
            result = actor().call([=](Registry &r, Work &work)
                { return r.screenshot(ref, encoding, compression, work.deadline); }, duration, false, false, true);
        }
        return result_object(result);
    });
}
PyObject *py_window_inspect(PyObject *, PyObject *args)
{
    return window::boundary([&]() -> PyObject *
    {
        PyObject *obj, *query, *options;
        if (!PyArg_ParseTuple(args, "OOO", &obj, &query, &options)) { return nullptr; }
        auto ref = parse_ref(obj, Kind::session); auto name = parse_string(query);
        if (!PyDict_CheckExact(options)) { PyErr_SetString(PyExc_TypeError, "expected options dict"); return nullptr; }
        Py copy(PyDict_Copy(options));
        int depth = 0, nodes = 0; uint64_t epoch = 0, revision = 0;
        if (name == "capabilities")
        {
            if (PyDict_Size(copy.value) != 0) { PyErr_SetString(PyExc_ValueError, "unexpected options"); return nullptr; }
        }
        else
        {
            if (PyDict_Size(copy.value) != 2) { PyErr_SetString(PyExc_ValueError, "expected two options"); return nullptr; }
            if (name == "elements")
            {
                depth = static_cast<int>(integer(field(copy.value, "max_depth"), 0, 32));
                nodes = static_cast<int>(integer(field(copy.value, "max_nodes"), 1, 4096));
            }
            else if (name == "validate_snapshot")
            {
                epoch = integer(field(copy.value, "tree_epoch"), 1, INT64_MAX);
                revision = integer(field(copy.value, "geometry_revision"), 1, INT64_MAX);
            }
            else { PyErr_SetString(PyExc_ValueError, "unknown query"); return nullptr; }
        }
        Result result;
        { NoPython detached; result = actor().call([=](Registry &r, Work &) { return r.inspect(ref, name, depth, nodes, epoch, revision); }); }
        return result_object(result);
    });
}
PyObject *py_window_action(PyObject *, PyObject *args)
{
    return window::boundary([&]() -> PyObject *
    {
        PyObject *obj, *element, *epoch, *revision, *action, *value, *authorization, *operation, *timeout;
        if (!PyArg_ParseTuple(args, "OOOOOOOOO", &obj, &element, &epoch, &revision, &action, &value,
                              &authorization, &operation, &timeout)) { return nullptr; }
        auto ref = parse_ref(obj, Kind::session); auto node = parse_ref(element, Kind::element);
        auto tree = integer(epoch, 1, INT64_MAX); auto geometry = integer(revision, 1, INT64_MAX);
        auto name = parse_string(action); choice(name, "set_value", "invoke");
        std::wstring replacement;
        if (name == "set_value") { replacement = wide(parse_string(value, 65536, true)); }
        else if (value != Py_None) { PyErr_SetString(PyExc_TypeError, "invoke value must be None"); return nullptr; }
        auto request = parse_string(operation, 128); double duration = seconds(timeout);
        auto grant = grant_from(authorization); // Attached state, no native lock, before dispatch.
        Result result;
        { NoPython detached; result = actor().call([=](Registry &r, Work &work)
            { return r.action(ref, node, tree, geometry, name, replacement, grant, request, work); }, duration, false, true); }
        return result_object(result);
    });
}
} // namespace scap
