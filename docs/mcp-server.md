# 可选 MCP Server

MCP server 位于 `src/scapkit_computer_use_mcp`，使用 FastAPI 承载官方 MCP Python
SDK 的 Streamable HTTP 接口，同时提供 stdio 启动方式。基础库不依赖 FastAPI 或 MCP；
只有选择 `[mcp]` extra 时才安装服务端依赖。最低 Python 版本仍为 3.10。

MCP 在 macOS 常规 CPython 3.10+ / free-threaded 3.14+、Windows x64 常规 CPython
3.10+ 上验证。**不支持 3.13t**：
官方 MCP SDK 的 `pyjwt[crypto] → cryptography → cffi` 依赖链中，CFFI 明确拒绝
free-threaded CPython 3.13。Windows 的 pywin32 还缺少 3.14t wheel，cryptography
缺少 ARM64 wheel，因此 Windows 可选 MCP 在普通 x64 Python 上使用。基础库自身的
无 GIL 和 ARM64 检查不受影响，CI 不为可选依赖构建 OpenSSL。

MCP 服务自 `0.1.0` 起提供，`0.1.1` 增加屏幕与系统声音录制工具，
`0.1.2` 增加操作系统与命令执行约定查询工具 `system_info`。

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

从 PyPI 安装可使用 `pip install 'scapkit_computer_use[mcp]'`。
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
        print((await client.call_tool("system_info", {})).structured_content)
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
agent 应先调用 `system_info` 判断**服务端电脑**的系统，再选择命令和参数。
桌面操作遵循 `system_info → device_info → list_displays → screenshot → 操作 → screenshot 验证`。
鼠标使用平台的全局输入坐标：macOS 为逻辑 points，Windows 为虚拟桌面物理像素；
截图位置是图片局部像素，必须按返回元数据换算。服务不会向 agent 暴露 native capsule
或裸像素内存。

| 工具 | 用途 |
| --- | --- |
| `system_info` | 操作系统、版本、架构、坐标单位及 subprocess 的路径、编码、环境与 shell 示例 |
| `device_info` | 平台、能力与权限状态，不弹授权窗口 |
| `list_displays` | 显示器 ID、全局原点和尺寸（macOS points / Windows 物理像素）、缩放信息 |
| `screenshot` | 返回 JPEG 图像内容与坐标换算元数据，调用后停止截图流 |
| `start_recording` | 开始指定显示器和系统声音录制，返回录制 ID |
| `stop_recording` | 按录制 ID 停止并完成 MP4，返回文件元数据 |
| `recording_status` | 查询当前或最近一次录制的状态、ID 和设置 |
| `get_mouse_position` / `move_mouse` | 读取或移动平台的全局输入坐标 |
| `click` / `drag` | 串行完成移动加点击，或从指定起点拖动到终点 |
| `scroll` | 内容移动方向；macOS 为行，Windows 为 wheel steps |
| `press_key` | 命名按键及修饰键，按下后自动释放 |
| `type_text` | 通过剪贴板粘贴 Unicode 文本，会覆盖剪贴板 |
| `read_clipboard` / `write_clipboard` | 读取或设置剪贴板文本 |
| `run_subprocess` | 执行命令并返回状态码、两路文本和截断标记 |

macOS 缺少 ScreenCapture / Accessibility 权限时，工具会返回明确错误，由用户在
系统设置为运行服务的终端或宿主应用授权。Windows 原生后端现已注册桌面工具，
坐标使用虚拟桌面物理像素，权限和平台限制见 [Windows 后端](windows.md)。

### 查询操作系统与 subprocess 约定

`system_info` 无参数、只读，不检查桌面权限，也不启动 shell；只需执行命令的客户端
可以直接使用，无需先调用 `device_info` 或 `list_displays`。

| 返回字段 | 含义 |
| --- | --- |
| `platform` / `os_name` | macOS 返回 `Darwin` / `macOS`；Windows 均返回 `Windows` |
| `os_version` | macOS 产品版本；Windows 含 build 的系统版本；无法取得时为 `null` |
| `architecture` | 服务端 Python 运行时报告的机器架构，如 `arm64`、`AMD64`；无法取得时为 `null` |
| `desktop_supported` | 平台是否有桌面后端，不代表原生扩展已安装或权限已就绪 |
| `coordinate_unit` | macOS 为 `global display points`，Windows 为 `physical_pixel`；其他平台为 `null` |
| `subprocess.path_style` | `posix` 或 `windows`，路径属于服务端电脑 |
| `subprocess.default_cwd` | 未传 `cwd` 时使用的服务端当前工作目录 |
| `subprocess.default_encoding` | 与基础库共用解析逻辑：macOS 为 `utf-8`；Windows 为当前控制台输出代码页，无控制台时回退 OEM 代码页 |
| `subprocess.arguments_are_literal` | 始终为 `true`；管道、变量展开与重定向需显式选择 shell |
| `subprocess.inherits_server_environment` | 始终为 `false`；`env` 在干净环境初始化后新增或覆盖 |
| `subprocess.environment_source` | macOS 为系统账户的干净 login + interactive shell；Windows 为用户/系统环境块再执行 cmd AutoRun |
| `subprocess.shell_example` | 可传给 `run_subprocess` 的 shell 调用示例：macOS `/bin/zsh -c`；Windows `cmd.exe /c` |

