# Windows 原生后端

Windows 后端提供键鼠、显示器枚举、剪贴板、BGRA/JPEG 截图及屏幕/系统声音
H.264/AAC MP4 录制。运行库只使用 Windows 系统接口；不依赖 FFmpeg 或显卡厂商 SDK。
目标为 Windows 10 22H2 / Windows 11 23H2 及以后的 x64、ARM64，
实际设备验证范围见文末。

0.1.2 发布流程从统一 sdist 构建 Windows x64 / ARM64 wheels，每种架构覆盖
CPython 3.10–3.14 和 3.13t/3.14t；在同架构 runner 上安装并验证导入、ABI、PE 架构与
无 GIL 状态。可通过手动预检先生成 artifact，正式 PyPI 上传由 Release 发布事件触发。
流程见 [CI 与 PyPI 发布](releasing.md)；构建/导入检查不等于真实桌面验收。

## 构建

需要 CPython 3.10+、uv、MSVC C++ 工具链和 Windows SDK（本机使用 10.0.26100.0）。
Windows 原生实现使用 C++20/C++/WinRT，继续由 setuptools 构建。

```powershell
uv sync --frozen
uv run --no-sync setup.py build_ext --inplace --force
```

扩展仍使用既有私有路径 `screen_capture_kit._scapkit`，平台决定编译 macOS 或 Windows
实现，Python 导入路径和公开函数保持兼容。基础包惰性加载桌面扩展，subprocess 不依赖它。
Media Foundation 延迟加载；缺少系统媒体组件时，录制给出能力错误。

CPython 3.13t/3.14t 使用单独 ABI，构建根据 `sysconfig` 显式设置 `Py_GIL_DISABLED`。
切换 ABI、编译选项或测试 hooks 后必须强制重建。Windows MCP 的上游 pywin32 目前没有
3.14t 安装包；使用普通 Python 运行 MCP，基础库仍可在无 GIL 解释器中运行。

Windows ARM64 的可选 MCP 依赖 `cryptography==50.0.1` 也没有预编译 wheel。
CI 在 x64 普通 Python 上验证 MCP，ARM64 保留基础库、无 GIL 和生产 wheel 检查；
ARM64 MCP 暂未验收。OpenSSL 不是基础库依赖，CI 不从源码构建它，并禁止
`cryptography` 回退到源码构建。基础库的视频编解码使用系统 Media Foundation。

## 坐标和输入

- Windows 全局输入坐标均为**虚拟桌面物理像素**，可以为负数。macOS 仍为 points。
- `list_displays()` 的 `x/y/width/height` 与鼠标 API 使用同一单位。
- Windows `scale_factor=1.0`；`ui_scale_factor` 单独返回 UI 缩放，如 1.5 或 2.0。
- 原尺寸截图中的点 `(image_x,image_y)` 加上显示器原点即可输入；缩放图片须先还原
  实际图像尺寸。MCP 的 `points_per_pixel_x/y` 保留兼容命名，代表输入单位/图片像素。
- `display_id` 是进程内 ID，不是 HMONITOR 指针，也不是可跨进程保存的设备标识。
  屏幕拓扑改变后重新枚举，不复用长期保存的 ID。
- 跨屏平滑路径经过实际显示器之间的可见接缝；不能把虚拟桌面外接矩形中的空白区
  当作合法目标。原生绝对事件带 `MOUSEEVENTF_VIRTUALDESK`。

