# 窗口截图与后台语义控制：设计及实施分工

日期：2026-10-27。状态：**待实现的方案，不是功能或桌面验收报告**。

固定代码输入：`25750d1e82e0d52853f6eba7825de23408fd5e05`（`master`）。
方案交付分支：`refs/heads/codex/provider-window-plan-20261027`。
遵循仓库 [AGENTS.md](../AGENTS.md)；本次只新增本文，不实施库代码、操作桌面或调整权限。
后续调度应分别记录代码输入 SHA、本文交付的 plan SHA、各实现依赖 SHA，不以当时分支尖端替代。

## 1. 推荐结论与首版完成标准

新增显式窗口会话，分开管理**窗口采集身份、可访问性控件身份、动作能力**。
截图采用系统的单窗口采集；控制采用 AX/UI Automation 的控件语义操作。
现有整屏 API 保持原语义。新窗口 API 只提供 `background_only` 策略，没有静默退回全局输入、
激活窗口或“失败后抢前台再试”的路径。

必须区分两项承诺：

1. **只截取一个窗口**：帧来自指定采集对象，不是从整屏图像中裁剪矩形；另一个窗口的内容不会因此成为采集目标。
2. **操作不会干扰另一窗口**：库不移动真实鼠标、不注入全局键盘、不读写系统剪贴板、不设置焦点；
   还必须验证目标应用自身执行动作时不会激活窗口、抢焦点、改变剪贴板或修改同进程其他窗口。
   第一项成立不能证明第二项。串行队列、私有事件源、异步函数也不提供桌面隔离。

AX/UIA 是应用提供的能力，不是限制应用副作用的沙箱。对任意第三方应用的“绝不抢焦点”不能仅凭 API 名称保证。
因此首版把动作启用条件定为：**身份可确认 + 当前控件支持 + 精确应用/动作的后台验证配置匹配**。
未验证或不支持就明确报错；不设置允许绕过此条件的 `force`/`unsafe` 开关。

### 1.1 可在下一轮实施的完整 V1

| 必交付能力 | 范围与验收口径 |
| --- | --- |
| 枚举、绑定、关闭 | 两平台顶层窗口；显式选择不可变会话 token；关闭/重启/ID 复用使旧引用失效 |
| 单窗口截图 | BGRA/JPEG、真实图像尺寸、窗口局部坐标映射、帧与几何版本；每次截图拥有独立短时采集生命周期 |
| 控件读取 | 限定已绑定的 AX 窗口根或 UIA 窗口根；有界树快照、控件引用、名称/角色/支持的动作；密码值不返回 |
| 真实后台写操作 | 仅 `set_value`（替换整个可编辑值）与 `invoke`（无导航、无弹窗的已验证动作）；直接调用 AX/UIA，不模拟键鼠或粘贴 |
| 能力与失败 | 分别报告 API 支持、身份关联、后台验证状态；未验证写操作必须失败；调用超时不声称未执行 |
| Python 与 MCP | Python async API、类型和错误；MCP 新的 window 工具独立传 token，与原整屏工具并存 |
| 验证交付 | 离线测试 + 两平台专用无敏感内容原生窗口样例/手工验收脚本 + 明确的平台验收表 |

这是一版可用于**窗口观察、可访问性检查以及已验证自有应用表单/按钮自动化**的接口，
不是通用“任意窗口后台鼠标”。V1 必须有真实 AX/UIA 写操作实现，不能只有截图和全局点击包装。
同时，不得为了凑齐成功案例把模拟后端当作真实支持：专用测试应用证明原生调用链可工作，
只为该应用版本启用验证配置；第三方应用仍须逐项验证。

首批参考配置限定仓库自带的 AppKit / Win32 测试窗口：可写文本框、只更新本窗口计数的按钮。
实现提供同样的配置接口供自有应用接入；配置标识必须绑定应用身份、版本、控件及动作，不能是用户传一个布尔值。
若没有完成对应机器上的真实验收，该配置保持 `not_run`、生产写操作禁用；不能宣称该平台后台控制已完成验收。

### 1.2 不进入 V1 的能力

- 任意坐标 click/double-click/right-click、hover、drag、键码/快捷键、按下/释放、IME、滚轮注入。
  Canvas、游戏、远程桌面、自绘控件没有可用语义操作时返回不支持。
- 以 `CGEventPostToPid`、废弃的 `AXUIElementPostKeyboardEvent`、`PostMessage`/`SendMessage` 键鼠消息
  作为后台兜底；进程定向不能解决同一进程多窗口路由和焦点/修饰键问题。
- `SendInput`、`CGEventPost`、`CGWarpMouseCursorPosition`、剪贴板保存后再粘贴、激活/恢复最小化窗口。
  “用后恢复鼠标/剪贴板/焦点”仍然干扰用户，不能满足本需求。
- 窗口录屏、应用音频、麦克风、自动包含弹窗、跨窗口事务、远程会话/虚拟机管理。
  需要任意键鼠且宿主机必须可用的场景，另立隔离虚拟机方案，不能以另建线程或 desktop 替代隔离验收。

## 2. 固定输入的代码问题与复用边界

以下为本次实际阅读的源码，不沿用历史行号或历史功能判断：

