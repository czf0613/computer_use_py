# macOS 修饰键生命周期

## 根因和实测证据

0.0.3 的 `key_combo` 和 `clipboard_paste` 都使用 `keyboard_click`：在默认
事件源上发送普通键 down/up，两个事件携带相同修饰键 flags，缺少修饰键自身的
释放序列。后续鼠标/滚动事件从默认事件源创建，会继承残留状态。

2026-09-12 在隔离的 `v0.0.3` 源码构建中，组合键和单独粘贴分别复现：

| 时点 | combined / HID flags | 实际焦点 |
| --- | --- | --- |
| 开始 | `0x20000000` | A |
| Cmd+A 或单独粘贴返回 | `0x20100000` | A |
| 等待 100 ms，不发送输入 | `0x20100000` | A |
| 点击 B 标题栏 | `0x20100000` | 仍为 A |

两个独立 AppKit 窗口同时记录真实文本、选择范围、事件和 key window。
测试直接导入指定源码及其 native 扩展，不经过 MCP，不修改 AIOS 或其环境。
判断采用四种修饰键掩码 `0x001e0000`，不能要求整个 flags 等于零。

真机还排除了两个不充分的修复假设：

- **Private source 不能独自隔离全局 flags。** 私有源在 HID tap 投递时仍会
  改写 HID 状态，清掉此前通过低层 API 持有的 Shift；系统不是把所有来源简单求并集。
- **CGEventPost 返回不代表释放已处理。** 即使完整发布了释放序列，立即读取
  仍可能看到 Command，随后只读采样才恢复。后续点击不能依赖任意固定延时。

SDK 说明参考：[CGEventSource](https://developer.apple.com/documentation/coregraphics/cgeventsource)、
[键盘事件构造器](https://developer.apple.com/documentation/coregraphics/cgevent/init(keyboardeventsource:virtualkey:keydown:))、
[事件计数器](https://developer.apple.com/documentation/coregraphics/cgeventsource/counterforeventtype(_:eventtype:))。
上述 HID/焦点因果关系来自本机实测，不把 API 文档当作跨版本实测证据。

## 高层操作的实现

`keyboard_click` / `key_combo` / `clipboard_paste` 每次创建 private source，
为本次引入的修饰键和主键预分配完整 down/up 事件，按相反顺序释放。
主键 key-up 仍携带当时持有的修饰键；不是简单把它的 flags 改成零。

事件在 **session tap** 发布，不把本次合成修饰键写入 HID 表。每条事件发布时
重新读取当前 HID flags 并合并，保留物理键和显式低层 HID 持有；清理不恢复
过时的开始快照。已存在的修饰键持有不会再由高层操作按下/释放。

内部 capsule 管理以下生命周期：

- 首次发布前完成全部 CGEvent、CGEventSource、capsule 分配；分配失败不产生输入。
- 结束函数在 mutex 下只发布一次释放序列，并等待自己 source 的 keyDown、keyUp、
  flagsChanged 计数达到预期；检查所有类型，避免只确认最后的 flagsChanged 而漏掉主键释放。
- 最长等待一秒；无法确认处理则抛出 `TimeoutError`，不重新播放输入，也不清空全局状态。
  计数证明 WindowServer 对该来源的处理，不证明目标应用已消费每条事件。
- Python 在 `finally` 清理，确认等待放在线程池，不阻塞 asyncio 事件循环。
  重复取消也等待清理结束；已有取消/异常在清理失败时保留，清理错误通过异常链呈现。
- 结束后立即释放 native 引用，capsule 本身的地址保持有效直至析构。
  未显式结束的 capsule 析构会尝试补清理；强制杀进程无法保证清理。

不发送 Escape、空白点击或额外普通键来恢复状态。

## 显式低层操作与并发

`keyboard_click_action` / native `keyboard_click` 仍是单事件 HID API，flags
完全由调用方决定，调用方拥有完整的按下/释放生命周期。例如：

```python
keyboard_click_action("command", "down", {"command"})
try:
    # 后续低层事件需要继续明确携带希望持有的 flags。
    ...
finally:
    keyboard_click_action("command", "up")
```

高层快捷键保留当前 HID 持有，不替调用方结束它。其他应用在 session 层合成的
任意状态不属于可识别的物理持有。私有源保证本库事件和内存所有权，不保证多个
并发桌面动作的语义隔离；调用方必须串行执行多步键盘、鼠标操作。MCP server
通过同一设备锁串行化调用。

硬件输入到 HID 采样和实际投递之间仍存在时序窗口，无法承诺与人类同时操作
原子化。左右同类修饰键同时持有、第三方事件过滤器等需要单独验收。

## 拖动验收发现的原有问题

独立跨屏测试在 0.0.3 同样失败：`mouse_drag` 按住鼠标后调用
`CGWarpMouseCursorPosition`，光标到达另一屏，但 AppKit 没收到正常拖动事件，
窗口没有随之跨屏。此时四种修饰键均为零，与 Command 残留是两个问题。

`mouse_drag` 现在插值发送 `kCGEventLeftMouseDragged`，携带全局 points 坐标和
移动 delta，仍在 `finally` 释放自己按下的鼠标按钮。普通 `move_mouse` 的光标
移动语义保持不变。验收核对实际窗口位置、所属屏幕、焦点和文本，而不只核对光标。

## 回归测试

`tests/test_modifier_lifecycle.py` 在临时目录编译真实 `control.c`，使用真实
CGEvent 对象，最终 post/warp 进入离线接收器。模型根据实测区分 session/HID
状态替换，覆盖延迟/缺失确认、后续点击/输入/滚动/拖动、四种修饰键、异常、取消、
显式持有、分配失败、native 引用释放、重复/并发关闭。接收器不向桌面发送输入。
它记录已投递 down/up 的平衡，不因释放 source 引用而掩盖未释放按键。

```sh
SCAPKIT_TESTING=1 uv run --no-sync setup.py build_ext --inplace --force
uv run --no-sync pytest tests/test_modifier_lifecycle.py tests/test_async_safety.py
```

离线模型不是 WindowServer。真实验收另用 [独立测试窗口](../tests/manual/README.md)，
运行前必须已有用户授权；测试报告区分系统状态、应用效果和未覆盖场景。
本次真机记录见 [2026-09-12 验收](2026-09-12-modifier-validation.md)。
