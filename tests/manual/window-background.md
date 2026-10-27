# 专用原生窗口样例：构建、协议与后续后台验收

本交付只提供 AppKit / Win32 测试应用，不实现产品窗口 API。方案输入为
`18304c2b16f562c50e0434b7d41d53fc6ac944a3` 的
[window-control-plan-20261027.md](../../docs/window-control-plan-20261027.md)。
`window-fixture-v1` 的 profile 仍为 **not_run**，不得因本次编译/离线检查而启用生产写操作。

同一个 `--role a` 进程创建 A1（蓝色）和 A2（橙色）；另启 `--role b` 创建独立绿色 B。
窗口标题、可见标签、颜色不同。每个窗口有真实可写文本框、只增加本窗口计数的按钮和鼠标画布。
文本框不是密码框，只输入约定的公开测试字符串。应用不会读取其他应用、剪贴板、屏幕或音频，
不会安装键盘/鼠标全局 hook，也不会自动操作外部窗口。日志只包含本应用自己收到的事件。
若人在样例框内输入个人信息，该信息会成为样例的文本状态；所以必须使用无敏感测试环境。
文本编辑器禁用复制/剪切/粘贴（macOS 也禁用 pasteboard 拖放和自动文本替换），避免人工快捷键
误读真实剪贴板或修改哨兵；用手工键入或真实 AX/UIA 值写入设置公开测试文本。

状态接口是**只输出、不接受指令**的私有目录里的 JSON 文件。不存在 HTTP、socket、控制文件、
`--set-text` 等测试后门。控制端必须走真实 AX / UIA；离线 probe 不能代替控件动作证据。

## 构建与离线验证（不创建窗口）

从仓库根目录执行；产物全部在 ignored 的 `build/window-fixtures/`，不提交二进制。

macOS，Xcode Command Line Tools / SDK：

```sh
sh tests/manual/build_window_fixture_macos.sh
uv run --no-project --python 3.10 python -I tests/manual/test_window_fixtures.py --binary build/window-fixtures/window-fixture-macos
uv run --no-project --python 3.14t python -I tests/manual/test_window_fixtures.py --binary build/window-fixtures/window-fixture-macos
xcrun --sdk macosx clang --analyze -fobjc-arc -fblocks -Wall -Wextra -Werror -Werror=unguarded-availability -mmacosx-version-min=13.0 tests/manual/window_fixture_macos.m -o build/window-fixtures/analysis.plist
xcrun --sdk macosx clang -fsyntax-only -fobjc-arc -fblocks -Wall -Wextra -Werror -Werror=unguarded-availability -mmacosx-version-min=15.0 tests/manual/window_fixture_macos.m
```

脚本使用 clang、ARC、AppKit、`-mmacosx-version-min=13.0` 和严格告警，不固定 CPU 架构，
不使用 CMake/PyObjC。当前构建机是 arm64；源码 target 不代表 macOS 13 或 Intel 已运行验收。
脚本将源文件 SHA-256 编译进程序。手工直接编译而不设置该宏会报告 `unversioned`，不能绑定验证配置。

Windows，在对应架构的 Visual Studio Developer PowerShell 中，安装 MSVC C++ 工具和 Windows SDK：

```powershell
powershell -NoProfile -File tests/manual/build_window_fixture_windows.ps1
$env:SCAPKIT_ANALYZE = '1'
powershell -NoProfile -File tests/manual/build_window_fixture_windows.ps1
Remove-Item Env:SCAPKIT_ANALYZE
uv run --no-project --python 3.10 python -I tests/manual/test_window_fixtures.py --binary build/window-fixtures/window-fixture-windows.exe
uv run --no-project --python 3.14t python -I tests/manual/test_window_fixtures.py --binary build/window-fixtures/window-fixture-windows.exe
```