`shell_example` 是显式 shell 的用法示例，不代表用户配置的登录 shell，也不执行命令探测。
默认编码是解码器的当前默认值，具体程序可能输出其他编码，必要时显式传入 `encoding`。
Windows 的 `.bat` / `.cmd` 文件需显式通过 cmd 执行。返回内容不包含环境变量值。

`run_subprocess` 使用基础库的文本流并并发排空 stdout/stderr，默认每路保留 20,000
字符（最高 100,000），超过后丢弃多余输出并标明截断。默认执行时限 30 秒（最高 120），
包含 shell 环境初始化；超时或取消后清理进程和管道，macOS 终止所属进程组，Windows
终止直接子进程，主动脱离进程组的进程不在服务管理范围内。MCP 结果无法传递 Python 的流对象，
因此此工具提供有界的等待模式，不提供后台进程句柄。环境来源与编码规则见
[进程执行](subprocess.md)。截图的 `timeout_seconds` 则只限定 native 启动后等待首帧的时间；
native 启动和停止使用基础库自身的超时及清理逻辑。

剪贴板操作被取消时，服务会等待已经发出的读写结束后再释放设备锁，防止延迟写入
覆盖下一次操作的内容；取消的 `type_text` 不会在写入完成后继续执行粘贴。

## 坐标与输出分辨率

**macOS 的输入坐标是逻辑 points；Windows 的输入坐标是物理像素。两平台的截图和
录屏宽高都表示输出图像像素，不能直接当成鼠标输入尺寸。**

| 返回数据 | 定义 |
| --- | --- |
| `system_info.coordinate_unit`、`device_info.coordinate_unit`、`screenshot.coordinate_unit` | macOS 为 `global display points`，Windows 为 `physical_pixel` |
| `display.x/y/width/height` | 目标显示器在全局输入坐标系中的原点和尺寸 |
| `image_width/image_height` | 本次 JPEG 的实际像素尺寸；不是客户端预览图尺寸 |
| `points_per_pixel_x/y` | 输入坐标单位 / 图片像素，分别等于 `display.width/image_width`、`display.height/image_height`；字段名为兼容保留，Windows 也使用它 |
| `scale_factor` | 当前显示模式的像素 / 输入单位；macOS 常为 2，Windows 固定为 1 |
| Windows `ui_scale_factor` | UI 缩放，如 150% 为 1.5；不用于鼠标坐标换算 |
| 录制结果 `width/height` | 视频输出像素，编码需要时右侧/底部补齐到偶数 |

截图坐标 `(image_x,image_y)` 以图片左上角为原点；鼠标需要加上目标屏幕全局原点：

```python
x = round(metadata["display"]["x"] + image_x * metadata["points_per_pixel_x"])
y = round(metadata["display"]["y"] + image_y * metadata["points_per_pixel_y"])
await client.call_tool("click", {"x": x, "y": y})
```

例如截图中 `(600,400)`：

- macOS：屏幕原点 `(-1440,0)`、逻辑尺寸 `1440×900`、截图 `2880×1800`，
  比例为 `0.5`，点击坐标为 `(-1140,200)` points。
- Windows：3840×2160 主屏即使设置 200% UI 缩放，点击坐标仍为 `(600,400)` 物理像素。
- Windows 副屏：原点 `(3840,1134)`、截图 `2560×1600`、150% UI 缩放，
  点击坐标为 `(4440,1534)`，不乘除 1.5。

若客户端把最后一张图缩成 `1280×800`，预览中的 `(300,200)` 须先还原成原图
`(600,400)`，再加屏幕原点。预览有留白或裁剪时，先扣除相应偏移。不要固定假设 Retina
倍率为 2，也不要把一个屏幕的比例用于另一屏。布局/分辨率改变后重新获取显示器和截图。

