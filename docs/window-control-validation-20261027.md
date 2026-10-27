# 窗口后台控制集成验证 — 2026-10-27

状态：**集成进行中，等待固定 macOS 提交及 Windows 修正候选/CI 结果**。
本文件记录实现自检，不是独立 R 验收，也不是任意应用后台控制验收。
唯一交付分支是 `refs/heads/codex/provider-window-control-20261027`，仓库为
`https://github.com/czf0613/computer_use_py.git`。没有默认分支写入、tag、release、
发布 workflow、强制推送或其他 Agent 工作区操作。

## 固定输入与合并

| 输入 | 精确版本与处理 |
| --- | --- |
| 原代码基线 | `25750d1e82e0d52853f6eba7825de23408fd5e05` |
| 固定方案 | `18304c2b16f562c50e0434b7d41d53fc6ac944a3`，通过公共合同祖先保留 |
| 公共 Python/ABI 合同 | `01b318287d40d9124ae2708eaa3f1fcc6821cbbc` |
| Provider MCP 输入 | `933692576cc18ea84202ff9d97dc610c5a2b3a50`，Provider input_sync 已固定，本工作区起始 HEAD 与之相同；普通 merge 确认 already up to date，没有重抓移动 tip |
| Windows 初始候选 | 按 SHA fetch 并普通 merge `a5e2c6a35fbaf4db9615fda19d8fcdf35a7c7e81`，含 fixture `f03d182beaf14a4d1f40c9d083246eec7ccbadea`；保留阶段祖先 |
| Windows 后续修正 | 等待上层给出准确 SHA；不自行修改上层已交回 Windows Agent 的截图 deadline/quarantine 问题 |
| macOS 最终候选 | 尚未收到准确 SHA；不猜移动分支，不读取其本机工作区 |

Windows 候选 [CI 40479320493](https://github.com/czf0613/computer_use_py/actions/runs/40479320493)
由上层监测；收到的状态仍是 pending，不能算通过。本集成尚未触发 workflow。

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
可观察失效，不能宣称排除了不可观察的跨进程 ABA。最终 macOS 实现及持续观察 stream
的新鲜帧、close 生命周期说明，在收到固定提交并核验后补齐，不把方案中的短 stream
描述预先当作最终实现事实。

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

## 本地实际检查

全部 Python 命令使用局部 `env -u PYTHONHOME -u PYTHONPATH`、uv/setuptools 和
`python -I`；没有修改全局环境。两个环境位于本 Provider 工作区的 `.venv` 与
`.cache/window-integration-314t`。runner 校验 package/window/MCP/native 路径属于
本 checkout；3.14t 在同进程 import 前、pytest 前后断言 GIL 关闭。

以下是收到 macOS 后端前的实际阶段结果；此时本机 macOS extension 无窗口 ABI，
`backend_unavailable` 被显式核验，不伪装为原生窗口测试通过。

| 检查 | 实际结果 |
| --- | --- |
| Python 3.10 公共 window + process 隔离 runner | `114 passed in 1.71s` |
| Python 3.14t 公共 window + process 隔离 runner | `114 passed in 1.52s`；`gil_enabled_after_tests=False` |
| Python 3.10 MCP 三文件（含 10 个新增场景） | `94 passed, 1 warning in 5.50s` |
| Python 3.14t MCP 三文件 | `94 passed, 1 warning in 5.27s`；`gil_enabled_after_pytest=False` |
| Ruff，改动 MCP/runner/测试文件 | `All checks passed!` |
| CI YAML / Python 3.10 syntax / 发布 workflow 差异检查 | `macOS matrix=14; Windows matrix=6 unchanged; Python 3.10 syntax=valid; release workflow=unchanged` |
| 两 ABI 完整窗口 native/旧 capture/recording 回归 | **not_run**，等待 macOS 固定输入 |
| 最终 testing/production 强制构建与 hooks 检查 | **not_run**，等待 macOS 固定输入 |

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

## CI 接入和未验收项

macOS CI 保留 15/26 arm64 × 3.10–3.14/3.13t/3.14t 共 14 项矩阵，新增
`tests/macos_window_checks.py` 接入公共/原生 window 及原有离线 capture、参数校验、
async safety、recording、modifier、release artifact、process 测试。uv/setuptools
构建本 checkout；production 强制重建后断言无 `_test_*`。MCP 三文件也在单解释器
内验证 GIL；继续排除不支持依赖的 3.13t MCP extra。

Windows **保留原 6 项矩阵**（x64 3.10/3.14/3.13t/3.14t、ARM64 3.14/3.14t）及
所有既有断言，仅把允许的普通 x64 MCP job 接入含 window 的隔离 runner；没有将
Windows 编译失败改成 skipped、降低 GIL 要求或引入 OpenSSL 构建。

上层已报告 Windows 外层 actor deadline 早于内层 screenshot deadline，会让正常
无帧触发永久 quarantine；原 Windows Agent 正在修复/增加合成验证，本阶段未代改。
更早 BSTR 编译失败及其修正保留在 [Windows handoff](window-windows-handoff.md)，
不能用旧失败覆盖未来确实通过的最终结果，也不能提前宣称通过。

下列均 **not_run / 未具备证据**：本集成的 macOS fixture 重验、Windows 桌面 UIA/WGC、
连续人类键鼠/修饰键非干扰、真实剪贴板哨兵、跨 DPI 设备、macOS 13/Intel、Windows
10/ARM64 桌面、任意第三方应用后台控制、生产 profile 审核，以及指定最终 SHA 的
独立 inspection worktree 验收。没有运行全套旧桌面 tests，没有操作用户应用、读取
真实剪贴板或申请 OS 权限。fixture/合成证据分别记录，不自动上升为生产信任。