只链接 Windows 系统库，使用 C++20、`/W4`，`SCAPKIT_ANALYZE=1` 加 `/analyze`。
用 x64 Developer Shell 构建 x64，用 ARM64 target Developer Shell 构建 ARM64；在相应机器运行。
句柄用 `HWND` / `uintptr_t` / `LONG_PTR`，JSON 用字符串保存完整位宽。
本机没有 Windows SDK/MSVC，以上 Windows 命令**未执行**。

`test_window_fixtures.py` 启动实际编译的程序并强制 `--offline-probe`：不初始化 NSApplication，
不创建 HWND，不调用 AX/UIA 或申请 OS 权限。检查实际 JSON 编码、build hash、进程/窗口 token、
模拟 native ID 复用、A2 状态独立、事件溢出、原子替换后的完整文件、目录/文件权限和错误参数。
macOS 子进程使用干净环境和一次性登录 shell profile；Windows 直接启动原生二进制，
从 `CreateEnvironmentBlock(..., FALSE)` 取得 user/system 环境，不继承 Python 环境，不经过 cmd/AutoRun。
`python -I` 避免宿主的 PYTHONHOME/PYTHONPATH 污染解释器。
Windows 的 symlink 测试若因系统权限不可用会明确 skip，不能写成已通过。
这些测试没有测试真实 UIA provider、AX、实际焦点或截图。

## 获准集成时的启动与关闭命令（本轮未运行）

先构建。状态目录参数必须为绝对路径、末级目录必须不存在、父目录必须存在且由测试者控制。
不要重用目录，不要放到共享目录、同步盘或源码中。目录存在、符号链接/reparse point 或创建失败都会拒绝。

macOS，在一个专用终端中：

```sh
fixture_run=$(mktemp -d /tmp/scapkit-window-fixture.XXXXXX)
build/window-fixtures/window-fixture-macos --role a --background --state-dir "$fixture_run/a" &
fixture_a_pid=$!
build/window-fixtures/window-fixture-macos --role b --state-dir "$fixture_run/b" &
fixture_b_pid=$!
```