例如主屏 3840×2160 / 200% 时，鼠标 `(600,400)` 仍是物理像素 `(600,400)`。
副屏 2560×1600 / 150%、原点 `(3840,1134)` 时，该屏截图中的 `(600,400)` 对应
全局 `(4440,1534)`，不再乘除 1.5。MCP 的通用公式为
`x = round(display.x + image_x * points_per_pixel_x)`，y 同理；客户端缩图须先还原
到返回的 `image_width/image_height`。详细定义见 [MCP 坐标说明](mcp-server.md#坐标与输出分辨率)。

鼠标拖拽使用真实 SendInput down/move/up，取消后释放按钮。Windows 输入受 UIPI、
当前交互会话和用户并发操作影响；返回成功表示事件已提交，不代表应用已经完成操作。
不支持控制锁屏/UAC secure desktop，也不会自动提权。

`scroll` 的方向始终描述**内容移动方向**。Windows `up` 对应负的纵向 wheel delta，
`down` 对应正值；横向 `left` 为正值、`right` 为负值。Windows distance 为 wheel
steps，系统/应用设置决定每步滚多少行，因此不承诺与 macOS 的行数精确相同。

`control` 为 Ctrl，`win/command` 为 Windows 键，`option/alt` 为 Alt；粘贴使用 Ctrl+V。
通用 `fn` 无法注入。标点 VK 按当前键盘布局解释，Unicode 文本请使用剪贴板粘贴。
低层 `keyboard_click_action` 不隐式按下 modifiers；非空 modifiers 要求调用者已按住
这些键。高层 `key_combo` 才管理成对按下/释放。合成事件标记不提供物理键状态隔离。

## 截图与生命周期

每个 capture handle 用 WGC 采集一个显示器；允许同时持有多个 handle。
frame pool 有两个 buffer，回调复制到自有 D3D11 纹理后发布最新帧，读者保留独立引用。
截图读回使用实际 stride，返回 bytes 与 handle 生命周期独立；JPEG 通过 WIC 编码。

stop 幂等，之后读取返回 None；已开始的读取允许完成。帧到达回调只运行原生代码，
GPU context 有独立同步，等待时不持有 Python thread state。句柄析构会停止和清理。
显示器关闭或尺寸变化可能要求重新启动 capture/recording；录制不会悄悄改换显示器。
当前输出为 SDR，HDR 色彩保真、休眠恢复、多 GPU 热插拔尚未完成专项验收。

## 录制、软件回退和丢帧

```python
import asyncio
import scapkit_computer_use as computer

async def main():
    display = next(d for d in computer.list_displays() if d["is_main"])
    handle = await computer.start_recording(display["id"], "new-recording.mp4", fps=30)
    try:
        await asyncio.sleep(5)
    finally:
        result = await computer.stop_recording(handle)
    print(result.path, result.frames_written, result.frames_dropped)

asyncio.run(main())
```

Windows 用 Media Foundation Sink Writer，建议使用硬件 transform；初始化不兼容时
再次尝试软件配置。不承诺某次录制一定使用硬件。GPU video processor 优先执行 BGRA→NV12，
不可用时使用 CPU 转换，随后读回紧凑 NV12 交给 MF。该实现仍有 GPU→CPU 数据复制，
不称为全程零拷贝。MF 负责 H.264/AAC 编码及 MP4 封装。

`video_quality` 在 Windows 映射为目标码率，默认 0.75，None 使用同一默认策略：

```text
bitrate = clamp(width * height * min(fps,60) * (0.03 + 0.17 * quality),
                128000, 100000000)  # bits/s
```

它与 VideoToolbox 的 Quality 不是相同算法；不承诺跨平台同值同画质。
奇数尺寸补齐到偶数。`fps` 是目标采样频率；实际能力受驱动/分辨率/编码器限制。

过载策略：

1. WGC 只缓存最新画面；编码线程仅持有当前待提交样本和下一样本，不建立无界原始帧队列。
2. 保留 Sink Writer 的默认阻塞节流。编码线程恢复时跳到当前时钟对应的帧槽，跳过过期帧。
3. 上一帧 duration 延伸到下一帧的真实时间，最后一帧截到停止时刻。丢帧不使播放加速。
4. 返回 `frames_written` / `frames_dropped`；正常负载和静止画面仍按目标 fps 提交。
5. 音频不随视频丢弃；音频队列最多缓存五秒。编码器阻塞超过可承受范围时明确失败，
   防止无限积压。音频写入保留 100 ms 的包到达余量，停止时排空尾部数据，再补齐
   没有 packet 的静音时间；这不改变输出时间戳。

WASAPI 采集启动时默认播放端点的系统混音，固定该端点，不采集麦克风。应用被路由到
其他设备的声音不会包含在内；默认端点改变不会自动切换已有录制。音频为 48 kHz 双声道。
视频与音频使用共同 QPC 基准；音频 packet 的 QPC 值按 Windows API 的 100 ns 单位处理。

文件先写入同目录唯一临时文件，finalize 成功且文件关闭后，以不覆盖方式发布为目标。
容器始终是 MP4，与扩展名无关。启动取消会回收迟到句柄；停止取消仍先完成收尾。
同一 handle 并发停止共享结果。丢弃句柄只清理临时文件，不发布成功结果。
驱动永久阻塞、强制结束进程或断电不保证及时完成清理。

## 验证

默认合成测试不采集桌面、不发输入、不修改剪贴板：

```powershell
$env:SCAPKIT_TESTING='1'
uv run --no-sync setup.py build_ext --inplace --force
uv run --no-sync pytest tests/test_windows_backend.py tests/test_async_safety.py tests/test_recording.py
uv run --no-sync pytest tests/test_mcp_server.py tests/test_mcp_recording.py
```

真实桌面测试必须得到用户授权并准备环境；以下命令会移动专用测试窗口、暂时修改
剪贴板文本（之后恢复文本）、截图及录制默认系统声音：

```powershell
uv run --no-sync python tests/manual/verify_windows.py --desktop --round-trips 20
# 可选：播放一秒测试音，检查录制的声音不是静音
uv run --no-sync python tests/manual/verify_windows.py --desktop --round-trips 0 --audio-tone
# 只补测基本输入与截图；按输出报告的文件名前缀保存每屏 JPG / 原始 BGRA
uv run --no-sync python tests/manual/verify_windows.py --desktop --round-trips 0 --skip-recording --output build/windows_research/basic-acceptance.json
```

本机验证：Windows 11 build 26200 / x64 / RTX 4070 Laptop，主屏 3840×2160 200%，
副屏 2560×1600 150%，原点 (3840,1134)。20 次往返、共 40 次跨屏拖窗通过，包含
192→144→192 DPI 切换；双 handle 截图、滚轮事件、Ctrl+A/退格、Unicode 粘贴、拖拽
取消释放通过。GPU 转换后 3.006 秒的 4K/30fps 录制写入 91 帧、丢帧 0，MF 解码成功。
随后补测了普通左/右键单击和 Left 键，断言测试窗口实际收到 down/up，鼠标按键已释放。
原先截图仅在内存校验；补测已保存两屏 JPG 与原始 BGRA，JPG 经解码/查看画面正常。
字母键测试会受当前 IME 影响，因此按键回执使用方向键，Unicode 文本使用粘贴验证。
加入音频包余量后的测试音实测录制 3.099 秒、93 帧、丢帧 0，解码音频峰值 414（int16），
验证系统 loopback 非静音。离线回归在 CPython 3.10 上 92 项通过、2 项跳过（含 MCP）；
3.14t 基础库 61 项通过、1 项跳过，扩展导入及运行没有开启 GIL。跳过项涉及
Windows 符号链接权限和现有 subprocess 后代进程组语义，不跳过编码测试。

生产构建分别产出 CPython 3.10 / 3.14t x64 wheel 和 sdist。两个 wheel 已在独立环境
以 `--no-deps` 安装验证：基础包不需要 MCP、所有公开导出可解析、没有 `_test_*` 接口，
3.14t 保持 GIL 关闭。sdist 包含 Windows 原生源码，不含生成的二进制文件。
PE 依赖检查只发现 Python、MSVC 运行时和 Windows 系统 DLL；MF DLL 为延迟加载。

MSVC `/W4` 编译通过；`/analyze` 剩余提示为 Python `PyArg_ParseTuple("s")` 的字符串
终止契约无法被 SAL 推断，以及 Windows SDK 头文件内的批注问题。原生资源和返回值
相关提示已处理。GitHub Actions 已通过 x64 四种 Python 配置的基础库检查，以及普通
Python 的 MCP 检查；ARM64 3.14 / 3.14t 已通过原生编译、合成测试和生产 wheel
构建。ARM64 可选 MCP 因上游 wheel 缺失而暂不纳入 CI。
macOS 15 / 26 的 14 项 Python 矩阵也已通过。各次提交的完整结果以
[GitHub Actions](https://github.com/czf0613/computer_use_py/actions) 为准。

这是短时实测，不代表长时间性能或所有驱动兼容性。Windows 10、ARM64 桌面交互、HDR、
其他键盘布局和第三方 OLE 文件拖放需对应环境补充验收。macOS 原生代码未在本机运行。
