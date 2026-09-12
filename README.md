# scapkit_computer_use

跨平台桌面自动化 Python 库，提供屏幕截图、鼠标控制、键盘输入和剪贴板操作。

当前支持 macOS；所用 ScreenCaptureKit API 的最低系统版本为 macOS 12.3。
官方预编译 wheel 仅提供 macOS 15+ arm64 版本，这不是源码安装的系统或架构限制。
Windows 支持开发中。

## 安装

需要 Python >= 3.10；支持 CPython 3.13/3.14 的 free-threaded 构建，通过 PyPI 安装：

```bash
pip install scapkit_computer_use
```

较早的 macOS（12.3+）或 Intel Mac 可从源码构建，需要安装 Xcode Command Line
Tools，并使用包含 ScreenCaptureKit 的 macOS SDK：

```bash
pip install --no-binary=scapkit_computer_use scapkit_computer_use
```

旧系统和 Intel Mac 的实际运行尚未验证；当前 CI 运行环境为 macOS 15 / 26 arm64。

或使用 [uv](https://github.com/astral-sh/uv)：

```bash
uv add scapkit_computer_use
```

## 权限

macOS 下需要授予以下系统权限：

- **辅助功能 (Accessibility)**：鼠标、键盘控制
- **屏幕录制 (Screen Recording)**：屏幕截图

```python
from scapkit_computer_use import check_permission, open_permission_settings

if not check_permission("Accessibility"):
    await open_permission_settings("Accessibility")
```

## 功能

### 显示器信息

所有坐标和尺寸均使用 **point**（逻辑分辨率），而非物理像素。物理像素 = point × scale_factor。API 中的所有位置参数（鼠标移动、点击等）同样使用 point 坐标。

```python
from scapkit_computer_use import list_displays

displays = list_displays()
# [{"id": 2, "x": 0, "y": 0, "width": 1920, "height": 1080, "scale_factor": 2.0, "is_main": True}]
# width/height 为 point 单位，实际物理像素为 1920×2 = 3840, 1080×2 = 2160
```

### 鼠标控制

```python
from scapkit_computer_use import (
    get_mouse_position, move_mouse, move_mouse_relative,
    mouse_click, mouse_scroll, mouse_drag
)

# 获取当前位置
pos = get_mouse_position()  # {"x": 100, "y": 200}

# 平滑移动（绝对坐标）
await move_mouse({"x": 500, "y": 300})

# 瞬间移动
await move_mouse({"x": 500, "y": 300}, smooth=False)

# 相对移动（生成 delta 事件，兼容游戏等指针锁定场景）
await move_mouse_relative({"dx": 100, "dy": 50})

# 瞬间相对移动
await move_mouse_relative({"dx": 100, "dy": 50}, smooth=False)

# 点击
await mouse_click("left")
await mouse_click("right")

# 滚动（方向为内容移动方向）
await mouse_scroll("down", 3)
await mouse_scroll("up", 3)

# 拖拽到目标位置
await mouse_drag({"x": 800, "y": 600})
```

> **注意：** `move_mouse` 使用 `CGWarpMouseCursorPosition`，不会生成鼠标移动的 delta 事件，因此不适用于依赖原始鼠标 delta 的应用（如游戏中的指针锁定）。这类场景请使用 `move_mouse_relative`，它通过 `CGEventCreateMouseEvent` 发送包含 `deltaX/deltaY` 的 `kCGEventMouseMoved` 事件。

### 键盘输入

使用跨平台的按键名称，无需关心底层键码：

```python
from scapkit_computer_use import keyboard_click, key_combo

# 按下并释放一个键
await keyboard_click("a")
await keyboard_click("return")

# 组合键
await key_combo("c", {"command"})    # Cmd+C 复制
await key_combo("v", {"command"})    # Cmd+V 粘贴
await key_combo("z", {"command", "shift"})  # Cmd+Shift+Z 重做
```

支持的按键名称包括：`a`-`z`、`0`-`9`、`return`、`tab`、`space`、`delete`、`escape`、`f1`-`f20`、`up`/`down`/`left`/`right` 等。完整列表见源码 `screen_capture_kit/keys.py`。

### 剪贴板

```python
from scapkit_computer_use import set_clipboard, get_clipboard, clipboard_paste

await set_clipboard("你好世界")
text = await get_clipboard()  # "你好世界"

# 直接粘贴到当前输入框（模拟 Cmd+V）
await clipboard_paste()
```

### 屏幕截图

基于 macOS ScreenCaptureKit，支持全分辨率 Retina 截图：

```python
from scapkit_computer_use import (
    list_displays, start_capture, stop_capture,
    current_frame_jpg, current_frame_bgra
)

displays = list_displays()
main = next(d for d in displays if d["is_main"])

# 启动截图流
handle = await start_capture(main["id"])

# 获取 JPEG 格式（可设置质量 0-100）
jpg_bytes = await current_frame_jpg(handle, quality=80)

# 获取原始 BGRA 像素数据
frame = await current_frame_bgra(handle)
# {"data": bytes, "width": 3840, "height": 2160, "bytes_per_row": 15360}

# 停止截图
await stop_capture(handle)
```

## 开发

```bash
# 构建 C 扩展
uv run setup.py build_ext --inplace

# 构建带合成测试支持的扩展，运行不操作桌面的测试
SCAPKIT_TESTING=1 uv run setup.py build_ext --inplace --force
uv run pytest tests/test_native_validation.py tests/test_native_arguments.py tests/test_native_capture.py tests/test_capture_lifecycle.py tests/test_async_safety.py
```

构建环境、并发约定和测试边界见 [开发文档](https://github.com/czf0613/computer_use_py/blob/master/docs/development.md)，
自动化发布见 [发布文档](https://github.com/czf0613/computer_use_py/blob/master/docs/releasing.md)，Codex 接手约定见 [AGENTS.md](https://github.com/czf0613/computer_use_py/blob/master/AGENTS.md)，
本次质量检查和验证边界见 [审查记录](https://github.com/czf0613/computer_use_py/blob/master/docs/code-review.md)。

`stop_capture()` 可以重复调用；停止后的新读帧返回 `None`。启动/停止超时抛出
`TimeoutError`。多线程读取和停止同一句柄受原生同步保护，但多步键鼠操作需要调用方串行安排。

## 许可证

MIT
