#pragma once
#include "backend.h"
#include "window_fixture_identity.h"
#include <array>
#include <deque>
#include <map>
#include <optional>
#include <set>

namespace scap::window
{
using Clock = std::chrono::steady_clock;
constexpr size_t max_bytes = 128 * 1024 * 1024;
constexpr size_t max_entries = 8192;
struct Failure
{
    const char *code;
    const char *domain = "window";
    int64_t native = 0;
    const char *dispatch = "not_dispatched";
    const char *retry = "never";
};
[[noreturn]] inline void fail(const char *code) { throw Failure{code}; }
struct Rect
{
    double x = 0, y = 0, width = 0, height = 0;
    bool operator==(const Rect &) const = default;
    bool valid() const;
};
struct Geometry
{
    Rect bounds;
    std::optional<Rect> client;
    UINT dpi = 0;
    bool minimized = false;
    bool visible = false;
    bool operator==(const Geometry &) const = default;
};
// Only atomics are shared with WinEvent/UIA/WGC callbacks. Invalidation is sticky.
struct Life
{
    std::atomic<bool> stale{false}, cleanup_failed{false};
    std::atomic<uint64_t> epoch{1};
    std::atomic<uint64_t> layout{1};
    void invalidate() noexcept { stale.store(true); epoch.fetch_add(1); }
    void changed(bool geometry = false) noexcept
    {
        epoch.fetch_add(1);
        if (geometry) { layout.fetch_add(1); }
    }
};
enum class Kind { capture_ref, accessible_ref, session, element };
struct Token
{
    const uint64_t id;
    const Kind kind;
    const DWORD creator = GetCurrentProcessId();
    const Clock::time_point issued = Clock::now();
    std::atomic<bool> consumed{false}, closed{false};
    std::atomic<bool> cleanup_failed{false};
    Token(uint64_t id_, Kind kind_) : id(id_), kind(kind_) {}
    void consume();
};
using Ref = std::shared_ptr<Token>;
using Subject = std::map<std::string, std::string>;
struct Rule { std::string identifier, role, action; };
struct Grant { Subject subject; std::string profile; std::vector<Rule> rules; };
struct Node
{
    virtual ~Node() = default; // Always destroyed on its owning actor.
};
struct NodeData
{
    std::string identifier, raw_identifier, role, name;
    std::optional<Rect> bounds;
    std::optional<bool> enabled, editable, password;
    bool value = false, invoke = false;
    bool operator==(const NodeData &) const = default;
};
struct TreeRow
{
    std::shared_ptr<Node> node;
    NodeData data;
    int parent = -1, depth = 0;
};
struct Semantic
{
    virtual ~Semantic() = default;
    virtual void validate() = 0;
    virtual std::vector<TreeRow> tree(int depth, int nodes, const Geometry &) = 0;
    virtual NodeData validate_node(const std::shared_ptr<Node> &, const Geometry &) = 0;
    virtual std::optional<bool> modal() = 0;
    virtual bool no_focus() = 0; // Must include live IUIAutomation2 readback.
    virtual Subject subject() = 0;
    virtual bool same_root(Semantic &) = 0;
    virtual bool act(const std::shared_ptr<Node> &, const std::string &, const std::wstring &) = 0;
};
struct CaptureTarget
{
    virtual ~CaptureTarget() = default;
    virtual Pixels capture(const Geometry &, double, double &) = 0;
};
struct Target
{
    HWND hwnd = nullptr; // Never narrow a HWND to DWORD/int.
    DWORD pid = 0;
    uint64_t generation = 0;
    std::string process_instance, title;
    std::shared_ptr<Life> life = std::make_shared<Life>();
    std::shared_ptr<Handle> process;
    std::unique_ptr<CaptureTarget> capture;
    std::unique_ptr<Semantic> semantic;
    Geometry geometry;
    uint64_t revision = 1, last_layout = 1;
    bool synthetic = false;
    void validate();
    Geometry refresh();
};
std::unique_ptr<Semantic> make_semantic(const std::shared_ptr<Target> &);
std::vector<std::shared_ptr<Target>> enumerate(bool accessible, DWORD pid, size_t limit);
std::string utf8(const std::wstring &, size_t limit = 4096);
std::wstring wide(const std::string &);
std::string executable_hash(HANDLE process, std::wstring &path);
bool fixture_image(const std::wstring &path);
std::string environment_id();
std::vector<uint8_t> encode_window_jpeg(const Pixels &, int);
Geometry read_geometry(HWND hwnd);

struct Row
{
    Ref ref;
    NodeData data;
    int parent = -1, depth = 0;
};
struct Info
{
    Ref ref;
    uint64_t generation = 0;
    DWORD pid = 0;
    std::string process_instance, native_id, title;
    Geometry geometry;
    bool capture = false, inspect = false;
};
struct Result
{
    enum class Type { none, infos, opened, context, snapshot, frame, receipt } type = Type::none;
    std::optional<Failure> error;
    std::vector<Info> infos;
    Ref handle;
    uint64_t generation = 0, revision = 0, epoch = 0;
    std::string process_instance;
    bool capture = false, inspect = false, no_focus = false;
    std::optional<bool> minimized, modal;
    Subject subject;
    std::vector<Row> rows;
    Pixels pixels;
    std::vector<uint8_t> encoded;
    Geometry geometry;
    bool mapping = false;
    double captured_at = 0;
    std::string frame_id, format, operation;
    bool effect = false;
};
struct Entry
{
    std::weak_ptr<Token> token;
    std::shared_ptr<Target> capture, semantic;
    Ref owner; // Elements retain their originating session, never another session.
    std::shared_ptr<Node> node;
    NodeData data;
    uint64_t epoch = 0, revision = 0;
    Clock::time_point snapshot_time{};
    std::vector<uint64_t> snapshot;
    bool uncertain = false;
    std::shared_ptr<Target> primary() const { return capture ? capture : semantic; }
};
struct Work
{
    std::mutex mutex;
    std::condition_variable changed;
    bool done = false;
    // 0 queued, 1 running, 2 dispatch committed, 3 canceled before dispatch.
    std::atomic<int> phase{0};
    std::atomic<bool> abandoned{false};
    Clock::time_point deadline;
    Result result;
    void checkpoint();
    void dispatch();
};
class Registry
{
    std::map<uint64_t, Entry> entries;
    uint64_t next = 1;
    std::set<std::string> revoked;
  public:
    Ref insert(Kind, Entry);
    Entry &get(const Ref &, Kind);
    void reap();
    Result list(bool accessible, DWORD pid, size_t limit);
    Result open(const Ref &, bool accessible);
    Result close(const Ref &);
    Result attach(const Ref &, const Ref &);
    Result inspect(const Ref &, const std::string &, int, int, uint64_t, uint64_t);
    Result screenshot(const Ref &, const std::string &, int, double);
    Result action(const Ref &, const Ref &, uint64_t, uint64_t, const std::string &,
                  const std::wstring &, const Grant &, const std::string &, Work &);
    void validate_snapshot(Entry &, uint64_t, uint64_t);
    void identity(Result &, Entry &);
#ifdef SCAPKIT_TESTING
    Result synthetic(const std::string &, const Ref &);
#endif
};
// One lazily started process-wide MTA actor, one in-flight operation, <=64 waiters.
// It is never replaced after a timeout. COM objects and late results stay native.
class Actor
{
    std::mutex mutex;
    std::condition_variable changed;
    using Job = std::pair<std::shared_ptr<Work>, std::function<Result(Registry &, Work &)>>;
    std::deque<Job> jobs;
    bool quarantine = false;
    bool initialized = false;
    std::optional<Failure> startup_error;
    void run() noexcept;
  public:
    Actor();
    Result call(std::function<Result(Registry &, Work &)>, double timeout = 5, bool cleanup = false,
                bool write = false);
};
Actor &actor();
PyObject *result_object(const Result &);
#ifdef SCAPKIT_TESTING
void test_bstr_value_pattern();
PyObject *py_test_window(PyObject *, PyObject *);
#endif
} // namespace scap::window
