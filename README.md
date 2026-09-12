# scapkit_computer_use

跨平台桌面自动化 Python 库，提供屏幕截图、鼠标控制、键盘输入、剪贴板和 subprocess 操作。

当前支持 macOS；所用 ScreenCaptureKit API 的最低系统版本为 macOS 12.3。
官方预编译 wheel 仅提供 macOS 15+ arm64 版本，这不是源码安装的系统或架构限制。
Windows 目前提供纯 Python subprocess 接口（源码安装）；桌面控制扩展仍在开发中。

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

## 可选 MCP Server

源码新增了基于 FastAPI 的 MCP server，供 agent 通过标准 MCP 工具控制设备。
普通安装不引入 MCP/FastAPI 依赖。当前 PyPI `0.0.3` 尚未包含此功能，先从源码运行：

```sh
uv sync --extra mcp
uv run --extra mcp scapkit-mcp
```

客户端连接 `http://127.0.0.1:8000/mcp`；也支持通过
`python -m scapkit_computer_use_mcp --transport stdio` 由客户端直接启动。
服务自动提供 agent instructions、操作指南 resource 和 prompt，说明权限、工具使用顺序、
Retina 坐标换算及操作后的验证流程。
可选 MCP 模块支持常规 Python 3.10+ 和 3.14t；上游 CFFI 不支持 3.13t，
这不影响基础库的 3.13t 支持。

详见 [MCP 安装、客户端配置与工具说明](docs/mcp-server.md) 和
[agent 操作指南](src/scapkit_computer_use_mcp/agent_guide.md)。

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