JPEG `quality` 不改变分辨率。录制宽高可能含编码补边，也不携带当前截图的完整坐标
映射；agent 根据录屏内容执行操作前应重新截图，使用这次截图的元数据。
上述定义和例子同时写入初始化 `instructions`、`computer://guide` 和 `computer_use`
prompt；各工具及其坐标参数 schema 也明确说明单位。

## 通过 MCP 录制视频

```python
started = await client.call_tool("start_recording", {
    "output_path": "/absolute/path/on/server/recording.mp4",
    # "display_id": 7,       # 省略时录制主屏幕
    "fps": 30,
    "video_quality": 0.75,
})
if started.is_error:
    raise RuntimeError(started.content)
recording_id = started.structured_content["recording_id"]
try:
    await asyncio.sleep(20)  # 期间仍可调用截图、输入等工具
finally:
    stopped = await client.call_tool("stop_recording", {"recording_id": recording_id})
if stopped.is_error:
    raise RuntimeError(stopped.content)
print(stopped.structured_content["result"])
```

文件保存在**运行服务的电脑**上，父目录必须存在，已有文件不会覆盖；工具不传输视频内容。
macOS 视频为 VideoToolbox 硬件 H.264；Windows 使用 Media Foundation，允许软件回退。
音频为系统播放声音的 AAC，不采集麦克风。
`fps` 默认 30，`video_quality` 默认 0.75；质量接受 0～1 的有限数值，显式 `null`
（Python 中为 `None`）使用平台默认策略。macOS 使用质量属性，Windows 映射到目标码率。
Windows 过载丢帧并保持真实时间轴，停止结果附带写入/丢弃帧数。
编码细节见 [录制接口](recording.md) 和 [Windows 后端](windows.md)。

每个服务同时管理一段录制，所有客户端共享状态。`start_recording` 等首帧就绪后返回；
`stop_recording` 等 MP4 收尾完成后返回，`result` 包含 `path`、`size_bytes`、
`duration_s`、`width`、`height`、`fps`。重复停止同一个 ID 会复用结果；下一段开始后
旧 ID 失效，不能误停新录制。停止不重新检查权限，便于在权限被撤回后清理资源。