若要测试“B 当前前台时 A 后台创建”，先运行 B 命令，手工点击 B，再由已准备的控制终端/调度器
启动 A；启动顺序不是自动聚焦操作。A/B 的启动都先在 prohibited activation policy 下完成
`finishLaunching`，随后切到 accessory，窗口通过 `orderBack:` 展示；无 Dock 图标/菜单栏。
这是为了避开 [finishLaunching 的默认激活行为](https://developer.apple.com/documentation/appkit/nsapplication/finishlaunching())。
代码不调用 activate、makeKeyAndOrderFront 或设置焦点。`--background` 明确标记此次后台启动；
B 也使用相同的安全启动路径，由人点击。该选项不禁止以后的人为点击和激活。
终端本身的窗口切换不属于样例的无干扰测量区间。

正常结束可点窗口内 **Quit this fixture process**，或关闭最后一个窗口，也可在上述终端中执行：

```sh
kill -TERM "$fixture_a_pid" "$fixture_b_pid"
wait "$fixture_a_pid"
wait "$fixture_b_pid"
```

SIGTERM/SIGINT 仅设置停止标记，主线程定时器正常退出并写 `running=false`。
不要对未知 PID 或已经退出后可能复用的 PID 执行 kill。

Windows，在已打开的专用 PowerShell 中（`-NoNewWindow` 避免新控制台）：

```powershell
$fixtureRun = Join-Path ([IO.Path]::GetTempPath()) ('scapkit-window-fixture-' + [guid]::NewGuid())
New-Item -ItemType Directory $fixtureRun | Out-Null
$fixtureExe = (Resolve-Path 'build/window-fixtures/window-fixture-windows.exe').Path
$fixtureA = Start-Process -FilePath $fixtureExe -NoNewWindow -PassThru -ArgumentList @('--role','a','--background','--state-dir',('"' + (Join-Path $fixtureRun 'a') + '"'))
$fixtureB = Start-Process -FilePath $fixtureExe -NoNewWindow -PassThru -ArgumentList @('--role','b','--state-dir',('"' + (Join-Path $fixtureRun 'b') + '"'))
```

Windows 的 A/B 都用 `SW_SHOWNOACTIVATE`、`SWP_NOACTIVATE` 创建并置于后台，不调用
`SetForegroundWindow`、`SetFocus`、`AttachThreadInput`。由人点击 B 文本框。
正常结束使用各进程窗口菜单 **Quit fixture**（关闭该进程全部样例窗），或逐个点关闭按钮。
需要终端关闭时，可以只向刚启动的两个进程的主窗口发送正常关闭请求：

```powershell
foreach ($fixtureProcess in @($fixtureA, $fixtureB)) {
    while (-not $fixtureProcess.HasExited) {
        $fixtureProcess.Refresh()
        if (-not $fixtureProcess.CloseMainWindow()) { throw 'Close the fixture modal/window manually' }
        [void]$fixtureProcess.WaitForExit(1000)
    }
}
```

modal 未关闭或进程卡住时先由人处理。必要的强制清理命令为
`Stop-Process -InputObject $fixtureA,$fixtureB`；强杀不会保证最终快照，必须记录为 interrupted，
不得拿残留的 `running=true` 状态作为验收成功证据。不要按程序名杀其他进程。

## 手动生命周期选项

每窗口 **Move / Resize / Minimize / Modal / Recreate** 都只操作当前样例窗，必须由用户明确点击。
这些选项不在 `increment` 的 profile 范围内；准备和恢复阶段的人工焦点变化不计入后台动作区间。
拖动系统标题栏、边框和在系统界面手工恢复最小化窗口同样可用。

**Recreate** 真正销毁旧窗口，再生成同 slot/同标题、不同实例 token 的新窗口；新窗不主动激活。
关闭一个 A 窗口后，可在另一 A 窗口重开它：macOS 点 **Reopen other A window**，
Windows 用菜单 **Reopen A1 / Reopen A2**。
保留最多 64 个窗口实例（包括 closed tombstone），达到上限必须重启样例，不能静默丢弃旧身份。
系统是否真正复用 windowNumber/HWND 必须单独观察；离线 probe 的复用只是合成场景。
modal 的打开和关闭被记录，整个 modal 区间都应拒绝后台写入。

## 公开状态 schema（fixture 协议 v1，不是产品 API）

目录内只有 `state.json`。macOS 目录 `0700`、文件 `0600`，以 `openat(O_EXCL|O_NOFOLLOW)`
写新文件、fsync 后 rename；Windows 使用当前用户 SID 的 protected DACL，持有不允许删除共享的
目录句柄，写新文件、FlushFileBuffers 后 MoveFileEx 替换。消费者短时读取并关闭文件；Windows
若长时间持有禁止删除共享的读句柄，会使发布失败，样例退出，不静默继续。
这不是防同用户恶意进程的安全边界；当前用户/管理员可以伪造元数据。只信任自己启动的二进制。

主线程约 100ms 更新，操作处理器也可更新。所有数据来自自身窗口和进程。
计数和序号为非负 JSON integer，native ID 为字符串；坐标可为负数，macOS 坐标可含小数。字段如下：

| 层级/字段 | 类型与含义 |
| --- | --- |
| `schema_version`, `fixture_version` | `1`, `"window-fixture-v1"` |
| `build_sha256` | 编译的单平台源文件 SHA-256；不是 Git commit。验收另存候选 commit 和二进制 SHA-256 |
| `platform`, `role`, `background` | `macos/windows`、`a/b`、是否传入后台选项 |
| `pid`, `process_token`, `process_start` | 本进程 PID、启动随机 UUID、OS 启动标记字符串；macOS 为 BSD seconds.microseconds，Windows 为 FILETIME 的十进制 100ns ticks |
| `execution`, `desktop_validation` | `desktop/offline_probe`；始终 `not_run`，样例不会自行认证 profile |
| `running`, `active` | 本进程运行标记、自己的应用/线程是否 active；不读取其他前台应用的身份 |
| `snapshot_sequence`, `observed_monotonic_ns` | 本次发布序号、单调时钟 ns；用于新鲜度，不是墙钟或跨机器时间 |
| `windows[]` | 当前和已关闭的窗口实例；按 slot 不能唯一定位重建后的窗口 |
| `windows[].slot/instance_token/title/native_id` | `A1/A2/B`、创建随机 UUID、展示标题、windowNumber 的十进制字符串或 HWND 的 `0x...` 字符串 |
| `windows[].closed/modal/text/count` | tombstone、手动 modal 状态、真实文本框全文、仅本窗口的计数 |
| `windows[].key/visible/minimized` | 当前状态；macOS key window，Windows 当前线程 active window。仅状态快照不能排除短暂焦点变化 |
| `windows[].bounds/coordinate_unit` | `[x,y,width,height]`；macOS 全局 points/左下坐标系，Windows GetWindowRect 物理像素/左上坐标系；不是截图的局部映射 |
| `windows[].accessibility` | `api: AX/UIA` 与 `window/text/increment` 的准确标识 |
| `windows[].event_totals` | 本实例各事件类型的累计次数；ring 溢出不清零 |
| `event_limit/events_dropped/event_sequence` | `4096`、累计被丢弃事件数、最后事件序号 |
| `events[]` | `{seq, monotonic_ns, window_token, type, data}`；进程事件的 token 为 null |

标识格式为 `scapkit.fixture.<slot>.<instance_token>.<window|text|increment>`，关闭重建后全部变化，
每个存活实例内稳定。macOS 在真实 NSWindow/NSTextField/NSButton 上公开 AXIdentifier；
Windows 每个 HWND 的 `WM_GETOBJECT` 返回真实 UIA provider，AutomationId 包含实例 token，
原生 EDIT 公开 ValuePattern，BUTTON 公开 InvokePattern。UIA SetValue 调用真实 EDIT 的
SetWindowText，Invoke 发出该按钮自己的 BN_CLICKED 通知，和用户点击共用计数处理器；
不发送键鼠消息、不通过状态文件改变控件、不设置焦点。provider 销毁时断开，旧引用不跟随复用的 HWND。
UIA provider 的 STA 生命周期使用
[Microsoft 的服务端 provider 接口](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-serversideprovider)
和 [ProviderOptions_UseComThreading](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationcore/ne-uiautomationcore-provideroptions)。

事件包括 `created/closed/text_changed/increment`，`app_activated/app_deactivated`，
`key_gained/key_lost`，macOS 的 `main_gained/main_lost/focus_changed`，Windows 的
`focus_gained/focus_lost`，以及 move/resize/minimize/modal 事件。
`key_down/key_up` 的 data 只含按键码、修饰键位和重复标记，不含按键字符；macOS 另有 `modifiers`。
Windows modifier 位为 Shift=1/Ctrl=2/Alt=4，macOS 使用 NSEventModifierFlags。
文本值只在 text 字段中回读；macOS 编辑中的 field editor 也被读回。UIA Value 限制 32767 UTF-16 units，
实际验收使用短公开字符串，不把 set_value 当成逐键输入。
`canvas_down/canvas_drag/canvas_up` 只记录本窗口画布局部 x/y；画布保留最近 512 个点，
无全桌面鼠标轨迹。Windows 不捕获鼠标到画布外；出界的轨迹不能算完整证据。

## 接入与真实验收步骤（全部 not_run）

公共 API 另由集成方冻结。本样例不导入产品模块、不约定函数参数或 profile schema。
适配器应消费以下证据，再映射到冻结后的接口：

1. 验证自己的启动 PID、OS 进程启动时间、process token、源代码/二进制 SHA，读新鲜完整快照；
   `offline_probe`、`running=false`、旧快照、缺失/不匹配字段均不得启用动作。
2. 选一个存活的 A1 **实例 token**。macOS 对照 windowNumber 与 AX 窗口根的 AXIdentifier；
   Windows 从完整 HWND 获取 UIA 根，并复核 PID/NativeWindowHandle/AutomationId。
   不按标题、位置或 PID 猜测 A1/A2 关联，保留原根/元素，销毁后废止引用。
3. 将根下 text/increment 的确切标识和真实支持的属性/模式绑定；macOS 用 AXValue/AXPress，
   Windows 客户端须设置并读回 AutoSetFocus=false。只为对应应用构建和这两种动作做候选验证。
   编译或 provider 声明有模式，不等于动作已通过后台验收。
4. 人工先给 A2 设置与 A1 不同的公开文字和计数；点击独立 B 并开始连续输入公开序列、Shift
   按下释放、在 B 画布内连续画线。准备好后保存 A/B 的完整基线快照和序号。
5. 控制端只对 A1 执行 20 次整个值替换与 20 次 increment，记录每次操作时间/结果。
   最终 A1 文本应等于最后约定值、计数精确 +20，A2 的 text/count/instance_token 必须不变。
   不能只检查 API 返回码；timeout/cancellation 仍须按 unknown 处理，不重试计数操作。
6. B 文本与公开输入序列一致、按键/修饰键完整、画布轨迹连续。测量区间内 B 的 key/focus loss、
   app_deactivated 不得增长，A1/A2 的 key gain/app_activated 不得增长；检查完整连续事件区间，
   不仅是最后 active 值。仅看 A2 text/count 未变不能证明 B 未受干扰。
   macOS focus_changed 也须逐条解释，不能出现非人工的变化。准备区间之外的人工切换另行标记。
7. 事件环缺失测量区间、序号不连续、程序退出/状态陈旧、画布出界、用户观察无法确认时，
   标记 inconclusive/not_run，不能靠最后快照推断没有短暂抢焦点。
8. 单窗口截图另验：A1 被 A2/B 部分和完全遮挡，图像只能含 A1。颜色/标签/计数可区分目标；
   图像正确不能替代输入无干扰。移动、resize、跨 DPI、最小化、modal、关闭重建另开专项区间。
   验证旧对象/映射失效且不恢复/激活窗口；没有对应设备的项目记 not_run。

剪贴板哨兵由用户在获准的集成阶段自行设置为 `SCAPKIT-WINDOW-CLIPBOARD-TEST`，
独立 harness 才能在明确授权范围内比对内容与变化序号；样例完全不读取剪贴板。
同时需要原生禁止调用计数、用户连续操作观察和事件日志；这些证据只覆盖这一个样例构建/场景，
不推广到第三方应用。验收通过与否由集成方记录，样例文件永远不把自身标成 verified。

## 本次实际结果与边界

| 项目 | 状态 |
| --- | --- |
| macOS arm64，13.0 deployment target，链接 AppKit/严格编译告警 | passed |
| macOS 15.0 target 语法检查、13.0 target clang 静态分析 | passed；静态证据，不是运行证据 |
| 编译后的 macOS 离线协议检查，Python 3.10 / 3.14t | 各 7 tests passed；不导入产品扩展，不证明其 GIL 行为 |
| 首次非隔离 Python 启动 | failed：Provider 的 PYTHONHOME 引起 encodings 导入失败；改用 python -I 后通过 |
| Windows 编译、MSVC /analyze、离线协议/ACL 测试 | not_run；本机为 macOS，无 Windows SDK/MSVC |
| 两平台真实窗口启动、后台创建、AX/UIA 身份和写入、截图 | not_run |
| 用户 B 连续键入、焦点事件、鼠标轨迹、剪贴板哨兵 | not_run；留给获准的集成阶段 |
| macOS 13/Intel、Windows 10/ARM64 桌面运行 | not_run；源码政策不等于实机证据 |

交付分支仅 `refs/heads/codex/provider-window-fixtures-20261027`；不更新默认分支、不发布版本。
