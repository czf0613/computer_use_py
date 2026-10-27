#include "window_backend.h"
#ifdef SCAPKIT_TESTING
namespace scap::window
{
namespace
{
std::atomic<int> live_roots{0}, live_nodes{0}, wrong_thread_release{0};
// A test-only resource that is held by the real process Actor while capturing
// or cleaning up. Only observation/release bypass its queue, never native work.
std::atomic<int> live_captures{0}, completed_captures{0}, capture_blocked{0};
std::atomic<bool> capture_probe_expired{false};
std::mutex capture_probe_mutex;
std::condition_variable capture_probe_changed;
bool capture_probe_released = false;
void block_capture_probe(int phase)
{
    std::unique_lock lock(capture_probe_mutex);
    capture_blocked = phase;
    // Failsafe for a broken test: never leave an immortal synthetic blocker.
    if (!capture_probe_changed.wait_for(lock, std::chrono::seconds(20), [] { return capture_probe_released; }))
    { capture_probe_expired = true; }
    capture_blocked = 0;
}
struct SyntheticCaptureResource
{
    int mode;
    std::thread::id thread = std::this_thread::get_id();
    explicit SyntheticCaptureResource(int value) : mode(value) { ++live_captures; }
    ~SyntheticCaptureResource()
    {
        if (mode == 8) { block_capture_probe(2); }
        if (mode == 6) { std::this_thread::sleep_for(std::chrono::milliseconds(50)); }
        if (thread != std::this_thread::get_id()) { ++wrong_thread_release; }
        --live_captures;
        ++completed_captures;
    }
};
struct SyntheticNode final : Node
{
    std::thread::id thread = std::this_thread::get_id();
    int index;
    bool live = true;
    explicit SyntheticNode(int value) : index(value) { ++live_nodes; }
    ~SyntheticNode() override
    { --live_nodes; if (thread != std::this_thread::get_id()) { ++wrong_thread_release; } }
};
struct SyntheticSemantic final : Semantic
{
    std::weak_ptr<Target> target;
    std::array<std::shared_ptr<SyntheticNode>, 3> nodes;
    bool is_modal = false, password = false, readonly = false, focus = true;
    std::optional<bool> enabled = true;
    std::wstring stored;
    int invoked = 0;
    std::thread::id thread = std::this_thread::get_id();
    explicit SyntheticSemantic(const std::shared_ptr<Target> &value) : target(value)
    {
        for (int i = 0; i < 3; ++i) { nodes[i] = std::make_shared<SyntheticNode>(i); }
        ++live_roots;
    }
    ~SyntheticSemantic() override
    { --live_roots; if (thread != std::this_thread::get_id()) { ++wrong_thread_release; } }
    void validate() override
    { if (!nodes[0]->live) { target.lock()->life->invalidate(); fail("stale_window"); } }
    NodeData describe(int index)
    {
        NodeData data;
        data.identifier = index == 0 ? "fixture.window" : index == 1 ? "fixture.text" : "fixture.increment";
        data.raw_identifier = "fixture.instance-1." + data.identifier;
        data.role = index == 0 ? "UIA.Window" : index == 1 ? "UIA.Edit" : "UIA.Button";
        data.name = "Synthetic";
        data.bounds = index == 0 ? Rect{0, 0, 64, 32} : Rect{double(index * 16), 8, 12, 12};
        data.enabled = enabled; data.password = password; data.editable = index == 1 && !readonly;
        data.value = index == 1; data.invoke = index == 2;
        return data;
    }
    NodeData validate_node(const std::shared_ptr<Node> &value, const Geometry &) override
    {
        auto node = std::dynamic_pointer_cast<SyntheticNode>(value);
        if (!node || !node->live || nodes[node->index] != node) { fail("stale_element"); }
        return describe(node->index);
    }
    std::vector<TreeRow> tree(int depth, int limit, const Geometry &) override
    {
        std::vector<TreeRow> rows{{nodes[0], describe(0), -1, 0}};
        for (int i = 1; depth && i < 3 && i < limit; ++i) { rows.push_back({nodes[i], describe(i), 0, 1}); }
        return rows;
    }
    std::optional<bool> modal() override { return is_modal; }
    bool no_focus() override { return focus; }
    Subject subject() override
    {
        return {{"platform", "windows"}, {"application_id", "synthetic"}, {"application_version", "1"},
                {"application_build", "synthetic"}, {"environment", "offline"}, {"backend_build", "synthetic"},
                {"window_identifier", "fixture.window"}};
    }
    bool same_root(Semantic &other) override { return this == &other; }
    bool act(const std::shared_ptr<Node> &node, const std::string &action, const std::wstring &value) override
    {
        validate_node(node, {});
        if (action == "set_value") { stored = value; return stored == value; }
        ++invoked; return false;
    }
};
struct SyntheticCapture final : CaptureTarget
{
    std::weak_ptr<Target> target;
    int mode = 0;
    explicit SyntheticCapture(const std::shared_ptr<Target> &value) : target(value) {}
    Pixels capture(const Geometry &, Clock::time_point deadline, double &at) override
    {
        if (mode == 1) { target.lock()->geometry.bounds.width += 1; target.lock()->life->changed(true); }
        if (mode == 2) { target.lock()->life->invalidate(); }
        if (mode == 3) { throw Failure{"capture_unavailable", "HRESULT", DXGI_ERROR_DEVICE_REMOVED}; }
        if (mode == 4) { fail("capture_timeout"); }
        if (mode >= 6)
        {
            SyntheticCaptureResource resource(mode);
            if (mode == 7 || mode == 9) { block_capture_probe(1); }
            else { std::this_thread::sleep_until(deadline); }
            // Mode 9 is a slow but healthy capture, released by the test within
            // its own deadline while other callers time out in the real queue.
            if (mode != 9 || Clock::now() >= deadline) { fail("capture_timeout"); }
        }
        Pixels pixels; pixels.width = mode == 5 ? 60 : 64; pixels.height = 32; pixels.stride = pixels.width * 4;
        pixels.data.resize(static_cast<size_t>(pixels.stride) * pixels.height, 127);
        at = double(clock100()) / 10000000.0;
        return pixels;
    }
};
} // namespace
std::optional<Result> test_capture_control(const std::string &command)
{
    if (command == "capture_probe_release")
    {
        { std::lock_guard lock(capture_probe_mutex); capture_probe_released = true; }
        capture_probe_changed.notify_all();
        return Result{};
    }
    if (command == "capture_probe_state")
    {
        Result out; out.type = Result::Type::context;
        out.subject = {{"live", std::to_string(live_captures)}, {"completed", std::to_string(completed_captures)},
                       {"blocked", std::to_string(capture_blocked)}, {"expired", capture_probe_expired ? "1" : "0"},
                       {"wrong_thread_release", std::to_string(wrong_thread_release)}};
        return out;
    }
    return std::nullopt;
}
Result Registry::synthetic(const std::string &command, const Ref &session)
{
    if (command == "bstr")
    {
        test_bstr_value_pattern();
        return {};
    }
    if (command == "capture" || command == "accessible")
    {
        auto target = std::make_shared<Target>();
        target->synthetic = true; target->pid = 4242; target->process_instance = "synthetic-process-start-1";
        target->generation = next; target->hwnd = reinterpret_cast<HWND>(uintptr_t{0xfedcba9876543210});
        target->geometry.bounds = {-1920, -160, 64, 32}; target->geometry.dpi = 144; target->geometry.visible = true;
        Entry entry;
        if (command == "capture") { target->capture = std::make_unique<SyntheticCapture>(target); entry.capture = target; }
        else { target->semantic = std::make_unique<SyntheticSemantic>(target); entry.semantic = target; }
        auto token = insert(command == "capture" ? Kind::capture_ref : Kind::accessible_ref, std::move(entry));
        Result out; out.type = Result::Type::infos;
        out.infos.push_back({token, target->generation, target->pid, target->process_instance,
                            "0xfedcba9876543210", "Synthetic", target->geometry, command == "capture", command == "accessible"});
        return out;
    }
    if (command == "resources")
    {
        reap();
        Result out; out.type = Result::Type::context;
        out.subject = {{"roots", std::to_string(live_roots)}, {"nodes", std::to_string(live_nodes)},
                       {"wrong_thread_release", std::to_string(wrong_thread_release)}};
        return out;
    }
    if (command == "gate")
    {
        // Exercise the actual admission/dispatch CAS used by timeout suppression.
        for (int i = 0; i < 100; ++i)
        {
            Work work; work.phase = 1; work.deadline = Clock::now() + std::chrono::seconds(1);
            std::atomic<int> dispatched{0};
            std::thread thread([&]
            {
                try { work.dispatch(); ++dispatched; } catch (const Failure &) {}
            });
            int expected = 1;
            bool canceled = work.phase.compare_exchange_strong(expected, 3);
            thread.join();
            if ((canceled && dispatched) || (!canceled && dispatched != 1)) { fail("backend_protocol_error"); }
        }
        return {};
    }
    auto &entry = get(session, Kind::session);
    auto target = entry.primary();
    if (!target->synthetic) { fail("identity_unverifiable"); }
    auto *semantic = entry.semantic ? dynamic_cast<SyntheticSemantic *>(entry.semantic->semantic.get()) : nullptr;
    auto *capture = entry.capture ? dynamic_cast<SyntheticCapture *>(entry.capture->capture.get()) : nullptr;
    if (command == "stale") { target->life->invalidate(); }
    else if (command == "move") { target->geometry.bounds.x += 10; target->life->changed(true); }
    else if (command == "minimize") { target->geometry.minimized = true; target->life->changed(true); }
    else if (command == "layout") { target->life->changed(); }
    else if (command == "modal" && semantic) { semantic->is_modal = true; }
    else if (command == "password" && semantic) { semantic->password = true; }
    else if (command == "readonly" && semantic) { semantic->readonly = true; }
    else if (command == "unknown_enabled" && semantic) { semantic->enabled.reset(); }
    else if (command == "autofocus" && semantic) { semantic->focus = false; }
    else if (command == "dead_element" && semantic) { semantic->nodes[1]->live = false; }
    else if (command == "root_reuse" && semantic) { semantic->nodes[0]->live = false; }
    else if (command == "wrong_hwnd") { target->hwnd = reinterpret_cast<HWND>(uintptr_t{0x1234567876543210}); }
    else if (command == "resize_during_frame" && capture) { capture->mode = 1; }
    else if (command == "closed_during_frame" && capture) { capture->mode = 2; }
    else if (command == "device_error" && capture) { capture->mode = 3; }
    else if (command == "capture_timeout" && capture) { capture->mode = 4; }
    else if (command == "unmapped_frame" && capture) { capture->mode = 5; }
    else if (command == "normal_frame" && capture) { capture->mode = 0; }
    else if ((command == "no_frame_deadline" || command == "hung_native" ||
              command == "hung_cleanup" || command == "held_frame") && capture)
    {
        std::lock_guard lock(capture_probe_mutex);
        if (live_captures) { fail("backend_protocol_error"); }
        capture_probe_released = false; capture_probe_expired = false;
        capture->mode = command == "no_frame_deadline" ? 6 : command == "hung_native" ? 7 :
                        command == "hung_cleanup" ? 8 : 9;
    }
    else if (command == "invokes" && semantic)
    {
        Result out; out.type = Result::Type::receipt; out.operation = std::to_string(semantic->invoked); return out;
    }
    else { fail("unsupported_background_operation"); }
    return {};
}
} // namespace scap::window
#endif
