# scapkit_computer_use

跨平台桌面自动化 Python 库，提供屏幕截图、屏幕与系统声音录制、鼠标控制、键盘输入、剪贴板和 subprocess 操作。

支持 macOS 和 Windows；macOS 的最低系统版本为 13.0。
官方 wheel 的构建目标为 macOS 15+ arm64 和 Windows x64 / ARM64，
这不是 macOS 源码安装的系统或架构限制。Windows wheel 自 0.1.2 起纳入发布流程。
Windows x64 / ARM64 原生后端使用 Windows 系统 API 完成键鼠、
双屏截图、剪贴板及 H.264/AAC 录制，不依赖 FFmpeg 或显卡厂商 SDK。
桌面实测环境为 Windows 11 x64；ARM64 已通过原生编译、合成测试和 wheel 构建 CI，
Windows 10 和 ARM64 的桌面交互仍需对应设备验收。安装与差异见 [Windows 后端](docs/windows.md)。

## 安装

需要 Python >= 3.10；支持 CPython 3.13/3.14 的 free-threaded 构建，通过 PyPI 安装：

```bash
pip install scapkit_computer_use
```

较早的 macOS（13.0+）或 Intel Mac 可从源码构建，需要安装 Xcode Command Line
Tools，并使用包含 ScreenCaptureKit 的 macOS SDK：

```bash
pip install --no-binary=scapkit_computer_use scapkit_computer_use
```

旧系统和 Intel Mac 的实际运行尚未验证；当前 CI 运行环境为 macOS 15 / 26 arm64。