| 文件/入口 | 当前事实 | 实施要求 |
| --- | --- | --- |
| `src/scapkit_computer_use/__init__.py` | 桌面导出惰性加载；`process.py` 可独立导入 | 新 window 模块继续惰性导出，不把 MCP/桌面扩展引入 subprocess 基础路径 |
| `screen_capture_kit/__init__.py`、`types.py`、`_scapkit.pyi` | `start_capture(display_id)`；`Point2D` 为全局输入坐标；读帧不携带窗口身份/几何版本 | 新增窗口类型与入口，不能把 window ID 塞进 display ID，也不重定义旧类型 |
| `native_code/osx/src/capture.m` | 选 `SCDisplay`，`initWithDisplay:excludingWindows:@[]`；仅缓存 complete 像素帧；显示真实光标 | 新增单窗口 filter；需要内容矩形/scale/帧状态/时钟/身份快照，不能复用旧读帧的 `None` 表达所有窗口错误 |
| `native_code/windows/src/capture.cpp`、`include/backend.h` | `Capture(uint32_t id)` → `CreateForMonitor`；WGC free-threaded frame pool；超出纹理尺寸时报 display size changed | 新增有类型的 WindowTarget/CreateForWindow 路径，保留原 monitor/recording 语义；窗口尺寸改变要显式处理 |
| `native_code/osx/src/control.c` | `CGWarpMouseCursorPosition`、HID/session tap `CGEventPost`；快捷键释放平衡并不提供后台隔离 | 不在此路径加窗口偏移伪装后台；AX 另建模块 |
| `native_code/windows/src/input.cpp` | `SendInput`、全局剪贴板、物理桌面坐标 | UIA 另建模块；窗口路径不能调用既有输入或剪贴板函数 |
| `src/scapkit_computer_use_mcp/device.py` | `click` 先全局移动；`type_text` 覆盖剪贴板再粘贴；设备 lock 串行化整屏操作 | 独立 window 会话注册表和入口；原设备 lock 不是用户输入锁 |
| `src/scapkit_computer_use_mcp/server.py`、`agent_guide.md` | 工具坐标为全局；错误包装主要为字符串 | 新工具明确局部坐标和结构化错误，指南同步，不能根据“当前绑定”隐式改写旧工具含义 |
| `setup.py`、两端 module init | setuptools，系统框架；free-threaded 扩展声明不启用 GIL | 注册新源码/方法并保留声明、条件编译和原生同步；不引入 CMake、PyObjC、pywinauto 等基础包依赖 |

复用编码器、像素复制、资源关闭模式，而非直接改变旧 capture handle 的外部行为。
现有测试 hooks、reader mutex、WGC weak/shared ownership 可作为实现参照。
`recording.py`、两平台录制器和 `process.py` 不在本次功能改动范围内。

## 3. 官方 API 与本机 SDK 核验

核验日期为本文日期。官方文档描述 API 合约；本机 SDK 编译只能证明声明与 availability，不能证明运行行为。

