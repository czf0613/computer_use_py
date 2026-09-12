# 可选 MCP Server

MCP server 位于 `src/scapkit_computer_use_mcp`，使用 FastAPI 承载官方 MCP Python
SDK 的 Streamable HTTP 接口，同时提供 stdio 启动方式。基础库不依赖 FastAPI 或 MCP；
只有选择 `[mcp]` extra 时才安装服务端依赖。最低 Python 版本仍为 3.10。

MCP 模块支持常规 CPython 3.10+ 和 free-threaded 3.14+。**不支持 3.13t**：
官方 MCP SDK 的 `pyjwt[crypto] → cryptography → cffi` 依赖链中，CFFI 明确拒绝
free-threaded CPython 3.13。需要无 GIL MCP 服务时请选择 3.14t。基础库自身的 3.13t
支持和 wheel 不受影响。

本功能目前在源码中，已发布的 PyPI `0.0.3` 尚不包含它；本次没有修改版本号。

## 安装与运行

在仓库内运行：

```sh
uv sync --extra mcp
uv run --extra mcp scapkit-mcp
```

也可把当前源码装进已有虚拟环境：

```sh
pip install '.[mcp]'
scapkit-mcp
```

包含此功能的版本发布后，可使用 `pip install 'scapkit_computer_use[mcp]'`。
基础安装 `pip install scapkit_computer_use` 不会安装 MCP 依赖。

默认只监听 `127.0.0.1:8000`，一个进程控制一台设备：

| 地址 | 用途 |
| --- | --- |
| `/mcp` | MCP Streamable HTTP，客户端应连接这个地址 |
| `/` | 服务名称、传输方式及文档地址 |
| `/agent-guide` | agent 操作指南 |
| `/healthz` | 服务存活检查，不代表已获得系统权限 |
| `/docs` | FastAPI 的普通 HTTP 路由文档；MCP 工具通过 `tools/list` 发现 |

```sh
scapkit-mcp --host 127.0.0.1 --port 8765
python -m scapkit_computer_use_mcp --transport streamable-http --port 8765
```

## 配置 agent 客户端

MCP 服务不会自行注册进 agent。先在客户端添加 MCP server，然后刷新工具列表。

支持 Streamable HTTP 的客户端：选择 HTTP 传输，填入
`http://127.0.0.1:8000/mcp`。需要先启动服务；客户端若运行在另一台机器，
它的 `127.0.0.1` 并不是被控电脑，应使用 SSH 转发或配置受保护的远程访问。

支持 `mcpServers` 配置的 stdio 客户端可以直接启动服务。把下面的 Python 路径替换成
安装了 `.[mcp]` 的虚拟环境绝对路径；具体配置文件位置由客户端决定：

```json
{
  "mcpServers": {
    "computer": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["-m", "scapkit_computer_use_mcp", "--transport", "stdio"]
    }
  }
}
```

stdio 模式不启动 HTTP 监听器，日志写 stderr，stdout 专用于 MCP 协议。
不要同时为同一台设备启动多个控制服务。HTTP 服务也应使用单 worker，不开启自动 reload。

可用官方 MCP Python SDK 验证连接：

```python
import asyncio
from mcp import Client

async def main():
    async with Client("http://127.0.0.1:8000/mcp") as client:
        print(client.instructions)
        print([tool.name for tool in (await client.list_tools()).tools])
        print((await client.call_tool("device_info", {})).structured_content)

asyncio.run(main())
```

## 给 agent 的说明

同一份 [agent 操作指南](../src/scapkit_computer_use_mcp/agent_guide.md) 随 wheel 和
sdist 一起打包，且通过三种 MCP 机制暴露，避免仅依赖客户端主动打开 README：

- 初始化结果的 `instructions`：连接成功即可获得完整指南。
- `computer://guide` resource：可通过资源发现和读取接口访问。
- `computer_use` prompt：供客户端显式载入操作流程。

工具本身也包含参数 schema、范围限制、权限要求、返回值说明以及读写提示。
agent 应遵循 `device_info → list_displays → screenshot → 操作 → screenshot 验证`。
鼠标使用全局 **point**；截图使用图片局部像素，必须按返回的实际尺寸换算，支持
Retina、负坐标显示器和客户端缩放。服务不会向 agent 暴露 native capsule 或裸像素内存。

