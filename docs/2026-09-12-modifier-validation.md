# 2026-09-12 macOS 修饰键真机验收

## 结论

**独立复现了 0.0.3 的 Command 残留与窗口焦点故障；当前源码的修复通过本机验收。**
同时复现并修复了旧 `mouse_drag` 只移动光标、不能正确跨屏拖动 AppKit 窗口的问题。
根因、事件源/投递层选择、资源所有权和取消语义见 [修饰键生命周期](modifier-lifecycle.md)。

用户在本任务明确授权“允许测试当前macOS”后执行。所有输入限定在两个自建的
AppKit 测试窗口；直接调用本仓库 API，不经过 AIOS 或 MCP。没有修改 AIOS
源码、虚拟环境或安装包。

## 环境与构建

- macOS **26.6.2 arm64**，CPython **3.10.20**，Accessibility 权限原本已具备。
- 本仓库 `src/scapkit_computer_use/screen_capture_kit/_scapkit.cpython-310-darwin.so`，
  最终使用 `SCAPKIT_TESTING=0 CFLAGS=-Werror` 强制构建，确认不含 `_test_*` hooks。
- 基线从 `v0.0.3` / `08c356ae6095ba52192cb526ec988c985a5d1ace` 导出到
  `/tmp/scapkit-baseline-003-20260912` 并单独构建，未替换任何环境中的安装包。
- 主屏：ID 5，`(0, 0, 1920, 1080)` points；副屏：ID 1，
  `(-1496, 160, 1496, 967)` points；两屏 scale 均为 2。
- 初期使用中文拼音输入法，精确单键文本断言受到 IME 转换影响，事件中的 keycode
  和字符仍正确。切到 ABC 后完整重跑通过；测试结束已恢复
  `com.apple.inputmethod.SCIM.ITABC`。测试窗口独立关闭自身文本自动纠正。
- 剪贴板被测试中文覆盖；不读取或保存原剪贴板，不访问个人文本或屏幕图像。

## 独立基线复现

组合键和单独粘贴分别启动新的 A/B 窗口。A 起始焦点与编辑器选择范围经过检查。
两个案例均满足：

1. 开始 combined/HID 为 `0x20000000`。
2. `key_combo("a", {"command"})` 或单独 `clipboard_paste()` 返回后，均为
   `0x20100000`；全选或中文插入确实生效。
3. 只读等待 100 ms 后仍残留 Command。
4. 点击 B 标题栏正常返回，焦点仍在 A，Command 仍残留。

判断仅针对 `flags & 0x001e0000`。每个失败案例的原始证据完整记录后，才在单独的
测试收尾阶段发布本案例引入的 Command 对应释放，防止污染下一案例及用户桌面。
没有发送 Escape，也不把收尾之后的结果算作修复通过。原始 standalone harness
默认遇到残留即停止；此次基线焦点诊断使用独立脚本继续验证已知故障点击。

## 最终真机验收

| 检查 | 结果 |
| --- | --- |
| 单独 Cmd+A 的选择范围、系统 flags、B 的实际焦点 | 通过 |
| 无预先组合键的单独中文粘贴、系统 flags、B 的实际焦点 | 通过 |
| 连续 Cmd+A → 中文粘贴 → B 标题点击 | 通过 |
| Command / Command+Shift / Command+Option / Command+Control / 四键组合的真实菜单动作 | 通过 |
| 每种快捷键、粘贴后接普通点击、输入、滚动、拖动 | 通过 |
| 18 次快捷键/粘贴返回后立即创建鼠标事件，无中间观察延时 | 通过 |
| 四种修饰键的真实 Task 取消与注入 await 异常，清理后点击切换焦点 | 通过 |
| 低层 API 显式持有四种修饰键，高层快捷键结束后仍保持其 HID/combined flags | 通过 |
| 普通低层 key down/up 配对 | 通过 |
| B 主屏→副屏→主屏的真实窗口拖动 | 通过 |
| 跨屏后的 B→A→B 实际焦点、输入、普通滚动/拖动 | 通过 |

最终 full 模式记录 275 个前后快照、751 条应用事件；lifecycle 模式记录 167 个快照、
326 条事件。所有无预期持有的完成点均立即检查 combined 和 HID 的四种修饰键位；
最终均为 `0x20000000`，四种修饰键位为零。显式持有场景则核对指定非零掩码，
不会把合法持有当作需要清除的污染。

跨屏拖动测量 B 的真实窗口位置：主屏 `(970, 323)` → 副屏 `(-968, 442)` →
主屏 `(970, 323)`，尺寸始终 `440 × 433`，窗口所属屏幕 ID 为 `5 → 1 → 5`。
两次拖动完成时 key window 均为 B。此验证是窗口拖动，不是文件/数据拖放。

## 离线与静态验证

- Python 3.10 指定安全测试集：**200 passed**，包含 native/capture 生命周期、输入、
  subprocess 与可选 MCP 测试。不会向桌面发布事件。
- Python 3.14t 隔离构建与基础安全测试：**178 passed, 1 skipped**。跳过的是仅适用
  常规 GIL 解释器的 GC 回调场景；运行前后均断言 GIL 关闭。此轮未重复验证 3.14t MCP。
- 最终输入/异步专项：**81 passed**，其中 63 个 native 修饰键/拖动回归。
- 缺失主键 key-up：即使修饰键已清且 source 引用释放，接收器仍记录未配对 down，
  native 必须报告确认超时。包含延迟投递、重复取消、异常链及事件循环响应验证。
- Clang `-Werror` 与 API availability：12.3 / 15.0 部署目标，production/test hooks
  两种配置；`control.c` 静态分析通过。所有 Python 源码以 3.10 语法解析通过。
- 修改的输入代码和回归脚本通过针对性的 Ruff 检查；`git diff --check` 通过。
- 独立审查发现的事件循环阻塞、取消被超时覆盖、接收器隐藏未释放键和接收器数据
  竞争均已处理并重新审查。不能据此声称不存在所有内存泄漏或系统竞态。

本机原始记录保留在以下临时目录（不提交二进制和大量事件日志）：

- `/tmp/scapkit-legacy-focus-20260912/combo/report.json`
- `/tmp/scapkit-legacy-focus-20260912/paste/report.json`
- `/tmp/scapkit-baseline-cross-20260912/report.json`
- `/tmp/scapkit-final-full-20260912/report.json`
- `/tmp/scapkit-final-lifecycle-20260912/report.json`
- `/tmp/scapkit-final-sequence-20260912/report.json`
- `/tmp/scapkit-final-310.log`、`/tmp/scapkit-final-314t.log`
- `/tmp/scapkit-final-regressions.log`

重跑入口见 [手动测试说明](../tests/manual/README.md)。临时文件可能被系统清理，
本文件保留验收条件、结论及关键数值，不依赖 AIOS 运行环境。

## 未验证范围

真实人手持续按住/中途按下松开修饰键、左右同类修饰键同时持有尚未做人机协作验收；
本次真实 HID 持有由低层 API 构造，相关中途状态变化另有离线覆盖。也未在 macOS 15、
更旧系统、Intel Mac、其他第三方输入法/事件过滤器或强制杀进程条件下运行验收。

这些限制不改变源码最低 macOS 12.3 或架构政策；12.3/15.0 的编译检查不等同于
对应系统的运行验证。CI 的 macOS 15/26 arm64 矩阵保留，本次没有推送触发远程 CI。
