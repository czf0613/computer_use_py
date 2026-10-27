# 窗口后台控制集成验证 — 2026-10-27

状态：**macOS 三项 review 修复与 Windows 排队超时/fixture 修复已整合。
Mac 本地针对性检查、自有 fixture 和双 ABI production 通过；等待新集成 Windows CI
及最终公共接口/MCP/Windows 独立 review**。Windows 上轮六项 CI 均失败，未算全绿。
上层已报告 macOS 固定修复 SHA 的独立复核关闭原三项问题；本文件的本地检查仍是
实现自检，不能替代最终公共接口/MCP/Windows 独立验收或任意应用后台控制验收。
唯一交付分支是 `refs/heads/codex/provider-window-control-20261027`，仓库为
`https://github.com/czf0613/computer_use_py.git`。没有默认分支写入、tag、release、
发布 workflow、强制推送或其他 Agent 工作区操作。

## 当前候选检查及剩余限制

本轮按固定 SHA `6e7db274028f83df27c7a613af0092a6e052edc4` 使用
`git fetch --no-tags` 和普通 merge，未改变原 input_sync `9336925`。
本 checkout 的原生实现与该发布提交一致；保留集成层 MCP incomplete cleanup
配额修复，以及 manual harness 的有限只读刷新和 nested finally。

| 当前检查 | 实际结果与范围 |
| --- | --- |
| Python 3.10 testing 强制构建及 `--review-only` | `168 passed in 1.05s`；20 项新增 transport 注入、73 项原生 window 和相关公共 API/身份/坐标回归 |
| Python 3.14t 同一检查 | `168 passed in 1.09s`；同进程导入及 pytest 后 GIL 关闭，扩展来自本 checkout |
| macOS 13.0 testing / 15.0 production preprocessing | strict warnings/availability 和 Clang static analysis 均 exit 0；不是旧系统运行验收 |
| 3.10 / 3.14t production 分别强制构建 | 两者 exit 0：ABI 1、空生产 profile 信任表、无 `_test_*`；3.14t 导入前后 GIL 关闭 |
| 3.10 自有 fixture，修复后运行一次 | exit 0；BGRA/JPEG 均 1080×804，mapping verified；AXValue/Press、兄弟窗口不变、错误根拒绝、关闭失效通过；一次有限只读刷新，没有重试动作，子进程已 reap |
| macOS CI runner 接入 | 新增 `test_window_macos_review.py`，并要求 testing AX transport hook 存在，缺失时直接失败；14 项矩阵保留，本轮未 dispatch |
| Windows 新增测试的 3.10/3.14t 语法及公共截图签名 | 两者通过；3.14t GIL 保持关闭，没有运行 Windows native |
| Windows fixture source/pin | SHA-256 `3bc6c190c861369f7ae2e6f73ef3861766aa563c52bac0031cda6a5082d20251` 一致；MSVC/Windows runtime 待 CI |
| 改动 runner/Windows 测试 Ruff 与 Git diff 检查 | exit 0；保留 timeout child 的 `check=False` 和正常/hung/queued 三类覆盖 |

Windows 修复合入后再次确认 macOS native、setup.py、公共 API/MCP 实现、自有 Mac
fixture 和 manual harness 与上述检查时完全一致，因此未重复 Mac 检查。两平台均
保留各自固定发布提交的原生实现，没有用本机 portable 检查冒充 Windows 原生验收。

fixture 只预检已有权限，未申请权限、操作用户应用、读取剪贴板或设置焦点。
set_value accepted/effect verified；invoke accepted/effect unknown，同时 fixture 自身
状态确认文本及计数生效。`human_continuous_input=not_run`、`profile_validation=not_run`。
生产 profile 信任表仍为空，生产写操作继续 `background_not_verified`。

上层报告原 macOS reviewer 在固定 `6e7db27` 的隔离 inspection worktree 复核：
双 ABI 各 20 项新增、7 项生命周期及 11 项独立生产路径探针通过，3.14t GIL off，
双 ABI production 无 test hooks，原三项关闭、无新增确定问题。这是**上层转交的
独立结果**，不是本集成 Agent 自称完成独立 review。最终公共接口/MCP/Windows
review 和完整集成 Windows CI 尚待完成；Windows 桌面和连续人类输入等限制见末节。

## 固定输入与合并