| 工具 | 用途 |
| --- | --- |
| `device_info` | 平台、能力与权限状态，不弹授权窗口 |
| `list_displays` | 显示器 ID、全局位置、point 尺寸、缩放比例 |
| `screenshot` | 返回 JPEG 图像内容与坐标换算元数据，调用后停止截图流 |
| `get_mouse_position` / `move_mouse` | 读取或移动全局 point 坐标 |
| `click` / `drag` | 串行完成移动加点击，或从指定起点拖动到终点 |
| `scroll` | 内容移动方向与滚动行数 |
| `press_key` | 命名按键及修饰键，按下后自动释放 |
| `type_text` | 通过剪贴板粘贴 Unicode 文本，会覆盖剪贴板 |
| `read_clipboard` / `write_clipboard` | 读取或设置剪贴板文本 |
| `run_subprocess` | 执行命令并返回状态码、两路文本和截断标记 |

macOS 缺少 ScreenCapture / Accessibility 权限时，工具会返回明确错误，由用户在
系统设置为运行服务的终端或宿主应用授权。Windows 目前只注册 `device_info` 和
`run_subprocess`，不向 agent 宣称尚未实现的桌面控制能力。Windows 实机行为尚未验证。

`run_subprocess` 使用基础库的文本流并并发排空 stdout/stderr，默认每路保留 20,000
字符（最高 100,000），超过后丢弃多余输出并标明截断。默认执行时限 30 秒（最高 120），
包含 shell 环境初始化；超时或取消后清理进程和管道，macOS 终止所属进程组，Windows
终止直接子进程，主动脱离进程组的进程不在服务管理范围内。MCP 结果无法传递 Python 的流对象，
因此此工具提供有界的等待模式，不提供后台进程句柄。环境来源与编码规则见
[进程执行](subprocess.md)。截图的 `timeout_seconds` 则只限定 native 启动后等待首帧的时间；
native 启动和停止使用基础库自身的超时及清理逻辑。

剪贴板操作被取消时，服务会等待已经发出的读写结束后再释放设备锁，防止延迟写入
覆盖下一次操作的内容；取消的 `type_text` 不会在写入完成后继续执行粘贴。

## HTTP 身份验证和远程访问

本机默认无 bearer token，但保留 Host / Origin 校验以防 DNS rebinding；不允许任意
网页跨域访问。配置 `SCAPKIT_MCP_TOKEN` 后，所有 HTTP 路由都要求
`Authorization: Bearer <token>`。不要把真实 token 写进仓库或直接放到命令行参数。

```sh
# 先通过进程管理器或当前 shell 设置 SCAPKIT_MCP_TOKEN。
scapkit-mcp --host 0.0.0.0 --port 8000 --allowed-host device.example:8000
```

非 loopback 监听必须同时提供 token 和 `--allowed-host`。浏览器客户端还需使用
`--allowed-origin https://agent.example` 指定准确 Origin；可以重复设置参数。
远程部署应在反向代理终止 TLS，或使用 SSH 隧道。这里提供预配置 bearer token，
不实现 OAuth 注册/登录流程；只支持 OAuth 的客户端需额外的认证网关。

## 嵌入现有 FastAPI 应用

```python
import os
from scapkit_computer_use_mcp import create_app

app = create_app(token=os.environ["SCAPKIT_MCP_TOKEN"])
```

`create_app` 返回完整 FastAPI 应用，已经在 lifespan 中管理 MCP session manager。
可将它交给 uvicorn，保持 `workers=1`。自定义宿主挂载子应用时，必须主动运行此应用的
lifespan，FastAPI 不会自动执行被挂载子应用的启动/关闭逻辑。

## 开发验证

```sh
uv run --extra mcp pytest tests/test_mcp_server.py
```

测试使用 fake 设备和本地临时 shell profile，不截图、不移动鼠标、不操作真实剪贴板。
覆盖 MCP 发现、HTTP/stdio、图片结构、坐标、权限、请求校验、动作串行、截图取消回收、
剪贴板取消顺序、命令输出上限及进程组超时清理。CI 在原有 macOS 15 / 26 arm64 矩阵中增加可选模块测试，
可选服务覆盖常规 3.10–3.14 与 3.14t；基础库继续覆盖 3.13t。

参考：[官方 MCP SDK ASGI 集成](https://py.sdk.modelcontextprotocol.io/run/asgi/)、
[MCP 传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、
[SDK 工具 schema](https://py.sdk.modelcontextprotocol.io/servers/tools/)、
[CFFI free-threading 支持](https://cffi.readthedocs.io/en/stable/whatsnew.html)。
