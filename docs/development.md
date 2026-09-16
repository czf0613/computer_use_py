# 开发与验证

库最低支持 macOS 13.0，源码构建默认使用此部署目标，
不限制 CPU 架构。官方 macOS wheel 为 macOS 15+ arm64，CI 运行于 macOS
15 / 26 arm64；0.1.2 发布流程另增 Windows x64/ARM64 wheels。这是发行与验证范围，
不是库本身的系统或架构限制。旧系统和 Intel
Mac 的实际运行尚未验证。Windows 原生扩展构建与验证参见 [Windows 后端](windows.md)。
最低 Python 版本为 3.10，
free-threaded ABI 从 CPython 3.13 开始单独构建。3.10 普通解释器本身仍有 GIL。

## 本地构建

安装 Xcode Command Line Tools 和 uv，然后运行：

```sh
uv sync
uv run setup.py build_ext --inplace --force
```

`.python-version` 固定最低支持版本，`setup.py` 是实际构建入口；CMake 文件只服务于
IDE。编译使用 ARC，并链接所用 Apple frameworks。`MACOSX_DEPLOYMENT_TARGET`
默认为 `13.0`；可通过环境变量选择更高的部署目标，编译和链接均显式传入该值。
架构跟随 Python/编译工具链，也可通过 `ARCHFLAGS` 指定；项目不强制 arm64。
官方 wheel 的发布 workflow 单独指定 `MACOSX_DEPLOYMENT_TARGET=15.0` 和
`ARCHFLAGS='-arch arm64'`。部署目标不代表已经在对应版本的 macOS 上运行验证。

## 自动化测试

```sh
SCAPKIT_TESTING=1 uv run setup.py build_ext --inplace --force
uv run pytest tests/test_native_validation.py tests/test_native_arguments.py tests/test_native_capture.py tests/test_capture_lifecycle.py tests/test_async_safety.py tests/test_process.py
uv run pytest tests/test_recording.py tests/test_recording_integration.py tests/test_native_recording.py tests/test_recording_writer.py
```

合成测试在内存中创建 8×8 BGRA `CVPixelBuffer`，覆盖 BGRA/JPEG 输出、重复停止、
停止后回调、并发更新/读取/停止和 capsule 析构；假 stream 模拟停止失败和
超时后迟到回调，并核对 frame/stream/delegate/回调持有者释放计数。输入测试使用非法参数或 mock，
不移动鼠标、不点击、不操作剪贴板、不读取屏幕。测试 hooks 在正常构建中不可见。
改变 ABI 或 `SCAPKIT_TESTING` 后必须加 `--force` 重新编译。

单独验证进程接口可运行 `uv run pytest tests/test_process.py`，不需要加载原生扩展。
这些测试使用临时 zsh profile 和本地子进程，覆盖文本流、环境隔离、编码、取消回收，
以及 Windows API 的模拟检查。接口和平台限制见 [进程执行](subprocess.md)。

修饰键生命周期回归使用 `uv run --no-sync pytest tests/test_modifier_lifecycle.py`。
它编译实际 C 事件构造代码并截留 post/warp，不操作桌面；真实系统状态和窗口焦点
的验收需要另行准备测试环境。参见 [修饰键生命周期](modifier-lifecycle.md)。

可选 MCP 模块通过 `uv run --extra mcp pytest tests/test_mcp_server.py tests/test_mcp_recording.py` 验证。
使用 fake 设备测试协议与操作逻辑，不访问真实桌面。安装和 agent 配置见
[MCP server](mcp-server.md)。

真实桌面测试分布在 `test_capture.py`、`test_mouse.py`、`test_keyboard.py`、
`test_list_displays.py`，需要单独授权；截图测试会在 `data/` 保存实际屏幕图像。
不能把这些测试加入无人值守 CI，也不能用全套测试代替上述安全测试集。

录制测试使用合成像素和 PCM：`test_recording_writer.py` 编译独立原生 probe，验证真实
硬件 H.264、AAC、AVAssetWriter 与 AVAssetReader；`test_native_recording.py` 用测试
入口绕过所有显示器查询和 SCStream 创建，覆盖固定帧时钟与停止状态机；Python 测试
验证参数、异步取消和迟到资源清理。本地存在 ffprobe 时增加容器检查。
实际 ScreenCaptureKit 交付与 QuickTime 播放必须在用户准备好后单独验收，不能从合成
测试推断已验证。操作说明见 [录制手工验收](../tests/manual/recording.md)。

