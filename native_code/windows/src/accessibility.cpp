#include "window_backend.h"
#include <UIAutomation.h>
#include <algorithm>

namespace scap::window
{
namespace
{
struct Variant
{
    VARIANT value{};
    ~Variant() { VariantClear(&value); }
};
std::string text(BSTR value, size_t limit = 4096)
{
    if (!value) { return {}; }
    auto size = SysStringLen(value);
    if (size > limit) { fail("resource_limit"); }
    return utf8(std::wstring(value, size), limit);
}
void uia_check(HRESULT hr, const std::shared_ptr<Life> &life)
{
    if (SUCCEEDED(hr)) { return; }
    if (hr == UIA_E_ELEMENTNOTAVAILABLE)
    {
        life->invalidate(); throw Failure{"stale_window", "UIA", hr};
    }
    throw Failure{hr == E_ACCESSDENIED ? "permission_denied" : "identity_unverifiable", "UIA", hr};
}
class OwnedBstr
{
    BSTR value = nullptr;
  public:
    OwnedBstr() = default;
    explicit OwnedBstr(BSTR allocated) : value(allocated)
    {
        if (!value) { throw std::bad_alloc(); }
    }
    ~OwnedBstr() { SysFreeString(value); }
    OwnedBstr(const OwnedBstr &) = delete;
    OwnedBstr &operator=(const OwnedBstr &) = delete;
    BSTR get() const { return value; }
    BSTR *put()
    {
        SysFreeString(value);
        value = nullptr;
        return &value;
    }
};
bool replace_value(IUIAutomationValuePattern &pattern, const std::wstring &replacement,
                   const std::shared_ptr<Life> &life)
{
    // Python's UTF-8 limit implies at most 65536 UTF-16 code units. Keep the
    // bound here as well, before narrowing the allocation length to UINT.
    if (replacement.size() > 65536) { fail("resource_limit"); }
    OwnedBstr input(SysAllocStringLen(replacement.data(), static_cast<UINT>(replacement.size())));
    // [in] BSTR is borrowed by SetValue; this MTA scope retains ownership even
    // if the provider fails. A wchar_t buffer is not a length-prefixed BSTR.
    uia_check(pattern.SetValue(input.get()), life);
    OwnedBstr readback;
    if (FAILED(pattern.get_CurrentValue(readback.put())) || !readback.get() || SysStringLen(readback.get()) > 65536)
    { return false; }
    return std::wstring(readback.get(), SysStringLen(readback.get())) == replacement;
}
std::vector<int> runtime_id(IUIAutomationElement *element, const std::shared_ptr<Life> &life)
{
    struct Array { SAFEARRAY *value = nullptr; ~Array() { if (value) { SafeArrayDestroy(value); } } } array;
    uia_check(element->GetRuntimeId(&array.value), life);
    if (!array.value || SafeArrayGetDim(array.value) != 1) { fail("identity_unverifiable"); }
    VARTYPE type = VT_EMPTY;
    LONG lower = 0, upper = -1;
    check(SafeArrayGetVartype(array.value, &type));
    check(SafeArrayGetLBound(array.value, 1, &lower)); check(SafeArrayGetUBound(array.value, 1, &upper));
    if (type != VT_I4 || upper < lower || int64_t(upper) - lower >= 128) { fail("identity_unverifiable"); }
    std::vector<int> result;
    for (LONG index = lower;; ++index)
    {
        int value = 0;
        check(SafeArrayGetElement(array.value, &index, &value));
        result.push_back(value);
        if (index == upper) { break; }
    }
    return result;
}
struct UiaNode final : Node
{
    ComPtr<IUIAutomationElement> element;
    std::vector<int> runtime;
    UIA_HWND hwnd = nullptr;
    std::string raw_identifier;
};
// UIA may invoke this COM object on another MTA thread. It only touches native atomics.
class Events final : public IUIAutomationStructureChangedEventHandler,
                     public IUIAutomationPropertyChangedEventHandler
{
    std::atomic<ULONG> references{1};
    std::shared_ptr<Life> life;
  public:
    explicit Events(std::shared_ptr<Life> value) : life(std::move(value)) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **value) override
    {
        if (!value) { return E_POINTER; }
        *value = nullptr;
        if (id == __uuidof(IUnknown) || id == __uuidof(IUIAutomationStructureChangedEventHandler))
        { *value = static_cast<IUIAutomationStructureChangedEventHandler *>(this); }
        else if (id == __uuidof(IUIAutomationPropertyChangedEventHandler))
        { *value = static_cast<IUIAutomationPropertyChangedEventHandler *>(this); }
        else { return E_NOINTERFACE; }
        AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references; }
    ULONG STDMETHODCALLTYPE Release() override
    {
        ULONG count = --references;
        if (!count) { delete this; }
        return count;
    }
    HRESULT STDMETHODCALLTYPE HandleStructureChangedEvent(IUIAutomationElement *, StructureChangeType, SAFEARRAY *) override
    { life->changed(); return S_OK; }
    HRESULT STDMETHODCALLTYPE HandlePropertyChangedEvent(IUIAutomationElement *, PROPERTYID property, VARIANT) override
    { life->changed(property == UIA_BoundingRectanglePropertyId); return S_OK; }
};
class UiaRoot final : public Semantic
{
    std::weak_ptr<Target> target;
    std::shared_ptr<Life> life;
    ComPtr<IUIAutomation2> automation;
    ComPtr<IUIAutomationTreeWalker> walker;
    std::shared_ptr<UiaNode> root;
    ComPtr<Events> events;
    bool structure_registered = false, properties_registered = false;
    bool fixture = false;
    std::string prefix;
    Subject identity;
    std::shared_ptr<Target> owner()
    {
        auto value = target.lock();
        if (!value || life->stale) { fail("stale_window"); }
        return value;
    }
    std::string property_text(IUIAutomationElement *element, PROPERTYID property)
    {
        Variant result;
        uia_check(element->GetCurrentPropertyValueEx(property, TRUE, &result.value), life);
        if (result.value.vt == VT_EMPTY || result.value.vt == VT_UNKNOWN) { return {}; }
        if (result.value.vt != VT_BSTR) { fail("identity_unverifiable"); }
        return text(result.value.bstrVal);
    }
    std::optional<bool> property_bool(IUIAutomationElement *element, PROPERTYID property)
    {
        Variant result;
        uia_check(element->GetCurrentPropertyValueEx(property, TRUE, &result.value), life);
        if (result.value.vt != VT_BOOL) { return {}; }
        return result.value.boolVal != VARIANT_FALSE;
    }
    std::shared_ptr<UiaNode> retain(ComPtr<IUIAutomationElement> element)
    {
        auto node = std::make_shared<UiaNode>();
        node->element = std::move(element);
        node->runtime = runtime_id(node->element.Get(), life);
        uia_check(node->element->get_CurrentNativeWindowHandle(&node->hwnd), life);
        node->raw_identifier = property_text(node->element.Get(), UIA_AutomationIdPropertyId);
        return node;
    }
    bool compare(IUIAutomationElement *a, IUIAutomationElement *b)
    {
        BOOL equal = FALSE;
        uia_check(automation->CompareElements(a, b, &equal), life);
        return equal != FALSE;
    }
    void ancestry(const UiaNode &node)
    {
        auto state = owner();
        int pid = 0;
        uia_check(node.element->get_CurrentProcessId(&pid), life);
        UIA_HWND hwnd = nullptr;
        uia_check(node.element->get_CurrentNativeWindowHandle(&hwnd), life);
        if (pid <= 0 || static_cast<DWORD>(pid) != state->pid || hwnd != node.hwnd ||
            runtime_id(node.element.Get(), life) != node.runtime ||
            property_text(node.element.Get(), UIA_AutomationIdPropertyId) != node.raw_identifier)
        { fail("stale_element"); }
        if (hwnd && (!IsWindow(static_cast<HWND>(hwnd)) || GetAncestor(static_cast<HWND>(hwnd), GA_ROOT) != state->hwnd))
        { fail("stale_element"); }
        ComPtr<IUIAutomationElement> current = node.element;
        std::set<std::vector<int>> visited;
        for (int depth = 0; depth <= 64 && current; ++depth)
        {
            if (compare(current.Get(), root->element.Get())) { return; }
            if (!visited.insert(runtime_id(current.Get(), life)).second) { fail("stale_element"); }
            ComPtr<IUIAutomationElement> parent;
            uia_check(walker->GetParentElement(current.Get(), &parent), life);
            current = std::move(parent);
        }
        fail("stale_element");
    }
    NodeData describe(const UiaNode &node, const Geometry &geometry)
    {
        NodeData data;
        data.raw_identifier = node.raw_identifier;
        data.identifier = data.raw_identifier;
        int role = 0;
        uia_check(node.element->get_CurrentControlType(&role), life);
        data.role = role == UIA_EditControlTypeId ? "UIA.Edit" :
                    role == UIA_ButtonControlTypeId ? "UIA.Button" :
                    role == UIA_WindowControlTypeId ? "UIA.Window" : "UIA." + std::to_string(role);
        data.name = property_text(node.element.Get(), UIA_NamePropertyId);
        data.enabled = property_bool(node.element.Get(), UIA_IsEnabledPropertyId);
        data.password = property_bool(node.element.Get(), UIA_IsPasswordPropertyId);
        RECT bounds{};
        uia_check(node.element->get_CurrentBoundingRectangle(&bounds), life);
        Rect local{double(bounds.left) - geometry.bounds.x, double(bounds.top) - geometry.bounds.y,
                   double(bounds.right) - bounds.left, double(bounds.bottom) - bounds.top};
        if (local.valid()) { data.bounds = local; }
        ComPtr<IUIAutomationValuePattern> value;
        HRESULT hr = node.element->GetCurrentPatternAs(UIA_ValuePatternId, IID_PPV_ARGS(&value));
        if (SUCCEEDED(hr) && value)
        {
            data.value = true;
            BOOL readonly = TRUE;
            if (SUCCEEDED(value->get_CurrentIsReadOnly(&readonly))) { data.editable = readonly == FALSE; }
        }
        else if (hr == UIA_E_ELEMENTNOTAVAILABLE) { uia_check(hr, life); }
        ComPtr<IUIAutomationInvokePattern> invoke;
        hr = node.element->GetCurrentPatternAs(UIA_InvokePatternId, IID_PPV_ARGS(&invoke));
        data.invoke = SUCCEEDED(hr) && invoke;
        if (hr == UIA_E_ELEMENTNOTAVAILABLE) { uia_check(hr, life); }
        // Only this source-identified adapter may expose stable semantic identifiers.
        // Keep the full raw identifier and RuntimeId in the retained node for every live check.
        if (fixture)
        {
            auto state = owner();
            if (&node == root.get()) { data.identifier = "fixture.window"; }
            else if (data.raw_identifier == prefix + "text" && role == UIA_EditControlTypeId &&
                     node.hwnd == GetDlgItem(state->hwnd, 100)) { data.identifier = "fixture.text"; }
            else if (data.raw_identifier == prefix + "increment" && role == UIA_ButtonControlTypeId &&
                     node.hwnd == GetDlgItem(state->hwnd, 101)) { data.identifier = "fixture.increment"; }
        }
        return data;
    }
    void initialize_fixture()
    {
        auto state = owner();
        wchar_t name[256]{};
        if (!GetClassNameW(state->hwnd, name, 256) || std::wstring(name) != L"ScapkitWindowFixtureV1") { return; }
        const auto &raw = root->raw_identifier;
        // Exact grammar: scapkit.fixture.(A1|A2|B).<lowercase UUID>.window
        const std::string base = "scapkit.fixture.";
        if (!raw.starts_with(base) || !raw.ends_with(".window")) { return; }
        auto dot = raw.find('.', base.size());
        if (dot == std::string::npos) { return; }
        auto slot = raw.substr(base.size(), dot - base.size());
        if (slot != "A1" && slot != "A2" && slot != "B") { return; }
        auto uuid = raw.substr(dot + 1, raw.size() - dot - 1 - 7);
        if (uuid.size() != 36) { return; }
        for (size_t i = 0; i < uuid.size(); ++i)
        {
            if (i == 8 || i == 13 || i == 18 || i == 23)
            { if (uuid[i] != '-') { return; } }
            else if (!((uuid[i] >= '0' && uuid[i] <= '9') || (uuid[i] >= 'a' && uuid[i] <= 'f'))) { return; }
        }
        std::wstring path;
        auto build = executable_hash(state->process->value, path);
        if (!fixture_image(path)) { return; }
        identity = {{"platform", "windows"}, {"application_id", "scapkit.fixture.windows.v1"},
            {"application_version", "1"}, {"application_build", "sha256:" + build},
            {"environment", environment_id()}, {"backend_build", SCAPKIT_WINDOW_BACKEND_SHA256},
            {"window_identifier", "fixture.window"}};
        prefix = raw.substr(0, raw.size() - 6);
        fixture = true;
    }
  public:
    explicit UiaRoot(const std::shared_ptr<Target> &state) : target(state), life(state->life)
    {
        uia_check(CoCreateInstance(CLSID_CUIAutomation8, nullptr, CLSCTX_INPROC_SERVER,
                                    IID_PPV_ARGS(&automation)), life);
        uia_check(automation->put_ConnectionTimeout(1000), life);
        uia_check(automation->put_TransactionTimeout(1000), life);
        uia_check(automation->put_AutoSetFocus(FALSE), life);
        if (!no_focus()) { fail("unsupported_background_operation"); }
        uia_check(automation->get_RawViewWalker(&walker), life);
        ComPtr<IUIAutomationElement> initial;
        // The only ElementFromHandle call: creation. Never replace a stale retained root.
        uia_check(automation->ElementFromHandle(state->hwnd, &initial), life);
        if (!initial) { fail("identity_unverifiable"); }
        root = retain(std::move(initial));
        validate();
        initialize_fixture();
        events.Attach(new Events(life));
        uia_check(automation->AddStructureChangedEventHandler(root->element.Get(), TreeScope_Subtree,
                                                             nullptr, events.Get()), life);
        structure_registered = true;
        PROPERTYID properties[] = {UIA_BoundingRectanglePropertyId, UIA_AutomationIdPropertyId,
            UIA_IsEnabledPropertyId, UIA_IsOffscreenPropertyId, UIA_IsPasswordPropertyId,
            UIA_NamePropertyId, UIA_ControlTypePropertyId};
        HRESULT hr = automation->AddPropertyChangedEventHandlerNativeArray(root->element.Get(), TreeScope_Subtree,
            nullptr, events.Get(), properties, static_cast<int>(std::size(properties)));
        if (FAILED(hr))
        {
            automation->RemoveStructureChangedEventHandler(root->element.Get(), events.Get());
            structure_registered = false;
            uia_check(hr, life);
        }
        properties_registered = true;
    }
    ~UiaRoot() override
    {
        if (properties_registered)
        {
            if (FAILED(automation->RemovePropertyChangedEventHandler(root->element.Get(), events.Get())))
            { life->cleanup_failed = true; life->invalidate(); }
        }
        if (structure_registered)
        {
            if (FAILED(automation->RemoveStructureChangedEventHandler(root->element.Get(), events.Get())))
            { life->cleanup_failed = true; life->invalidate(); }
        }
        // All COM pointers released here on the MTA actor; callbacks retain only Life.
    }
    void validate() override
    {
        auto state = owner();
        int pid = 0;
        UIA_HWND hwnd = nullptr;
        uia_check(root->element->get_CurrentProcessId(&pid), life);
        uia_check(root->element->get_CurrentNativeWindowHandle(&hwnd), life);
        if (static_cast<DWORD>(pid) != state->pid || hwnd != state->hwnd ||
            runtime_id(root->element.Get(), life) != root->runtime ||
            property_text(root->element.Get(), UIA_AutomationIdPropertyId) != root->raw_identifier)
        { life->invalidate(); fail("stale_window"); }
    }
    bool no_focus() override
    {
        BOOL enabled = TRUE;
        return SUCCEEDED(automation->get_AutoSetFocus(&enabled)) && enabled == FALSE;
    }
    Subject subject() override { validate(); return identity; }
    bool same_root(Semantic &other) override
    {
        auto *candidate = dynamic_cast<UiaRoot *>(&other);
        return candidate && compare(root->element.Get(), candidate->root->element.Get()) &&
               root->runtime == candidate->root->runtime;
    }
    std::optional<bool> modal() override
    {
        auto state = owner();
        if (!IsWindowEnabled(state->hwnd)) { return true; }
        struct Owned { HWND root; bool found = false; } owned{state->hwnd};
        EnumThreadWindows(GetWindowThreadProcessId(state->hwnd, nullptr), [](HWND hwnd, LPARAM param) -> BOOL
        {
            auto &s = *reinterpret_cast<Owned *>(param);
            if (GetWindow(hwnd, GW_OWNER) == s.root && IsWindowVisible(hwnd)) { s.found = true; return FALSE; }
            return TRUE;
        }, reinterpret_cast<LPARAM>(&owned));
        if (owned.found) { return true; }
        if (fixture) { return false; }
        ComPtr<IUIAutomationWindowPattern> pattern;
        if (FAILED(root->element->GetCurrentPatternAs(UIA_WindowPatternId, IID_PPV_ARGS(&pattern))) || !pattern)
        { return {}; }
        BOOL value = TRUE;
        WindowInteractionState interaction{};
        if (FAILED(pattern->get_CurrentIsModal(&value)) || FAILED(pattern->get_CurrentWindowInteractionState(&interaction)))
        { return {}; }
        return value || interaction == WindowInteractionState_BlockedByModalWindow;
    }
    NodeData validate_node(const std::shared_ptr<Node> &value, const Geometry &geometry) override
    {
        auto node = std::dynamic_pointer_cast<UiaNode>(value);
        if (!node) { fail("stale_element"); }
        ancestry(*node);
        return describe(*node, geometry);
    }
    std::vector<TreeRow> tree(int depth, int nodes, const Geometry &geometry) override
    {
        std::vector<TreeRow> rows;
        std::set<std::vector<int>> visited;
        size_t text_bytes = 0;
        std::function<void(std::shared_ptr<UiaNode>, int, int)> walk;
        walk = [&](std::shared_ptr<UiaNode> node, int parent, int level)
        {
            if (!visited.insert(node->runtime).second) { fail("identity_unverifiable"); }
            ancestry(*node);
            auto data = describe(*node, geometry);
            text_bytes += data.identifier.size() + data.role.size() + data.name.size();
            if (text_bytes > 1024 * 1024) { fail("resource_limit"); }
            int index = static_cast<int>(rows.size());
            rows.push_back({node, std::move(data), parent, level});
            if (level == depth || rows.size() == static_cast<size_t>(nodes)) { return; }
            ComPtr<IUIAutomationElement> child;
            uia_check(walker->GetFirstChildElement(node->element.Get(), &child), life);
            while (child && rows.size() < static_cast<size_t>(nodes))
            {
                walk(retain(child), index, level + 1);
                if (rows.size() == static_cast<size_t>(nodes)) { break; }
                ComPtr<IUIAutomationElement> next;
                uia_check(walker->GetNextSiblingElement(child.Get(), &next), life);
                child = std::move(next);
            }
        };
        walk(root, -1, 0);
        return rows;
    }
    bool act(const std::shared_ptr<Node> &value, const std::string &action, const std::wstring &replacement) override
    {
        auto node = std::dynamic_pointer_cast<UiaNode>(value);
        if (!node) { fail("stale_element"); }
        if (action == "set_value")
        {
            ComPtr<IUIAutomationValuePattern> pattern;
            uia_check(node->element->GetCurrentPatternAs(UIA_ValuePatternId, IID_PPV_ARGS(&pattern)), life);
            if (!pattern) { fail("element_not_editable"); }
            return replace_value(*pattern.Get(), replacement, life);
        }
        ComPtr<IUIAutomationInvokePattern> pattern;
        uia_check(node->element->GetCurrentPatternAs(UIA_InvokePatternId, IID_PPV_ARGS(&pattern)), life);
        if (!pattern) { fail("unsupported_background_operation"); }
        uia_check(pattern->Invoke(), life);
        return false; // Successful Invoke is accepted, not a verified business effect.
    }
};
} // namespace
std::unique_ptr<Semantic> make_semantic(const std::shared_ptr<Target> &target)
{
    return std::make_unique<UiaRoot>(target);
}
#ifdef SCAPKIT_TESTING
void test_bstr_value_pattern()
{
    // No HWND, UIA client, proxy, or desktop provider is created. The local COM
    // implementation consumes the same real OLE strings as the production path.
    class ValuePattern final : public IUIAutomationValuePattern
    {
      public:
        std::wstring stored;
        HRESULT write_result = S_OK, read_result = S_OK;
        HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id, void **value) override
        {
            if (!value) { return E_POINTER; }
            *value = nullptr;
            if (id != __uuidof(IUnknown) && id != __uuidof(IUIAutomationValuePattern)) { return E_NOINTERFACE; }
            *value = static_cast<IUIAutomationValuePattern *>(this);
            AddRef(); return S_OK;
        }
        // Stack-owned local test receiver; never marshaled or handed to UIA.
        ULONG STDMETHODCALLTYPE AddRef() override { return 1; }
        ULONG STDMETHODCALLTYPE Release() override { return 1; }
        HRESULT STDMETHODCALLTYPE SetValue(BSTR value) override
        {
            if (!value) { return E_INVALIDARG; }
            stored.assign(value, SysStringLen(value));
            return write_result;
        }
        HRESULT STDMETHODCALLTYPE get_CurrentValue(BSTR *value) override
        {
            if (!value) { return E_POINTER; }
            *value = SysAllocStringLen(stored.data(), static_cast<UINT>(stored.size()));
            if (!*value) { return E_OUTOFMEMORY; }
            // Exercise cleanup even when a provider fills an out parameter and fails.
            return read_result;
        }
        HRESULT STDMETHODCALLTYPE get_CurrentIsReadOnly(BOOL *value) override
        { if (!value) { return E_POINTER; } *value = FALSE; return S_OK; }
        HRESULT STDMETHODCALLTYPE get_CachedValue(BSTR *value) override { return get_CurrentValue(value); }
        HRESULT STDMETHODCALLTYPE get_CachedIsReadOnly(BOOL *value) override { return get_CurrentIsReadOnly(value); }
    } pattern;
    auto life = std::make_shared<Life>();
    for (const auto &value : {std::wstring(), std::wstring(L"\u7a97\u53e3 \U0001f9ea"), std::wstring(65536, L'x')})
    {
        if (!replace_value(pattern, value, life) || pattern.stored != value) { fail("backend_protocol_error"); }
    }
    pattern.read_result = E_FAIL;
    if (replace_value(pattern, L"failed readback", life)) { fail("backend_protocol_error"); }
    pattern.write_result = E_ACCESSDENIED;
    bool rejected = false;
    try { replace_value(pattern, L"failed write", life); }
    catch (const Failure &error)
    { rejected = std::string(error.code) == "permission_denied" && error.native == E_ACCESSDENIED; }
    if (!rejected) { fail("backend_protocol_error"); }
    bool allocation_rejected = false;
    try { OwnedBstr allocation_failure(nullptr); }
    catch (const std::bad_alloc &) { allocation_rejected = true; }
    if (!allocation_rejected) { fail("backend_protocol_error"); }
}
#endif
} // namespace scap::window