| 主题 | 核验结果与设计影响 | 官方来源 |
| --- | --- | --- |
| macOS 单窗口 | `initWithDesktopIndependentWindow:`；窗口遮挡/移出屏幕仍可采集，最小化暂停；不自动包含其他子窗口/弹窗。帧内容可能随配置缩放 | [Apple WWDC22 10155](https://developer.apple.com/videos/play/wwdc2022/10155/?time=276) |
| macOS AX | `AXUIElementPerformAction`、可写属性；`kAXErrorCannotComplete` 可能是应用迟迟未返回，不能推断动作未生效 | [AXUIElementPerformAction](https://developer.apple.com/documentation/applicationservices/1462091-axuielementperformaction) |
| Windows 单窗口 | `IGraphicsCaptureItemInterop::CreateForWindow(HWND, …)`；接口最低 Windows 10 1903，不改变本库 Win10 22H2/Win11 23H2+ 的目标政策 | [CreateForWindow](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow) |
| Windows 不捕获光标 | `IsCursorCaptureEnabled(false)` 从 Win10 2004 提供；关闭图像中的光标不等于输入隔离 | [IsCursorCaptureEnabled](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.graphicscapturesession.iscursorcaptureenabled?view=winrt-26100) |
| UIA 窗口根 | `ElementFromHandle` 取得该 HWND 的根；后续仅在根下遍历，禁止全桌面 ElementFromPoint 定位后台目标 | [ElementFromHandle](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-elementfromhandle) |
| UIA 自动焦点 | **默认的 Invoke/SetValue 等可能先设置焦点**；必须成功配置 `IUIAutomation2::put_AutoSetFocus(FALSE)` 并读回，再允许后台动作 | [AutoSetFocus](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation2-put_autosetfocus) |
| UIA 写入/副作用 | Value 要求 enabled 且非 readonly；Invoke 是否及时返回、是否有其他业务副作用取决于 provider；关闭 AutoSetFocus 不能约束应用内部行为 | [SetValue](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationvaluepattern-setvalue)、[Invoke](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationinvokepattern-invoke)、[Invoke provider 指南](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-implementinginvoke) |
| UIA 线程 | 不拥有 UI 窗口的专用 COM MTA 线程；事件注册/移除同线程，考虑跨位数 apartment 生命周期 | [UIA threading](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)、[IUIAutomation2 超时配置](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nn-uiautomationclient-iuiautomation2) |
| HWND 与几何 | `IsWindow` 不能阻止销毁/句柄复用；GetWindowRect 有 DPI 虚拟化和隐形边框，不可直接当截图像素边界 | [IsWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-iswindow)、[GetWindowRect](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowrect) |
| 全局输入 | SendInput 进入系统键鼠输入流，受 UIPI 和已有按键状态影响，不是独立后台通道 | [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) |

本机为 macOS 26.6.2，`xcrun --show-sdk-version` 返回 **27.0**。检查了该 SDK 下：

- `ScreenCaptureKit.framework/Headers/SCStream.h`、`SCShareableContent.h`：
  单窗口 filter 与帧 `contentRect/contentScale/scaleFactor` 自 12.3；filter 的
  `contentRect/pointPixelScale`、`ignoreShadowsSingleWindow` 自 14.0；`includeChildWindows` 自 14.2；
  `SCWindow.active` 自 13.1，不能在 13.0 无保护调用。
- `ApplicationServices.framework/Frameworks/HIServices.framework/Headers/AX*.h`：
  有窗口根/属性/动作与消息超时；搜索 `WindowNumber`、`_AXUIElementGetWindow`、`AXUIElementGetWindow`
  **未发现公开声明**。本次不能确认公开的通用 AXWindow ↔ CGWindowID 直接桥接。
  不把网上使用的私有符号或字符串属性视为已验证的公共接口。
- `CoreGraphics.framework/Headers/CGEvent.h` 有 `CGEventPostToPid`，这只证明存在进程定向 API，
  不证明窗口定向、不影响焦点或输入状态；V1 不采用。

本轮还用 `xcrun --sdk macosx clang -x objective-c -fobjc-arc -fblocks -fsyntax-only`
及 `-Werror -Werror=unguarded-availability -mmacosx-version-min=13.0` 编译了一个仅含上述公开声明的临时探针，
14.0/14.2 属性均有 `@available` 保护，退出码 0。**没有运行探针、链接产品、申请权限或截取桌面**。
Windows SDK/编译/运行本轮均为 `not_run`；Windows 结论限官方 API 与仓库源码。

## 4. 推荐 API、身份与能力数据

在 `scapkit_computer_use.window` 中新增 API，并从包顶层惰性导出。下列是待实现合约，非现有调用示例：

```python
async def list_windows(*, pid: int | None = None) -> list[WindowInfo]: ...
async def open_window(ref: WindowRef) -> WindowSession: ...
async def list_accessible_windows(pid: int) -> list[AccessibleWindowInfo]: ...
async def open_accessible_window(
    ref: AccessibleWindowRef, *, profile_id: str | None = None
) -> AccessibleWindowSession: ...

# WindowSession / AccessibleWindowSession 均支持 async with、幂等 aclose。
# WindowSession 的控制能力须成功建立下述关联，否则只有采集能力。
async def attach_accessibility(self, ref: AccessibleWindowRef, *, profile_id: str) -> None: ...
async def capabilities(self) -> WindowCapabilities: ...
async def screenshot(self, *, format: str = "jpeg", quality: int = 80,
                     timeout_seconds: float = 5.0) -> WindowFrame: ...
async def elements(self, *, max_depth: int = 8, max_nodes: int = 512) -> ElementSnapshot: ...
async def element_at(self, x: float, y: float, *, snapshot_id: str) -> ElementRef: ...
async def set_value(self, element: ElementRef, value: str) -> ActionReceipt: ...
async def invoke(self, element: ElementRef) -> ActionReceipt: ...
```

`screenshot` 仅属于 WindowSession；AccessibleWindowSession 可以独立读取/操作明确选择的语义窗口，
不会假装它已经与某张截图对应。`element_at` 是在绑定树内按局部矩形找控件，**不会发 click**；
遇到重叠候选、缺少坐标关联、过期快照均报错，不自动选最近控件。
API 的参数必须运行时检查类型、范围、有限数、深度/节点/字节上限；`bool` 不当作 ID、维度或坐标整数接受。

### 4.1 身份结构

- `WindowInfo`：`ref`、平台、诊断用 native ID、PID、进程实例标记、可空标题、窗口边界、
  坐标单位、可知的可见/最小化状态、独立能力字段。标题用于展示/搜索，**不是主键**。
- `WindowRef`：本进程/服务会话生成的不可构造 opaque token，绑定 native 对象、进程实例和 generation。
  不能直接用裸 `CGWindowID`/`HWND` 打开，不可跨进程/重启持久化；PID 也不唯一标识进程实例。
- 进程实例核验：macOS 可用 `proc_pidinfo(PROC_PIDTBSDINFO)` 的启动时间，Windows 用只读进程句柄与
  `GetProcessTimes`；读取失败须说明 `identity_unverifiable`，不能把未知启动时间视作匹配。
- `AccessibleWindowRef`：独立持有 AX 根/COM 根及 generation；`ElementRef` 附属根 token、树 epoch、
  原生元素引用。动作不能用重新按名称搜索替换旧元素，也不能跨 session 传用。
- token 是防误路由机制，不是 OS 访问控制或应用沙箱。枚举与操作不可能对外部进程构成原子事务。
  必须保留原生对象、检查前后身份并监听销毁；无法排除竞态时拒绝或报告不确定，不能宣称杜绝恶意 ABA 复用。

### 4.2 macOS 的采集/语义身份关联必须显式处理

`SCWindow.windowID` 可精确选采集窗口，但 `AXUIElementCreateApplication(pid)` 只选了进程。
从 `kAXWindowsAttribute` 得到的窗口根不应仅凭相同 PID、标题、位置、尺寸就获得写权限，
即使当前恰好只有一个候选；这些只能作为候选提示，不能证明同一窗口。

V1 采用以下确定的保守决策，不把桥接调查留给实现者猜测：

1. 普通 SCWindow 可以截图；普通 AX 窗口可以作为**另一个显式语义目标**被枚举/读取。
2. 要在同一个 WindowSession 上使用图片局部坐标定位控件或执行语义动作，必须有可验证的公共关联。
   首个自有 AppKit 样例可通过自有接口报告 `NSWindow.windowNumber`、窗口唯一标记、进程实例；
   同一唯一标记公开于 AXIdentifier，原生端复核，并持有原 AX 根。样例标记按创建实例生成，关闭即失效。
3. 没有这种应用适配器，`attach_accessibility` 返回 `identity_unverifiable`；歧义返回
   `ambiguous_window`。不调用 `_AXUIElementGetWindow`，不猜 `AXWindowNumber`，不以窗口激活来辅助配对。
4. 独立 `open_accessible_window` 不依赖 CGWindowID；其写操作仍受后台验证配置约束。
   文档与 MCP 必须标明它是 control-only，不能让客户端误认为与 capture token 自动等价。

因此通用 macOS 第三方窗口的“图像绑定后直接写控件”不是 V1 默认支持项。
这不会把 AX 写操作实现推迟掉：独立 AX 根与具备公开关联的自有应用均实现同一套语义操作。

### 4.3 Windows 的关联

先对 HWND/进程实例建立 WGC item，再从该 HWND 取得 UIA 根，检查 PID、NativeWindowHandle、根比较结果。
子控件可能没有独立 HWND，不能因此改用前台元素；通过 retained element、RuntimeId（仅会话内）、
根祖先关系确认归属。保留原根，绝不因旧元素失效而 `ElementFromHandle` 自动重绑新窗口。
`IsWindow + PID` 不足以证明未复用，销毁通知、WGC Closed、UIA element-not-available 任何一个都应废止旧 generation。
无法可靠读取/确认的 provider 保留读取能力或报 `identity_unverifiable`，不放宽写入条件。

### 4.4 能力不能只有一个 background=true

每个动作返回 `api_support = supported | unsupported | unknown`、
`identity_status = verified | unverified | stale`、
`background_validation = verified | not_run | failed`、`reason_code`、`profile_id` 和验证版本范围。
顶层分开给 `capture`、`inspect_elements`、`set_value`、`invoke`、`coordinate_input`。
V1 的 `coordinate_input` 永远 unsupported；单窗口截图成功不提升控制能力。

后台配置只允许已验证的应用构建/控件种类/动作及前提；版本或窗口状态改变重新评估。
AXPress/UIA Invoke 能触发打开文档、导航、弹窗等行为，不列入通用白名单。
`set_value` 不是逐键输入、追加、粘贴，也不声称触发与真实键盘相同的 IME/键盘监听事件。
只读/disabled/密码控件拒绝写入；无 settable AXValue 或 ValuePattern 时不回退剪贴板。

动作返回 `ActionReceipt(operation_id, dispatch_state, effect_state)`：
`dispatch_state` 为 not_dispatched/accepted/unknown，`effect_state` 为 verified/unknown。
只有有针对性的状态回读才能标记 effect verified；API 返回成功本身不代表业务任务完成。
验证回读不把实际用户文本或剪贴板写进日志。

## 5. 局部坐标、截图与时效

新 API 单独定义 `WindowPoint`，左上为原点。macOS 局部单位为 points，Windows 为物理像素。
局部基准是所绑定的窗口表面，不默认是客户区：标题栏/边框是否包含由元数据明确表达。
图像维度始终是编码后真实 pixels，Windows 的 `ui_scale_factor` 不能参与输入坐标换算。

`WindowFrame` 必须原子返回：

- `window_token/generation`、`frame_id`、`geometry_revision`、单调时钟 `captured_at` 与帧状态；
- `image_width/image_height`、BGRA stride 或 JPEG bytes；
- `window_bounds`（全局诊断边界）、`local_rect`、`image_content_rect_px`、可空 `client_rect_local`；
- `coordinate_unit`、`image_to_local` 仿射变换和 `mapping_status`。

原始 SCK `contentRect` 是 SDK 定义的 surface 中 points，不能直接当 pixels。
原生层结合同帧 contentScale/scaleFactor 和真实 pixel-buffer 尺寸归一化，再校验边界/缩放。
不能用枚举时的屏幕 scale 或显示器大小代替；macOS 13 无 filter 新属性时使用同帧元数据。
无法可靠建立映射仍可返回图像，但 `mapping_status=unavailable`，坐标定位报 `mapping_unavailable`。

归一化后统一换算，裁剪/留白/阴影偏移均包含在矩形中：

```text
local_x = local_rect.x + (image_x - image_content_rect_px.x)
          * local_rect.width / image_content_rect_px.width
local_y 同理；只允许 content rect 内的有限坐标，右/下边界不包含。
```

例如 800×600 points 内容在图片中占 1600×1200 pixels，内容左上图片偏移为 (20, 10)，
图片点 (220, 210) 对应局部 (100, 100) points；不能直接加窗口全局原点当作后台点击。
Windows 对应尺寸若为 1600×1200 物理像素，则该点为局部 (200, 200)，不会再除以 200% UI 缩放。
客户端自行缩图时先还原原始图片坐标。

V1 每次 screenshot 新建短时 stream/pool，等待该次启动后的有效完整帧，复制后在 finally 关闭。
没有“读上次缓存当本次截图”的默认路径。采集开始/交付前检查身份和几何版本；移动/缩放跨越该区间
则返回 `geometry_changed`，由调用者显式重新截图，不无限内部重试。
静态窗口的首次有效帧可以成功；旧流 idle 状态不是新帧，suspended/blank 不可被上一张图掩盖。
`elements` 也返回树 epoch；布局变更使旧的局部定位失效。动作至少重新检查元素活性、归属、enabled 和能力，
不能仅拿历史图片坐标定位全局桌面。

## 6. 窗口生命周期与失败语义（补充验收条件）

此节对应 `steer_17c744c4ad454d989f3421cf9753443d`。

| 状态/事件 | 必须校验 | 截图行为 | 控制行为 |
| --- | --- | --- | --- |
| 窗口关闭 | retained 对象有效性、Closed/AX 销毁通知、进程实例 | `window_closed`；清缓存、废止 generation | 排队且未发出的动作拒绝；已发出但结果不明为 `outcome_unknown` |
| ID 被复用（含同 PID） | 原对象 + generation + 进程实例；不能只查 ID/PID | `stale_window`；不会把同号新窗图片交给旧会话 | 原 ElementRef 永久失效；重新枚举并由调用者明确绑定新窗 |
| 进程退出/重启 | 启动标记，不用标题或重用 PID 补绑 | `stale_window` | 同上；profile 也重新匹配版本 |
| 目标窗口移动 | geometry_revision，捕获前后位置 | 本次跨越变化则 `geometry_changed`；下一次返回新原点 | 旧 snapshot 定位报 `stale_geometry`；稳定控件引用仍需重新验证 |
| 缩放/调整大小/跨 DPI 屏幕 | 尺寸、scale、content rect、树布局 | 新 pool/config；竞态报 `geometry_changed`，不拉伸旧帧冒充 | 旧局部映射失效，不按旧比例发送输入 |
| 部分/完全遮挡 | 仍使用目标原生采集对象；不换成桌面 hit-test | macOS 按官方单窗口合约；Windows 行为须实测，失败报 `capture_unavailable` | 仅遍历绑定根；后台能力不由“看得见”推断 |
| 最小化 | AXMinimized / IsIconic 等可知状态；未知不得假装 false | V1 统一 `window_minimized`；状态未知且无新帧则 `capture_timeout`，不恢复窗口 | V1 拒绝最小化窗口写操作；不发 restore/activate；恢复后重新截图/枚举控件 |
| 同一进程多窗口 | 每窗独立根、原生对象、generation；重名/同尺寸覆盖测试 | 仅所选窗口；不会按 PID 包含所有窗口 | 不用应用“当前窗口”；无法关联返回 `identity_unverifiable`/`ambiguous_window` |
| 弹窗、菜单、sheet、modal | 窗口归属、独立窗口根、modal/disabled 状态及 profile 前提 | 不自动改 capture filter，也不保证弹窗在原窗图内 | 默认 `modal_blocked` 或 `unsupported_background_operation`；不把动作改发弹窗/主窗口 |
| 权限撤销/会话切换/受保护内容 | 平台错误与会话状态 | `permission_denied`/`capture_unavailable`；全黑但有效帧不擅自判断成功内容或伪造具体权限原因 | 不提权、不设置权限；能力降为不可用 |

弹窗若确实要操作，必须重新枚举成独立目标并显式绑定，再做自己的能力验证。
对无法可靠识别 modal 状态的 provider，不把“没有检测到”当作满足 profile；严格写入拒绝。
监视失效/事件队列溢出时废止相关引用。没有公开 API 能把其他进程的窗口寿命锁成事务，
检查之后发生的销毁可能只在返回时发现，必须保留 `outcome_unknown`，不能承诺回滚。

### 6.1 稳定错误模型

新增 `WindowError` 及必要的 `WindowPermissionError`、`WindowTimeoutError` 子类，统一携带：
`code`、`operation`、`window_token`、可空 `native_domain/native_code`、`dispatch_state`、`retry_hint`。
不复用一个字符串 `OSError` 淹没未验证/权限/关闭的区别；native 返回结构化失败后由 Python 映射，
避免在原生锁内构造异常。保留原有 API 的错误行为。

| code | 语义/后续动作 |
| --- | --- |
| `unsupported_background_operation` | 原始键鼠/剪贴板/无语义动作；绝不执行替代路径 |
| `background_not_verified` | API 存在但没有该应用/动作/版本的无干扰验收；先验证适配器 |
| `identity_unverifiable` / `ambiguous_window` | 不能确认采集/控制关联或出现多个候选；重新显式选目标，不能自动挑第一个 |
| `stale_window` / `window_closed` / `stale_element` | 引用失效；废止旧 token，不能自动重绑 |
| `stale_geometry` / `geometry_changed` / `mapping_unavailable` | 旧图片或单位映射不可靠；读取新快照，不执行写动作 |
| `window_minimized` / `modal_blocked` | 当前窗口状态不在 V1 支持范围；不自行恢复或激活 |
| `permission_denied` / `capture_unavailable` / `element_not_editable` | 已识别的前提不足；原生错误保留但去除敏感内容 |
| `capture_timeout` | 本次无可用新帧；没有图像结果，不意味着窗口关闭 |
| `outcome_unknown` | 写动作发出后超时、取消或通信失败；可能已生效，**禁止自动重试** |
| `interference_detected` | 验收或可归因的运行观测发现副作用；禁用配置/会话，不通过恢复焦点伪装无干扰 |
| `backend_busy` / `session_closed` | 有限队列满、前一操作仍在途或会话关闭；不能另起无限线程绕过串行化 |

排队期间取消且尚未 dispatch，记录 not_dispatched；开始执行后的取消保留取消异常和 operation_id，
会话可查询最近动作状态。没有确认结果时记录 unknown，不包装成安全取消或失败后自动再次 invoke。
读取可以由调用者按 retry_hint 重试；写操作只在确认 not_dispatched 后才可能安全重试。

## 7. 两平台原生实现选择

### 7.1 macOS

- 新增 `window.m` 管理 SC/AX 窗口身份、状态与能力；`accessibility.m` 管理 AX 根、树与动作。
  `window_capture.m` 构建独立 filter 和帧元数据；与 `capture.m` 共享只涉及像素/编码/安全关闭的内部 helper。
- 枚举用 SCShareableContent，显式排除桌面/系统非目标项；允许获取不在屏幕上的候选，
  不把 `isOnScreen=false` 直接解释成最小化。ScreenCapture/Accessibility 权限分别预检，不弹授权请求。
- 只用 `initWithDesktopIndependentWindow:`。`showsCursor=NO`、`capturesAudio=NO`；
  新系统 `includeChildWindows=NO`，阴影属性按 availability 设置；13.0 路径依赖帧元数据归一化。
  不使用要求 14+ 的 SCScreenshotManager 作为唯一实现，也不提高源码最低版本以省掉分支。
- AX 从显式窗口根读取子树；有界深度/节点/字符串长度、检测环。禁止从 system-wide
  `AXUIElementCopyElementAtPosition` 获取被遮挡位置上的前台窗口控件。
- 写入前查询 `AXUIElementIsAttributeSettable(kAXValueAttribute)` 或 action names；
  调用 `AXUIElementSetAttributeValue`/`AXUIElementPerformAction`，不设 AXFocused、AXMain、不执行 AXRaise。
  `AXUIElementSetMessagingTimeout` 有限超时；`CannotComplete` 映射依据 dispatch 阶段保留 unknown。
- 持有 AX root 与元素引用直到关闭/操作结束；销毁与变化通知放入受控队列，仅用于失效与观察，
  不触发自动重绑或执行下一动作。需要 CFRunLoop 的 AXObserver 由专用线程拥有。

### 7.2 Windows

- 新增 `window.cpp`（枚举/身份/几何）与 `accessibility.cpp`（UIA actor）。
  EnumWindows + PID/进程启动时间，读取 IsIconic、窗口 owner/root；不枚举/控制其他安全桌面。
- `capture.cpp` 增加 tagged target：MonitorTarget 或 WindowTarget，不以 HWND 截断为 uint32。
  WindowTarget 调用 CreateForWindow，持有 item 并监听 Closed。复用 D3D11/WIC，
  每次截图关闭 FrameArrived/Closed 订阅与 pool；收到尺寸变化丢弃本次不匹配帧并报 geometry_changed。
- 只捕获指定 HWND，不用 BitBlt 整屏裁剪或 PrintWindow 作为无提示替代。最小化、保护内容、
  不支持 WGC、设备丢失均明确错误；不自动 SetForegroundWindow、ShowWindow(SW_RESTORE)。
- 几何读取在现有 DpiScope 内执行，保留调用线程原 DPI context；结合 DWM extended frame bounds、
  client 映射和实际 WGC ContentSize 验证偏移。必要时链接 `dwmapi`；不能让 GetWindowRect 的隐形边框错位。
- 专用 MTA actor 创建支持 IUIAutomation2 的客户端（如 CUIAutomation8），成功设置并回读
  `AutoSetFocus(FALSE)`，配置有限 ConnectionTimeout/TransactionTimeout。不允许不支持此设置时换老对象继续写入。
  事件注册/移除、root/element/pattern 的使用与释放留在 actor；不把 COM 指针交给任意 Python executor 线程。
- ElementFromHandle → 根内查找 → 检查归属/模式 → ValuePattern::SetValue 或 InvokePattern::Invoke。
  只支持 TextPattern 的控件不能自动转换为 ValuePattern；没有能力就报错。
  不调用 SetFocus、SendInput、PostMessage 键鼠、全局剪贴板、AttachThreadInput；不自动提权/UIAccess。

Windows 部分为实施设计。**本机没有 Windows 原生运行证据**，不能把历史整屏测试转换成窗口后台验收。

## 8. 资源、取消、线程与兼容性

### 8.1 共同生命周期

会话状态：`OPEN → CLOSING → CLOSED`；失效另有 `STALE`，不允许恢复为 OPEN。
动作状态：`QUEUED → DISPATCHED → COMPLETED/UNKNOWN`。每会话串行，跨窗口也不宣称同进程应用业务隔离。
native registry/共享状态必须有 mutex/队列；Python asyncio.Lock 只管本 loop，不能代替无 GIL 同步。
会话和 stream 绑定创建它们的事件循环，跨 loop 调用拒绝。

截图启动/停止是不可简单取消的系统异步调用：参照现有 MCP screenshot 与 recording 的收尾方式，
shield 在途启动，取消后仍接收并关闭迟到 handle；重复取消不提前释放底层状态。
所有等待离开 asyncio 主线程；等待 native queue/mutex/IPC/编码时释放 Python thread state。
close 幂等且所有等待者共享结果；不会清除仍被读者使用的像素或元素。

macOS queue 拥有 stream、最新 frame/元数据、delegate back-pointer；在 queue 上 retain 完整快照后才离开读取。
保留 reader mutex，锁内不做 Python 分配；ARC malloc/calloc 结构强字段在 free 前置 nil。
超时 completion 拥有独立状态，不捕获将释放的裸指针；entry/callback 有 autoreleasepool。
Windows callback 使用 weak/shared ownership；先撤销订阅，再关闭 session/pool，再释放 GPU/COM 资源；
不要持有 GPU 锁等待会反向获取该锁的 callback。

AX/UIA 调用不是可撤销事务。有限 API 超时也不保证任意坏 provider 及时返回。
V1 每个原生语义 actor 使用有界队列（例如 64）且至多一个在途写操作；超时后隔离该 actor，
后续写入返回 backend_busy/outcome_unknown，不能无限新建线程。保留纯原生在途状态，
迟到返回后在拥有线程释放；本地 aclose 可使会话不可再用，但若 provider 不返回，明确报告清理未完成。
不能强杀线程、提前 free、承诺即时回收；退出/模块卸载路径不得留下回调访问 Python 对象。
后续若要求硬期限进程隔离，另立受控 helper 进程设计，不在 V1 暗中引入。

### 8.2 兼容性要求

- CPython 3.10+；避免 3.11+ 专属语法/API（例如未作兼容的 TaskGroup、asyncio.timeout、StrEnum）。
  每个 PyArg_ParseTuple 与 fallible allocation 检查；失败返回 NULL 前设置异常。
- 3.13t/3.14t 继续原生同步，`Py_GIL_DISABLED` 守卫和不启用 GIL 声明不变。
  新代码要在导入与并发测试前后断言 GIL 未被开启；不以“序列化 Python 层”代替此检查。
- Native method table docstring 保持 NULL；接口说明写 `.pyi` 与 Python wrapper。
- macOS 源码最低 13.0；wheels 仍为 15+ arm64；本地新 SDK 编译不代表 13.0 或 Intel 已运行。
  Windows 仍为 x64/ARM64，指针宽度/ABI 不变，源码目标政策不因 CreateForWindow 的最低版本而扩大。
- 原 `start_capture(display_id)`、current_frame 返回 None、整屏坐标、输入释放、录制与 shell 环境行为保持兼容。
  新的窗口异常/帧类型不能悄悄改变旧 API。
- MCP 依赖只在 extra。保留 macOS 3.13t 基础库检查、普通 Python 与 3.14t MCP 约束；
  Windows MCP 仍限有上游 wheel 的普通 x64 Python，保留 3.13t/3.14t 与 ARM64 基础库检查，
  不构建 OpenSSL、不弱化 GIL 断言。

## 9. MCP 合约

新增 `window_list`、`window_open`、`window_capabilities`、`window_screenshot`、
`window_elements`、`window_element_at`、`window_set_value`、`window_invoke`、`window_close`。
macOS 的独立语义目标通过 `accessible_window_list/open` 与 `window_attach_accessibility` 显式表达。
每个调用都传 session token；不设“当前窗口”全局变量，不给旧 `click/type_text` 添加隐式窗口模式。

窗口 token 归属创建它的 MCP 会话；stdio/HTTP 生命周期结束时清理。
若所用 SDK 的无状态模式不能提供客户端会话身份，V1 窗口会话工具必须明确限制为有状态 transport，
不能用服务全局“最后一个客户端”替代；无状态调用返回不支持。会话注册表有数量上限和闲置过期。
维持单设备单 worker、Host/Origin 校验、非 loopback bearer 保护；不得把 token、标题、控件值写入普通日志。

新工具错误使用 `CallToolResult(isError=True)` 的稳定 JSON（code、operation_id、dispatch_state、retry_hint），
不混淆为 HTTP 500 或笼统“点击失败”。新截图 ImageContent 同时附 WindowFrame 元数据，
不可复用整屏 `display.origin` 的换算说明。新字段用 `input_units_per_pixel` 或完整仿射变换；
旧 `points_per_pixel_x/y` 仅保持原工具兼容。
打包 `agent_guide.md`、server 描述、README、docs/mcp-server.md 必须一致，提示失败后重新查询能力，
禁止推荐切到全局工具绕过 background_only。

## 10. 按依赖划分的实施任务

上层 external Codex 负责调度；本规划 Agent **不启动其他 Agent、不执行下表实现**。
建议先冻结 A 的合约，再并行 B/C，最后 D 集成与独立 R 验收。每位实现者使用自己的 Provider 工作区与
上层指定输出分支；消费精确依赖 SHA，不能修改其他 Agent 工作区或默认分支。

| 任务/建议负责人 | 依赖 | 文件与具体交付 | 完成条件 |
| --- | --- | --- | --- |
| A：公共合约与测试骨架 Agent | 本文固定 plan SHA、选定代码 input SHA | 新增 `src/scapkit_computer_use/window_types.py`、`window_errors.py`、`window.py`、`window_profiles.py`；修改包 `__init__.py`、`screen_capture_kit/_scapkit.pyi`；新增 `tests/test_window_api.py`、`test_window_identity.py`、`test_window_coordinates.py` | 明确 token/epoch、profile schema、错误、异步关闭和 native DTO；fake 后端验证所有拒绝分支；不连接桌面 |
| B：macOS 原生 Agent | A 的冻结 DTO/入口 | 新增 `native_code/osx/include/window.h`、`src/window.m`、`window_capture.m`、`accessibility.m`；修改 `capture.m` 内部 helper 和 `ext.c` 注册；新增 `tests/native_window_helpers.h`、`test_native_window_macos.py` | SC 单窗/元数据/取消、AX 独立根与显式关联、真实 set_value/invoke 路径、公共 API availability、合成并发测试 |
| C：Windows 原生 Agent | A 的冻结 DTO/入口 | 新增 `native_code/windows/src/window.cpp`、`accessibility.cpp`；修改 `capture.cpp`、`include/backend.h`、`module.cpp`；新增 `tests/test_native_window_windows.py` | WGC WindowTarget、窗口身份与 DPI、MTA actor、AutoSetFocus=false、UIA 模式与错误、离线 fake COM/像素测试；真实结果另列 |
| D：集成与用户入口 Agent | A+B+C 的选定提交 | 统一修改 `setup.py` 注册源码/库；MCP `device.py/server.py/agent_guide.md`；README、`docs/development.md`、`docs/windows.md`、`docs/mcp-server.md`；新增 `tests/test_mcp_windows.py`、`tests/manual/verify_window_background.py`、`tests/manual/window_fixture_macos.m`、`window_fixture_windows.cpp`、`tests/manual/window-background.md` | 构建、fake MCP、参考应用与可重复验证流程；profiles 未实测不启用；指南无全局兜底建议；更新已有 CI 的非发布检查，不能触发 release |
| R：独立 review/acceptance Agent | D 的精确候选 commit SHA | 隔离 inspection worktree、审查记录及对应测试证据；通过上层指定分支交付 | 审核无全局副作用路径、身份/取消/锁、API 文档；区分 passed/failed/not_run；不在自身原会话 reset 冒充候选 SHA |

`setup.py`、公共 `.pyi`、包导出只由 A/D 负责合入，B/C 提交所需注册清单，降低交叉编辑冲突。
B/C 不各自重新定义错误/元数据结构。A 不能因为缺少原生实现而把 fake 后端暴露为产品支持。
任一平台未达到真实验收门槛，D/R 应交付“实现已编译/合成通过、桌面 not_run”的准确状态，不替上层做发布决定。

## 11. 测试与验收矩阵

### 11.1 自动化：不接触桌面

在 SCAPKIT_TESTING 下编译离线接收器、假窗口/AX provider/COM provider 与合成帧，
不是 monkeypatch 后仅验证 mock 调用次数。测试必须覆盖真正的 native 身份状态机、映射、所有权与拒绝分支。

| 测试组 | 核心断言 |
| --- | --- |
| 身份与归属 | 窗口关闭、同 PID 同 ID 被复用、进程启动标记变化、重复标题/同尺寸、元素来自兄弟窗口、弹窗根/owner 变化；旧 token 不重绑 |
| 禁止副作用 | 新窗口路径的全局鼠标/键盘/clipboard/focus/restore 接收器设为触碰即失败；包括拒绝、超时、取消和 cleanup；UIA AutoSetFocus 设置失败即拒绝 |
| 坐标 | 1x/2x/非整数缩放、负全局原点、边框/留白、缩图、跨 DPI、无效 metadata、旧 revision、NaN/Inf/越界；Windows 不乘 ui_scale_factor |
| 帧状态 | 首帧延迟、complete/idle/suspended/blank、最小化后旧缓存、移动/resize 前后不一致、Closed、设备错误、JPEG 尺寸与 BGRA stride/乘法溢出 |
| 动作与错误 | 不可写值/密码/缺 pattern、profile 未验证、对象失效、modal、AX CannotComplete、UIA 超时/拒绝；unknown 不重试，返回码不等同效果确认 |
| 资源与取消 | 启动/复制/等待/stop 各阶段取消，重复 cancel/close，迟到回调、allocation fail、GC 重入、并发 read/close、释放计数归零 |
| MCP | 跨 session token 拒绝、超额/过期 cleanup、control-only 标记、错误结构、工具描述、图像元数据；fake devices，不操作真实桌面 |
| 兼容 | 旧 capture/input/recording 的受影响合成回归；惰性导入和不装 MCP 的基础包；生产 wheel 无 `_test_*` |

后续实现后才运行（下列文件部分尚未创建，本规划没有运行这些命令）：

```sh
uv sync --frozen
SCAPKIT_TESTING=1 uv run setup.py build_ext --inplace --force
uv run pytest tests/test_window_api.py tests/test_window_identity.py tests/test_window_coordinates.py
uv run pytest tests/test_native_window_macos.py tests/test_native_capture.py tests/test_capture_lifecycle.py tests/test_native_arguments.py tests/test_async_safety.py
uv run --extra mcp pytest tests/test_mcp_windows.py tests/test_mcp_server.py tests/test_mcp_recording.py
```

Windows 在对应机器用 PowerShell 设置 `$env:SCAPKIT_TESTING='1'`，相同 setuptools 构建，执行
`uv run pytest tests/test_native_window_windows.py tests/test_windows_backend.py tests/test_async_safety.py`。
MCP 只在符合依赖矩阵的解释器上运行。不得执行整个 pytest suite，避免混入真实桌面测试。

macOS 原生修改必须对 deployment target 13.0 和 15.0 做 `-Werror=unguarded-availability` 语法检查、
compiler warnings 与静态分析；Windows `/W4` 和 `SCAPKIT_ANALYZE=1`，分析告警分类记录。
至少在 Python 3.10 和 free-threaded 3.14t 强制重建并测试，同时保留 CI 3.13t 基础矩阵。
无 GIL 检查应围绕导入和 pytest 执行，在同一解释器内检查 `sysconfig.get_config_var('Py_GIL_DISABLED')`
与 `sys._is_gil_enabled()`，不能仅用另一进程的导入成功代替并发运行检查。

### 11.2 后续手工验证：无敏感内容窗口、不抢用户输入

**本次不执行。** 上层在已准备好专用测试环境、明确桌面验证范围后安排。
样例应用是原生 AppKit / Win32 程序，提供无副作用文本框、计数按钮、两个同进程窗口 A1/A2、
独立进程用户窗口 B、可手动打开的 modal，以及只写入这些测试窗口事件的日志。
不得用全桌面键盘钩子记录用户输入，不采集真实应用或读取真实剪贴板内容。

建议可重复步骤（至少包含以下一轮）：

1. 用户自行启动样例、选 A1 为目标，A2 设为不同计数/文本。B 为前台，文本仅为预设测试字符串。
   用户自行将剪贴板设置为公开哨兵 `SCAPKIT-WINDOW-CLIPBOARD-TEST`；先确认没有敏感内容。
   harness 仅在这次显式测试中读取哨兵和变化序号，产品后台路径不读写剪贴板。
2. 用户保持 B 焦点，持续用真实鼠标在 B 的测试画布画线，并输入约定字符串/按住 Shift 再释放。
   harness 只截取 A1，执行 A1 文本框 set_value 与计数按钮 invoke 各 20 次；A1 被 B 部分/完全遮挡各一轮。
   用专用验收模式测试候选 profile，不因调用本身成功就把 profile 标记 verified。
3. 核对 A1 文本/计数确实改变、A2 完全未变；B 收到的测试字符/修饰键序列完整、画线无程序跳点；
   两端样例记录的 active/key/focus 事件证明 B 始终是用户窗口，A1/A2 未变成前台。
   哨兵内容与剪贴板变化序号均不变，不允许“改后恢复”通过。产品路径的禁止调用计数必须为零。
4. 先独立验证“只截取一个窗口”：A2/B 使用明显不同的测试图案，A1 返回图像不包含它们；
   再独立验证上一步的输入/焦点/剪贴板证据。图像正确不能代替无干扰结果。
5. 用户手动移动/缩放 A1、移到不同 DPI 屏幕、最小化/恢复；验证旧 snapshot 被拒绝，
   最小化显式报错且不会被恢复。没有相应显示器时该项 `not_run`，不是跳过后算通过。
6. 关闭 A1 并创建同标题新窗，旧 session/ElementRef 必须失败；真实 ID 未复用只算关闭/重建通过，
   ID 复用使用离线状态机注入，不能谎称真实系统已复用。再测试 A2 同进程重名与 modal 出现后拒绝写入。
7. 在截图启动和动作在途阶段取消；确认 B 不受影响、迟到资源收尾、已发出动作结果 unknown 不自动重试。
   用户随后在 B 手工继续输入，确认没有遗留的合成按键/按钮；不通过“清空全部按键状态”修复。

仅观察前后 `GetForegroundWindow`/frontmost application 相同不足以排除中途抢焦点再恢复，
必须结合测试窗口连续事件日志、原生禁止调用计数和用户观察；仍只证明该应用/版本/场景。
应用若自己导航/弹窗/改剪贴板，即使库没有注入事件也判失败，撤销相应 profile。
不把未验证的第三方应用动作作为这次测试的默认目标。

### 11.3 本规划阶段的真实状态与后续门槛

| 项目 | 本轮状态 | 后续记录要求 |
| --- | --- | --- |
| 固定输入、源码/AGENTS 阅读、官方文档、本机 SDK 声明 | 已核验；仅静态证据 | 留存固定 SHA 与来源日期 |
| macOS 13.0 target 公开 API 探针 | 仅 syntax-only 通过 | 不写成 macOS 13 运行通过 |
| 新窗口单元测试/实现编译 | `not_run`（本轮只写方案） | 后续记录命令、退出码、真实输出 |
| macOS 单窗截图、AX 写入、无干扰 | `not_run`（本轮禁止桌面输入/截图） | 对候选精确 SHA 在准备好的测试窗口验收 |
| Windows 编译、UIA AutoSetFocus、WGC 遮挡/最小化/DPI、无干扰 | **`not_run`（本机为 macOS）** | Windows 实机/runner；不得用 macOS mock 代替 |
| Windows 10 / ARM64 桌面窗口行为 | **`not_run`** | 对应 OS/架构实机；历史 x64 整屏验收不覆盖 |
| macOS 13 / Intel 窗口运行 | `not_run` | 源码兼容目标与 wheel 政策分开写 |
| 发布/release/tag/默认分支合并 | 未执行，也不在本任务授权范围 | 由上层另行决定 |

独立 R 必须新建隔离 inspection worktree 检查指定候选 SHA，不 reset 自身会话；
测试源码、扩展 ABI、SCAPKIT_TESTING 状态和报告都对应同一 SHA。
该工作区内的构建可以改变 ignored 产物，但不得污染原始 checkout 或其他 Agent 工作区。

## 12. 交接与拒绝发布条件

上层实施顺序：A 固定合约 → B/C 平台实现 → D 集成与受控样例 → R 独立检查 →
对应机器上获准的桌面验证。本文提交不授权默认分支合并、release/tag 或发布工作流。

以下任一情况都应阻止宣称“后台窗口控制已验收”：

- 仍有全局输入、剪贴板、激活/恢复窗口的兜底；或只把坐标加偏移后调用现有 click。
- 只凭 PID/标题把截图窗口与 AX 根配对，或失效后静默重绑。
- Windows 未显式关闭 AutoSetFocus，或将 UIA/AX 有此方法写成后台已验证。
- 超时/取消未知结果自动重试；资源清理不确定却报告已完全关闭。
- 只有 fake/合成通过而没有明确 desktop `not_run`，或把历史整屏 Windows 证据当成本功能证据。
- 未推送交付分支、未以实际远端 SHA 确认 publication，或把本地文件/模型完成当成交付。

推荐分工总结：**公共 API/身份合约一组，macOS 原生一组，Windows 原生一组，随后由集成与独立验收接力**。
平台能力缺口是公开接口中的明确状态；实现者无需也不得为了统一外观虚构跨平台后台鼠标能力。