| 输入 | 精确版本与处理 |
| --- | --- |
| 原代码基线 | `25750d1e82e0d52853f6eba7825de23408fd5e05` |
| 固定方案 | `18304c2b16f562c50e0434b7d41d53fc6ac944a3`，通过公共合同祖先保留 |
| 公共 Python/ABI 合同 | `01b318287d40d9124ae2708eaa3f1fcc6821cbbc` |
| Provider MCP 输入 | `933692576cc18ea84202ff9d97dc610c5a2b3a50`，Provider input_sync 已固定，本工作区起始 HEAD 与之相同；普通 merge 确认 already up to date，没有重抓移动 tip |
| Windows 初始候选 | 按 SHA fetch 并普通 merge `a5e2c6a35fbaf4db9615fda19d8fcdf35a7c7e81`，含 fixture `f03d182beaf14a4d1f40c9d083246eec7ccbadea`；保留阶段祖先 |
| Windows 后续修正 | 按 SHA fetch 并普通 merge `73d9bf53724f98831b06b7406274e55e41f922ef`，再普通 merge `50f09b62be977dca79fd27010f774b1a8d22194b`；统一截图 deadline、修正 fixture header 路径及测试参数 |
| Windows 排队与 fixture 修复 | 按 SHA fetch --no-tags 并普通 merge `e346d22aee18f14c9efdd956c9dd574b6951d5c2`；保留集成测试 `check=False`，无冲突覆盖 |
| macOS 初始候选 | 按 SHA fetch 并普通 merge `b6a020645fb980db95e4c2bbd6ded72c417f7070`，自动合并 setup.py，保留 Windows 源/库及 macOS Security framework |
| macOS review 修复 | 按 SHA fetch --no-tags 并普通 merge `6e7db274028f83df27c7a613af0092a6e052edc4`；保留旧提交祖先及失败历史 |