或使用 [uv](https://github.com/astral-sh/uv)：

```bash
uv add scapkit_computer_use
```

## 可选 MCP Server

提供基于 FastAPI 的 MCP server，供 agent 通过标准 MCP 工具控制设备。
普通安装不引入 MCP/FastAPI 依赖。MCP 服务自 `0.1.0` 起提供，也可从源码运行：

```sh
uv sync --extra mcp
uv run --extra mcp scapkit-mcp
```

客户端连接 `http://127.0.0.1:8000/mcp`；也支持通过
`python -m scapkit_computer_use_mcp --transport stdio` 由客户端直接启动。
调用方先使用 `system_info` 查询服务端操作系统、坐标单位和 subprocess 的路径、编码及 shell 用法。
MCP 也提供 `start_recording`、`stop_recording`、`recording_status`，将视频保存到服务端路径。
服务自动提供 agent instructions、操作指南 resource 和 prompt，说明权限、工具使用顺序、
macOS 逻辑坐标、Windows 物理坐标、截图像素换算及操作后的验证流程。
可选 MCP 模块支持常规 Python 3.10+；macOS 也测试 3.14t。上游 CFFI 不支持
3.13t，Windows MCP 的 pywin32 依赖缺少 3.14t 安装包、cryptography 缺少 ARM64 wheel，
因此 Windows MCP 在普通 x64 Python 上验证。这不影响基础库的无 GIL 和 ARM64 支持。

详见 [MCP 安装、客户端配置与工具说明](docs/mcp-server.md) 和
[agent 操作指南](src/scapkit_computer_use_mcp/agent_guide.md)。

## 权限

macOS 下需要授予以下系统权限：

- **辅助功能 (Accessibility)**：鼠标、键盘控制
- **屏幕录制 (Screen Recording)**：屏幕截图、屏幕与系统声音录制；不会采集麦克风

```python
from scapkit_computer_use import check_permission, open_permission_settings

if not check_permission("Accessibility"):
    await open_permission_settings("Accessibility")
```

## 功能

### 显示器信息

**显示器几何与鼠标输入使用同一套平台坐标，截图和录屏尺寸则始终是图像像素。**

| 数据 | macOS | Windows |
| --- | --- | --- |
| `list_displays()` 的 `x/y/width/height` | 全局逻辑 points | 虚拟桌面物理像素 |
| 鼠标位置、绝对移动、点击与拖拽位置 | 全局逻辑 points | 虚拟桌面物理像素 |
| JPEG / BGRA 的宽高、录制结果的宽高 | 输出图像像素 | 输出图像像素 |
| `scale_factor` | 当前显示模式的像素 / point 比例 | `1.0` |
| `ui_scale_factor` | 不提供此字段 | UI 缩放，如 150% 为 `1.5`；不用于鼠标坐标换算 |

两平台均以屏幕左上角为局部原点，向右/向下递增；全局原点由显示器布局决定，副屏可有
负坐标。macOS 的鼠标坐标不要乘 Retina 倍率；Windows 不要乘或除系统的 150% / 200%
UI 缩放。多屏必须使用目标显示器自己的原点和截图尺寸。

```python
from scapkit_computer_use import list_displays

displays = list_displays()
# macOS 示例：逻辑尺寸 1920×1080 points，scale_factor=2，截图为 3840×2160 像素
# Windows 示例：3840×2160 屏幕设置 200% UI 缩放时，width/height 仍为 3840/2160，
# scale_factor=1.0，ui_scale_factor=2.0；鼠标位置也使用物理像素
```

从截图中的局部像素位置转换为鼠标全局输入坐标：

```python
x = round(display["x"] + image_x * display["width"] / image_width)
y = round(display["y"] + image_y * display["height"] / image_height)
```

`image_width/image_height` 必须是实际返回图片的像素尺寸。若客户端把图片缩小展示，先把
观察到的位置还原到原图像素。MCP 已返回 `coordinate_unit`、`image_width/image_height`
和 `points_per_pixel_x/y`；后两个比例字段沿用兼容命名，在 Windows 上也表示
**输入坐标单位 / 图片像素**。完整例子见 [MCP 坐标说明](docs/mcp-server.md#坐标与输出分辨率)。

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

> **macOS 注意：** `move_mouse` 使用 `CGWarpMouseCursorPosition`，不会生成鼠标移动的 delta 事件，因此不适用于依赖原始鼠标 delta 的应用（如游戏中的指针锁定）。这类场景请使用 `move_mouse_relative`，它通过 `CGEventCreateMouseEvent` 发送包含 `deltaX/deltaY` 的 `kCGEventMouseMoved` 事件。

### 键盘输入

使用跨平台的按键名称，无需关心底层键码：

```python
from scapkit_computer_use import keyboard_click, key_combo

# 按下并释放一个键
await keyboard_click("a")
await keyboard_click("return")

# macOS 组合键；Windows 的复制/粘贴使用 {"control"}，不是 {"command"}
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

# 直接粘贴到当前输入框（macOS Cmd+V；Windows Ctrl+V）
await clipboard_paste()
```

### 屏幕截图

macOS 使用 ScreenCaptureKit，Windows 使用 Windows Graphics Capture。JPEG 与 BGRA
均返回当前采集画面的像素尺寸，不因逻辑坐标或 UI 缩放而缩小；JPEG `quality` 只控制
压缩质量，不改变分辨率。以实际图片/BGRA 的宽高为准，不用逻辑尺寸或面板标称尺寸代替。

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

### 屏幕与系统声音录制

指定显示器和保存路径，输出 QuickTime 兼容的 H.264/AAC MP4。录制结果的
`width/height` 是视频像素尺寸，不是鼠标坐标尺寸；编码需要时在右侧/底部补齐到偶数。

```python
from scapkit_computer_use import start_recording, stop_recording

handle = await start_recording(display_id, "/path/to/recording.mp4", fps=30)
try:
    await do_something()
finally:
    result = await stop_recording(handle)
print(result.path, result.size_bytes, result.duration_s)
```

默认目标帧率为 30 fps，支持指定整数帧率；画面静止时继续使用缓存帧，停止时补齐最后一个
帧区间。只录制系统播放声音，不采集麦克风。macOS 的 VideoToolbox 要求硬件 H.264 编码；
Windows 的 Media Foundation 优先硬件、允许软件回退，过载时丢帧并保留真实时间轴。
默认 `video_quality=0.75`，macOS 映射为编码器质量，Windows 映射为目标码率；
`None` 使用平台默认策略。输出父目录必须存在，已有文件不会被覆盖。
详见 [录制接口、生命周期与异常说明](docs/recording.md)。

### 执行 subprocess

`run_subprocess()` 是异步函数。可执行文件与参数分开传入；执行 shell 命令时，
把 shell 作为可执行文件，并传入 `-c` 或 `/c` 等参数。

```python
import sys
from scapkit_computer_use import run_subprocess

result = await run_subprocess(
    sys.executable,
    ["-c", "import os; print(os.getenv('EXAMPLE'))"],
    cwd="/path/to/workdir",
    env={"EXAMPLE": "你好"},
)
print(result.returncode, result.stdout, result.stderr)
# 也可以解包：returncode, stdout, stderr = result

result = await run_subprocess("/bin/zsh", ["-c", "printf '%s' hello"])
# Windows 示例：await run_subprocess("cmd.exe", ["/c", "echo hello"])
```

| 参数 | 含义 |
| --- | --- |
| `executable` | 可执行文件名称或路径；名称通过加载后的环境 PATH 查找 |
| `args=()` | 参数序列，保留空格和特殊字符；不会自动拼接成 shell 命令 |
| `cwd=None` | 工作目录，默认当前工作目录 |
| `env=None` | 在 shell 环境上新增或覆盖的变量，均为字符串 |
| `use_stream=False` | 默认等待退出，返回退出码和两路文本；为 True 时返回运行中的 `SubprocessStream` |
| `encoding=None` | macOS 默认 UTF-8；Windows 默认控制台输出代码页，无控制台时用系统 OEM 代码页 |
| `errors="strict"` | 解码错误默认抛异常；可指定 `"replace"` 保留其他可解码内容 |

环境来自重新加载的系统/用户 shell 配置，**不继承当前 Python 进程的环境变量**。
macOS 读取账户登录 shell 的 login/interactive 配置；Windows 从系统和用户配置构造环境，
再执行 cmd AutoRun。调用方传入的 `env` 最后合并。详情见 [进程执行说明](https://github.com/czf0613/computer_use_py/blob/master/docs/subprocess.md)。

流式 stdin 接收字符串，stdout/stderr 支持异步 `read()`、`readline()` 和逐行迭代：

```python
import asyncio

process = await run_subprocess(
    sys.executable, ["-u", "-c", "import sys; print(input()); print('done', file=sys.stderr)"],
    use_stream=True,
)

async def feed():
    process.stdin.write("你好\n")
    await process.stdin.drain()
    process.stdin.close()

async def consume(stream):
    async for line in stream:
        print(line, end="")

await asyncio.gather(feed(), consume(process.stdout), consume(process.stderr))
returncode = await process.wait()
# 或：stdout, stderr = await process.communicate("你好\n")
```

Windows 程序可能输出 UTF-8、GBK、ANSI 或 UTF-16；无法通用地自动判断。
例如使用 `encoding="utf-8"`、`encoding="gbk"` 或 `encoding="utf-16-le"` 明确指定。
同一编码用于该进程的三路文本流；跨块中文由增量解码器处理，换行统一为 `\n`。

## 开发

```bash
# 构建 C 扩展
uv run setup.py build_ext --inplace

# 构建带合成测试支持的扩展，运行不操作桌面的测试
SCAPKIT_TESTING=1 uv run setup.py build_ext --inplace --force
uv run pytest tests/test_native_validation.py tests/test_native_arguments.py tests/test_native_capture.py tests/test_capture_lifecycle.py tests/test_async_safety.py tests/test_process.py
```

构建环境、并发约定和测试边界见 [开发文档](https://github.com/czf0613/computer_use_py/blob/master/docs/development.md)，
自动化发布见 [发布文档](https://github.com/czf0613/computer_use_py/blob/master/docs/releasing.md)，Codex 接手约定见 [AGENTS.md](https://github.com/czf0613/computer_use_py/blob/master/AGENTS.md)，
本次质量检查和验证边界见 [审查记录](https://github.com/czf0613/computer_use_py/blob/master/docs/code-review.md)。

`stop_capture()` 可以重复调用；停止后的新读帧返回 `None`。启动/停止超时抛出
`TimeoutError`。多线程读取和停止同一句柄受原生同步保护，但多步键鼠操作需要调用方串行安排。

## 许可证

MIT