## 录制原生边界

`recording.m` 管理 stream、最新/待提交像素帧与 host-clock 输出时钟。capture queue
串行处理输入，NSCondition 同步启动和停止结果；capsule 的 Objective-C 指针在析构前
始终不变。delegate 使用 weak owner，停止完成或超时后断开输出。结束只执行一次，
并发停止等待同一结果；析构发起 abort，不发布文件。

`recording_writer.m` 独立管理硬件 VideoToolbox session、AAC 和 MP4 临时文件。
提交/结束/取消从调用方串行调用，内部编码回调与写入队列独立。停止排空时不持有
Python thread state、capture queue 或 condition，避免回调死锁。
正常提交最多允许八帧在途，为启动和短暂性能波动留出余量，槽位覆盖压缩输出写入完成；
只在最终停止时调用
`VTCompressionSessionCompleteFrames`，避免逐帧同步等待阻塞采集并重复旧画面。
音频静音补齐跟随已完成视频时间。可选 `video_quality` 控制质量，由编码器分配码率；
指定时检查硬件编码器支持情况。异步回调通过弱 owner 的完成上下文访问 writer，
取消在编码器失效及队列排空后恢复未返回的槽位，允许最终引用在输出队列释放。
新增 API 和类型见 [录制说明](recording.md)。

## 截图生命周期与并发约定

Python capsule 独占持有 native handle；只有 capsule 析构才释放结构体。
`stop_capture()` 在 capture queue 上标记停止、断开 delegate 的 back-pointer、
取出 stream 并清空缓存，然后请求系统停止。重复停止是安全的。停止后新读帧返回
`None`；已经 retain 的在途读帧可以完成并返回最后一帧。

ScreenCaptureKit 在同一串行 queue 交付 frame，只有完整帧会替换缓存。读取先在
queue 上 retain 快照，再离开 queue 编码/复制，避免编码阻塞新帧回调。
独立 reader mutex 串行化同一 handle 的 CPU pixel-buffer lock/unlock。
缓存释放不会使在途快照失效。Python bytes/字典分配全部放在原生锁外，允许 GC 回调
安全地重入读帧。等待 queue、mutex、异步 completion 和 JPEG 编码
期间释放 Python thread state，兼容常规 GIL 和 free-threaded 解释器。

停止完成超时会抛出 `TimeoutError`，但 handle 保持关闭。启动超时会断开 delegate，
延迟到达的启动 completion 负责停止 stream。completion 不持有 raw handle。
析构发起异步停止，不等待系统 completion；操作系统完全停流可能晚于对象回收。
ARC 强引用字段在 `free` 前显式置空，临时 Objective-C 对象在 autorelease pool 中回收。

线程安全保证限定于 native 对象的内存/生命周期。多个协程或线程交错执行
“按下、等待、松开”不构成原子桌面操作，调用方应串行安排输入动作。

## Python 3.10 与 no-GIL

`match`、内置泛型、`X | None` 和 `asyncio.to_thread` 均可用于 Python 3.10。
含 `#` 的 Python C API 格式在 3.10 上需要 `PY_SSIZE_T_CLEAN`；构建统一定义它。
free-threaded 构建通过条件编译声明 `Py_MOD_GIL_NOT_USED`，同时使用原生同步原语，
而不是仅声明后继续依赖 GIL。

可使用独立虚拟环境验证 ABI，避免覆盖日常开发环境：

```sh
uv venv --python 3.14t /tmp/scapkit-314t
SCAPKIT_TESTING=1 uv pip install --python /tmp/scapkit-314t/bin/python -e . pytest pytest-asyncio
```

本项目开发依赖由 dependency group 管理；独立环境可显式安装 `pytest pytest-asyncio`
后运行上述指定测试文件。请参照 CI 的实际构建命令，不要将不带 `t` 的 wheel 用于
free-threaded Python。

参考：[CPython free-threaded C API 指南](https://docs.python.org/3.14/howto/free-threading-extensions.html)、
[Apple ScreenCaptureKit 示例](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)。
