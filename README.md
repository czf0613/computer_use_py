# scapkit_computer_use

跨平台桌面自动化 Python 库，提供屏幕截图、鼠标控制、键盘输入和剪贴板操作。

当前支持 macOS，Windows 支持开发中。

## 安装

需要 Python >= 3.11，使用 [uv](https://github.com/astral-sh/uv) 管理项目：

```bash
uv sync
uv run setup.py build_ext --inplace
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
# [{"id": 2, "x": 0.0, "y": 0.0, "width": 1920.0, "height": 1080.0, "scale_factor": 2.0, "is_main": True}]
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

支持的按键名称包括：`a`-`z`、`0`-`9`、`return`、`tab`、`space`、`delete`、`escape`、`f1`-`f20`、`up`/`down`/`left`/`right` 等。完整列表见 `screen_capture_kit/keys.py`。

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

# 运行测试
uv run pytest tests/test_mouse.py -v -s
```

## 许可证

MIT