Windows 候选 [CI 40479320493](https://github.com/czf0613/computer_use_py/actions/runs/40479320493)
由上层监测；Windows handoff 记录六项库构建及各 186 项测试通过，但 fixture 编译出现
`C1083: build/window-fixtures/fixture-build.h` 找不到，**不是全绿**，后续 MCP/production
步骤未运行。修正候选的 [CI 40481667396](https://github.com/czf0613/computer_use_py/actions/runs/40481667396)
测试 `73d9bf5`，上一阶段查询为 `in_progress`，而且它不含 `50f09b6` 的参数修正。

本集成 [CI 40482766511](https://github.com/czf0613/computer_use_py/actions/runs/40482766511)
准确测试 `74584d4e6f247a0388ebd89da31a675a92a3e19b`，本轮 `gh run view` 确认
**六项全部 failure**，失败步骤均为 fixture compile/protocol。上层核验各项 native
build 和 189 tests 已通过，MCP/production 后续未运行。本轮读取失败日志的实际原文：
`UIAutomationCore.h(49): error C4430: missing type specifier - int assumed. Note: C++ does not support default-int`；
六项均记录 `Process completed with exit code 1.`。这不是矩阵全通过，不能以 Mac MCP
结果代替未运行的 Windows MCP。现在已合入准确 Windows 修复 `e346d22`，发布本候选
后只 dispatch 一次完整集成 `windows.yml`；run ID/URL/head SHA 记录在 Provider 回执，
结果尚未返回时为 needs_input/published，不触发 macOS/release，不长时间 watch。

## Python / MCP 入口与边界

Python 新入口为 `list_windows` / `open_window`、独立的
`list_accessible_windows` / `open_accessible_window`。两类 session 均支持
`capabilities`、`elements`、`element_at`、`set_value`、`invoke`、`aclose`，并提供
`closed`、`cleanup_complete`、`last_action`。capture session 另有 `screenshot` 和
显式 `attach_accessibility`。`async with` 负责关闭；裸 ID、标题或 token 字符串不能
重建 Python opaque ref。原 display/process/recording API 保持原语义。

MCP 对应 `window_list/open/capabilities/screenshot/elements/element_at/set_value/invoke/close/status`、
`accessible_window_list/open` 和 `window_attach_accessibility`。每个 token 归属于可信
SDK 连接；支持 stdio、stateful HTTP 的 legacy 握手。默认 stateless HTTP 及 SDK 2.2
现代 HTTP 单次交换返回 `unsupported_transport`，不以客户端自报 header 代替身份。
Host/Origin、bearer、单 worker 约束保持。完整参数见 [MCP 文档](mcp-server.md)。

截图来自窗口采集对象，不是整屏裁剪。图像尺寸是 pixels；窗口局部单位在 macOS 是
points、Windows 是 physical pixels。`frame.to_local` 保留 generation/geometry provenance；
MCP 用 frame/snapshot token 检查时效。`element_at` 是树内语义定位，不发送鼠标事件。
AX/UIA 写操作只有整个值替换及精确控件 invoke；坐标点击、键盘、拖动、剪贴板、
窗口激活/恢复/焦点操作不提供后台兜底，不支持时明确报错。

截图身份与语义身份分别建立。普通 macOS SCWindow 只读截图不应依赖 fixture/AX 身份；
公共 API 不能凭同 PID/标题把它绑定到 AX 根。SCWindow 的保证限定原对象/进程实例及
可观察失效，不能宣称排除了不可观察的跨进程 ABA。

已合入的 macOS 后端确实是**每次截图创建短寿命 SCStream**，没有持续观察流。
完整帧的 display timestamp 必须晚于本次 capture 开始；旧/不完整帧不替代新帧。
成功路径等待 stop 并移除 output，迟到 start/stop 使用独立 retained owner；超时仍
未完成时有界 capture gate 继续占用。`6e7db27` 修复了旧版把 stop/remove 失败也
报为完成的问题：只有两者都成功才释放 owner/gate，否则 close 持续 incomplete。
失败时保留至进程退出、最多一个 stream 的纯 native owner；不重试 teardown 或
创建替代 stream。离线 transport 证明状态/资源边界，没有证明真实 SCK 泄漏或零泄漏。
native 离线测试覆盖实际 CMSampleBuffer 接收器、旧帧拒绝、两种 late-start 时序、
会话 close 前后清理及像素所有权。contract 已澄清“成功返回已关闭”和“超时后保留
待清理 owner”的区别，没有改 ABI，也没有把 GC 当清理确认。

`window_profiles._APPROVED_EVIDENCE_SHA256` **保持空表**。注册候选不产生授权，
生产写操作为 `background_not_verified`；fixture 成功或编译/合成通过不自动写入信任表。
没有 unsafe 开关。未知写操作可能已执行，保留 operation ID，禁止自动重试或换 session
重试。真实 AX/UIA 调用路径存在也不代表任意第三方应用无副作用。

## MCP 关闭失败：实际复现及修复

在原输入中，`expire()` 调用 `close_entry()` 后无条件移除 session；后者保存 close
异常而不向 reaper 抛出。`close_client()` 同样无条件清空 sessions 并丢弃 owner。
因此 native 返回 `backend_busy` / `outcome_unknown`、Python 标记 closed 但
`cleanup_complete=False` 时，闲置/断开仍释放配额，新的 open 可以再次进入 native。

新增测试经真实 SDK/公共 Python API，fake native 保留未关闭 handle 并返回失败；
不是仅对私有 dict 做相同实现的镜像断言。覆盖直接闲置、显式 close 后闲置、legacy
和现代进程内连接断开、stateful HTTP DELETE；外部行为断言是第二次 open 必须返回
`resource_limit`，原连接仍能查询 incomplete 状态，其他连接不能读取旧 token，重复
close/reaper 不重发 native close。最初 **10 failed, 49 deselected**，失败点是新的
open 错误成功；修复后同命令 **10 passed, 49 deselected in 0.65s**。

修复仅在 `cleanup_complete=True` 时逐出。断开后仍保留不可访问的 closed owner 与
incomplete 条目，计入服务的 32 client / 64 session 上限；连接内 8 session 配额也
保留。这避免断开/重连绕过容量，没有增加新 native ABI、重试、GC 清理承诺或线程。
无迟到清理确认接口时保守保留容量，可能持续到服务退出。

**真实资源边界**：MCP 配额不是 OS 内存/线程硬上限。Python closed、传输断开、
capsule 析构和 GC 均不证明 AX/UIA provider 已返回。原生 actor 必须保留仍被 callback
使用的纯 native 状态，超时隔离且不创建替代线程；迟到资源由原生层收尾。取消 open
未向 MCP 交出 session 的路径仍由公共 Python API 排空并关闭迟到 handle，失败通过
异常链暴露，MCP 无法凭空生成 native cleanup 确认。退出/强杀亦不作优雅清理保证。

## 历史阶段的本地实际检查

全部 Python 命令使用局部 `env -u PYTHONHOME -u PYTHONPATH`、uv/setuptools 和
`python -I`；没有修改全局环境。两个环境位于本 Provider 工作区的 `.venv` 与
`.cache/window-integration-314t`。runner 校验 package/window/MCP/native 路径属于
本 checkout；3.14t 在同进程 import 前、pytest 前后断言 GIL 关闭。

以下是收到 macOS 后端前的实际阶段结果；当时本机 macOS extension 无窗口 ABI，
`backend_unavailable` 被显式核验，不伪装为原生窗口测试通过。

| 检查 | 实际结果 |
| --- | --- |
| Python 3.10 公共 window + process 隔离 runner | `114 passed in 1.71s` |
| Python 3.14t 公共 window + process 隔离 runner | `114 passed in 1.52s`；`gil_enabled_after_tests=False` |
| Python 3.10 MCP 三文件（含 10 个新增场景） | `94 passed, 1 warning in 5.50s` |
| Python 3.14t MCP 三文件 | `94 passed, 1 warning in 5.27s`；`gil_enabled_after_pytest=False` |
| Ruff，改动 MCP/runner/测试文件 | `All checks passed!` |
| CI YAML / Python 3.10 syntax / 发布 workflow 差异检查 | `macOS matrix=14; Windows matrix=6 unchanged; Python 3.10 syntax=valid; release workflow=unchanged` |

AnyIO assertion-rewrite warning 来自先导入检查再执行 pytest，不是 GIL 开启。
初次 3.14t 命令用了 `--no-sync` 未写 `--python 3.14t`，uv 提示与 `.python-version`
的 3.10 请求不符；实际解释器/扩展是 3.14.6t 且同进程 GIL 断言通过。后续命令显式
选择 3.14t，不改全局配置。阶段复现命令：

```sh
env -u PYTHONHOME -u PYTHONPATH uv run --no-sync python -I tests/window_contract_checks.py
env -u PYTHONHOME -u PYTHONPATH UV_PROJECT_ENVIRONMENT=.cache/window-integration-314t uv run --no-sync --python 3.14t python -I tests/window_contract_checks.py --free-threaded
env -u PYTHONHOME -u PYTHONPATH uv run --no-sync python -I tests/mcp_window_checks.py
env -u PYTHONHOME -u PYTHONPATH UV_PROJECT_ENVIRONMENT=.cache/window-integration-314t uv run --no-sync --python 3.14t python -I tests/mcp_window_checks.py --free-threaded
```

合入 macOS 后的新一轮检查：

| 检查 | 实际结果 |
| --- | --- |
| 3.10/3.14t testing 强制构建，`SCAPKIT_TESTING=1 CFLAGS=-Werror` | 两者 exit 0，本 checkout ABI 1 |
| 3.10 完整受影响离线 runner（含原有 release artifacts） | `422 passed in 38.63s` |
| 3.14t 同一 runner | `421 passed, 1 skipped in 40.90s`；导入前后/pytest 后 GIL 关闭 |
| 3.10 MCP 三文件 | `94 passed, 1 warning in 5.16s`；native ABI 1 |
| 3.14t MCP 三文件 | `94 passed, 1 warning in 5.39s`；pytest 后 GIL 关闭 |
| 三个新 macOS 模块 strict warnings/availability，13.0 与 15.0 target | 两个 `strict_warnings ...: passed` |
| 同模块 clang static analysis，13.0 与 15.0 target | 两个 `static_analysis ...: passed` |
| 3.14t production 强制构建/隔离导入 | exit 0，`production_test_hooks=absent`，ABI 1，GIL 关闭 |
| 3.10 production 强制构建/隔离导入 | exit 0，`production_test_hooks=absent`，ABI 1，空生产信任表 |

唯一 skip 是既有 `test_native_capture` 对 Python dict 分配时同步 GC 的要求，不适用
free-threaded GC；不是跳过新的 window 测试，也没有弱化 GIL 断言。完整回归使用
`tests/macos_window_checks.py`；MCP 仍使用独立 runner。仅新变更/真实失败触发重跑。

## 历史阶段的自有 macOS fixture

由本 checkout 重建 fixture；source hash
`826b5520278953797bea9167ed6ab0a5db0293fb3f81762d166c1b16b880d30c`、binary hash
`d7f5a799567efe0cde4f44bcff69eac7e725a84ed6e6a28b0f5ecfd2ac66d53e` 与已审核 pin
完全一致，未调整 pin 或绕过检查。harness 只启动自己的子进程，使用最小新环境，
按 PID/fixture 根选择对象；只预检已存在的权限，不申请权限，不读剪贴板，不保存截图。

实际失败保留：第一次 BGRA/JPEG 与桥接成功，首次 AX 树返回
`stale_element:tree_changed`；增加有限只读刷新后的下一次运行在 set_value 前得到
`stale_element/not_dispatched`，目标未变化，脚本 exit 1。临时 testing-only
诊断捕捉到树从 17 个节点变为 18 个，后两节点的 object/parent/role/bounds 等字段
发生变化，旧快照被正确拒绝；诊断运行也 exit 1。临时诊断已移除，macOS native 源码
与固定输入一致。没有重试任何 accepted/unknown 写操作。

脚本现在最多三次只读 elements/validate_snapshot，只有确认 not_dispatched 且
retry_hint=refresh 的 stale 快照才刷新；每项候选动作仍只提交一次。它为初始 AX
树变动提供可见的准备步骤，不降低 native 身份/epoch/几何或生产 profile 保护。
最终脚本 exit 0，确实发生一次只读快照刷新，未重试动作：BGRA/JPEG 均为 1080×804、
mapping verified；`capture_open_without_ax_root`、exact bridge、explicit attachment
和 sibling bridge rejection 均 passed；set_value accepted/effect verified，invoke
accepted/effect unknown，同时 fixture 独立状态确认 `target_effect=true`（文本及计数）、
`sibling_unchanged=true`。A1 关闭后旧 capture 被拒绝，A2/进程仍在；随后关闭 handles、
终止并 reap 自有进程，`owned_process_cleanup=true`、`fixture_exit_code=0`。
最后增加嵌套 finally，确保一个 handle close 失败不跳过另一个 handle/子进程清理，
之后再次只跑该脚本也 exit 0，以上结果相同。源码与二进制 fixture pin 均未改动。
测试脚本的原有 import 排序及独立 CLI 的 blocking-launch/catch-all lint 项已处理：
排序修正，后两项用注明用途的局部 noqa；最终针对改动文件的 Ruff 通过。

这些结果覆盖本机 macOS 26.6.2 arm64 自有 fixture 的指定操作，不是连续非干扰证明。
截图成功也不保证不可观察的 ABA 被消灭；窗口身份和动作能力仍分别判断。
`human_continuous_input=not_run`、`clipboard=not_accessed`、`profile_validation=not_run`。
最终 production 构建不含 candidate-action hook，测试没有注入生产证据 pin。

后合并复现命令（testing/production 需要分别 force rebuild）：

```sh
env -u PYTHONHOME -u PYTHONPATH SCAPKIT_TESTING=1 CFLAGS=-Werror uv run --no-sync --python 3.10 python -I setup.py build_ext --inplace --force
env -u PYTHONHOME -u PYTHONPATH PYTHON_VARIANT=3.10 uv run --no-sync --python 3.10 python -I tests/macos_window_checks.py
env -u PYTHONHOME -u PYTHONPATH PYTHON_VARIANT=3.14t UV_PROJECT_ENVIRONMENT=.cache/window-integration-314t uv run --no-sync --python 3.14t python -I tests/macos_window_checks.py
sh tests/manual/build_window_fixture_macos.sh
env -u PYTHONHOME -u PYTHONPATH uv run --no-sync --python 3.10 python -I -X faulthandler tests/manual/verify_window_macos.py --desktop
env -u PYTHONHOME -u PYTHONPATH SCAPKIT_TESTING=0 CFLAGS=-Werror uv run --no-sync --python 3.10 python -I setup.py build_ext --inplace --force
env -u PYTHONHOME -u PYTHONPATH PYTHON_VARIANT=3.10 uv run --no-sync --python 3.10 python -I tests/macos_window_checks.py --production
```

3.14t 的 force rebuild 同样加 `UV_PROJECT_ENVIRONMENT=.cache/window-integration-314t`
及 `--python 3.14t`；不要用 3.10 编译产物代替。静态分析/availability 命令是
`tests/window_macos_checks.py --target 13.0/15.0`，分析另加 `--analyze`。

## CI 接入和未验收项

macOS CI 保留 15/26 arm64 × 3.10–3.14/3.13t/3.14t 共 14 项矩阵，新增
`tests/macos_window_checks.py` 接入公共/原生 window 及原有离线 capture、参数校验、
async safety、recording、modifier、release artifact、process 测试，并纳入本轮新增的
macOS review transport 测试。uv/setuptools
构建本 checkout；production 强制重建后断言无 `_test_*`。MCP 三文件也在单解释器
内验证 GIL；继续排除不支持依赖的 3.13t MCP extra。

Windows **保留原 6 项矩阵**（x64 3.10/3.14/3.13t/3.14t、ARM64 3.14/3.14t）及
所有既有断言，仅把允许的普通 x64 MCP job 接入含 window 的隔离 runner；没有将
Windows 编译失败改成 skipped、降低 GIL 要求或引入 OpenSSL 构建。

Windows 的 `73d9bf5` 已将 admission、排队、WGC 等帧和编码统一为同一个 absolute
deadline；额外一秒仅用于完成/清理 grace，不是延长等帧预算。新离线场景区分正常
`capture_timeout` 后 actor 仍可用与真实 hung capture/cleanup 的永久 quarantine。
fixture `/FI` 参数现为生成 header 的绝对路径。本机没有 Windows 编译器/运行环境，
这些原生行为仍由集成 CI 验证，不从本地语法检查推断通过。

`e346d22` 进一步以 queued phase 原子 `0 -> 3` 取消排队中已超时的请求：只向该调用
报告 `actor.queue_timeout/not_dispatched`，不会隔离另一 session 仍在预算内的 healthy
actor。只有已经 running 且超出完成预算的请求沿用永久 quarantine。新增 synthetic
场景让 50ms screenshot/Invoke 排在健康长截图后，断言旧请求不迟到执行，之后截图和
Invoke 仍可用；原正常 no-frame 和两种 hung 覆盖均保留。Windows Agent 的 portable
production Actor probe 通过仅是其 handoff 证据，集成后的 Windows extension 执行待 CI。
fixture 在 UIAutomation.h 前显式加入 ole2.h，并同步 source pin；setup.py 的 native
build hash 包含该 pin header。生产信任表没有更新，没有新的 unsafe/全局输入路径。
上层已安排 Windows reviewer 定向复核 `e346d22`，本文件不预先记为 review passed。

上一阶段先离线复现新 child harness 的 `screenshot(timeout=0.1)` 与冻结公共签名不符，
真实报 `TypeError: got an unexpected keyword argument 'timeout'`。本集成修正为
`timeout_seconds`，随后普通合并上层提供的相同修正 `50f09b6`，保留双方祖先。
Python 3.10/3.14t 的 AST/实际 `inspect.signature.bind` 检查通过，3.14t GIL 保持关闭；
没有调用 native capture。针对新文件的 Ruff 首次报告 subprocess.run 缺少显式
`check`，现补 `check=False`，保留对子进程 returncode/stdout/stderr 的原断言。
重新检查通过。上一阶段 macOS native/公共 API/MCP 实现与已测 `5cc6466` 完全一致，
故当时未重复 macOS 检查。本轮因 macOS native 确有新变更，只重建/验证相关路径和
一次自有 fixture，没有扩展到无关全部平台矩阵；没有 dispatch macOS/release。

上层的独立 macOS review 针对 `b6a0206` 离线复现了三项生产路径问题：
`SWShot.stop` 在 stop/remove 均失败时仍完成并释放 gate；`AXChildren` count/values
错误被 `read_tree` 当空树；`SWElements` 仅比较前后 fingerprint，未校验中间实际
返回 nodes 内容。原 macOS Agent 新增注入测试在修复前真实 **12 failed, 8 passed**，
之后发布 `6e7db27`。当前已合入：AXChildren IPC/无效对象/不合法数组上报 stale，合法
空叶/显式截断仍允许；返回 nodes、保留对象及 fingerprint 来自同一次观察，再做
一次校验，发现变化就拒绝，不自动重试。新检查与上层独立复核见本文开头；旧通过
不覆盖这些历史失败，修复后的通过也不改写旧失败。最终公共接口/MCP/Windows 独立
review 尚未完成。

更早 BSTR 编译失败及其修正保留在 [Windows handoff](window-windows-handoff.md)，
不能用旧失败覆盖未来确实通过的最终结果，也不能提前宣称通过。

下列均 **not_run / 未具备证据**：Windows 桌面 UIA/WGC、
连续人类键鼠/修饰键非干扰、真实剪贴板哨兵、跨 DPI 设备、macOS 13/Intel、Windows
10/ARM64 桌面、任意第三方应用后台控制、生产 profile 审核，以及指定最终 SHA 的
独立 inspection worktree 验收。没有运行全套旧桌面 tests，没有操作用户应用、读取
真实剪贴板或申请 OS 权限。fixture/合成证据分别记录，不自动上升为生产信任。