`recording_status` 返回 `idle`、`recording`、`completed` 或 `failed`，以及当前/最近一次
录制的 ID 和设置；已完成时附文件结果，失败时附收尾错误。它不主动查询编码器健康状态。
HTTP 连接断开不会停止录制，可重连查询 ID 后停止；状态只保留至下一次成功开始或服务重启。
开始/停止请求取消时会等待清理，可能保存部分录制，请先查询状态再重试。HTTP 服务正常退出
和 stdio 会话正常结束时会完成仍在进行的录制；崩溃、强制终止不保证文件完整。

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
uv run --extra mcp pytest tests/test_mcp_server.py tests/test_mcp_recording.py tests/test_mcp_windows.py
```

测试使用 fake 设备和本地临时 shell profile，不截图、不移动鼠标、不操作真实剪贴板。
覆盖 MCP 发现、HTTP/stdio、图片结构、坐标、权限、请求校验、动作串行、截图取消回收、
剪贴板取消顺序、命令输出上限及进程组超时清理。CI 在原有 macOS 15 / 26 arm64 矩阵中增加可选模块测试，
可选服务覆盖常规 3.10–3.14 与 3.14t；基础库继续覆盖 3.13t。

参考：[官方 MCP SDK ASGI 集成](https://py.sdk.modelcontextprotocol.io/run/asgi/)、
[MCP 传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、
[SDK 工具 schema](https://py.sdk.modelcontextprotocol.io/servers/tools/)、
[CFFI free-threading 支持](https://cffi.readthedocs.io/en/stable/whatsnew.html)。

## 显式窗口工具与客户端归属

本分支完成冻结 Python window API 的 MCP 适配和 **fake 协议测试**，尚未接入
macOS/Windows 原生 `WINDOW_BACKEND_ABI`，窗口请求真实返回 `backend_unavailable`。
生产 profile 的验收证据白名单为空；未验证动作返回 `background_not_verified`。
这不改变旧整屏工具的全局输入、截图、剪贴板和录制语义；窗口失败不能引导 agent
调用旧 `click/type_text` 等绕过后台策略。截图成功也不代表后台操作已验证。

默认 HTTP 无状态，全部窗口工具返回结构化 `unsupported_transport`。stdio 支持
SDK 2.2 的握手和 `2026-07-28` 协议；HTTP 窗口使用有状态服务与握手客户端：

```sh
scapkit-mcp --stateful-http --host 127.0.0.1 --port 8000
```

嵌入时使用 `create_app(stateless_http=False)`；官方 SDK 客户端明确设置
`Client(streamable_http_client(url), mode="legacy")`。SDK 2.2 的 HTTP
`2026-07-28` 路径始终按单次交换运行，即使开启 stateful 也拒绝窗口工具。
`--stateful-http` 不改变 Host/Origin/Bearer 校验及单 worker 限制。

| 工具 | 显式参数与结果 |
| --- | --- |
| `window_list` / `window_open` | 可选 PID → 候选 `ref_token`；消费该 token → `session_token`，仅打开截图根 |
| `accessible_window_list` / `accessible_window_open` | 必填 PID → 独立语义根；ref token + 可选已有 `profile_id` → control-only session，无截图能力 |
| `window_attach_accessibility` | session token、语义 ref token、profile ID；Python 验证桥接，不按标题/PID 自动配对 |
| `window_capabilities` | session token → API support、identity、background validation 分别报告 |
| `window_screenshot` | session token、quality 0..100、timeout_seconds (0,30] → JPEG + 实际像素和完整局部仿射 metadata |
| `window_elements` | session token、max_depth 0..32、max_nodes 1..4096 → 无 value 的树、snapshot ID、element tokens |
| `window_element_at` | session token、snapshot ID、x/y；附 frame ID 时解释为原截图像素，否则明确为 root-local 单位；只查找不点击 |
| `window_set_value` / `window_invoke` | session token、element token；前者语义替换完整 value，后者调用精确验收动作，均无键盘/鼠标/剪贴板回退 |
| `window_status` | session token → `last_action`、busy、closed、cleanup_complete，取消后可恢复操作编号，无 native 调用 |
| `window_close` | session token → 排除新操作并等待已接收操作收尾；明确报告 cleanup_complete |

截图 `image_width/image_height` 是真实图片像素；`image_to_local=(a,b,c,d,tx,ty)`
表示 `x=a*ix+c*iy+tx, y=b*ix+d*iy+ty`。只有 `image_content_rect_px` 内的像素可映射。
`local_rect/client_rect_local` 使用局部 macOS points / Windows pixels；
`window_bounds` 为观察用全局边界。不可套用旧 display origin、`points_per_pixel_*`
或 Windows UI scale。服务端保留最新 frame 的无图片数据副本，以 `frame_id` 保留
来源并通过 `frame.to_local()` 做新鲜度校验；不能用调用方自报的几何来源替代。
control-only 目标返回 `capture_unavailable`，不伪造截图根。

所有错误以 `CallToolResult(isError=True)` 返回，text 中 JSON 与
`structuredContent` 一致，至少包含 code、operation_id、dispatch_state、retry_hint。
参数错误为 `invalid_arguments`，不会回显输入 value。`unknown` 可能已经生效，
不能重试或换 session 重试；取消不是未提交证明。`window_status` 不是效果验证，
busy 时不能据 `last_action` 发起重试。允许选择已有 profile ID，不提供 profile 注册、
证据上传、自动验收或 unsafe 开关，也不自动授权第三方应用。

归属来自 SDK 可信连接对象，而非用户传入 client ID/header：SDK 2.2 的
`ServerSession` 每请求新建，适配器通过受检查的 `_connection` / `_request_outbound`
兼容点获取连接和 transport；有状态 HTTP 把 owner 存入 Connection.state 并注册
exit_stack 清理。stdio/进程内测试连接使用独立 lifespan owner，支持现代协议每请求
新 Connection 的实际行为。未知 SDK/transport 结构拒绝操作。升级 SDK 时必须重新
执行生命周期测试，不能把请求对象当会话身份。

每 client 最多 256 refs / 8 sessions，服务最多 32 window clients / 64 sessions；
配额包括 pending open 与 cleanup 不完整的会话。refs 一次性且 60 秒到期，元素/
frame mapping 30 秒到期，session 闲置 300 秒到期，5 秒定期清理，无需下一次请求。
重复 close 在条目尚存时幂等；已成功关闭的条目可在新 open 时逐出，因此先保存回执。
更新树会使旧元素 token 失效，任何 token 都不能跨 client/window 使用。
同窗口并发操作拒绝为 `backend_busy`，close 等待，status 可读；跨窗口序列不原子。

stdio EOF、有状态 HTTP DELETE/SDK 会话超时和服务正常关闭会清理资源。
HTTP 单个请求/SSE 连接中断并非会话结束，仍由显式 close、闲置清理、DELETE 或 shutdown
回收。观察到目标进程身份失效时关闭对应窗口句柄；未观察到时由 native revalidation 和
闲置清理约束留存。强制杀进程不保证优雅清理。错误、token、标题、value 不写入适配器
普通日志；安全与兼容边界见打包的 [agent guide](../src/scapkit_computer_use_mcp/agent_guide.md)。
