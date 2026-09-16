# Windows 技术选型与实施方案

更新：2026-09-16。已按用户后续确认完成本分支实现：硬件编码优先、允许软件回退，
编码过载时丢弃过期视频帧。实现细节和完整验证边界见 [Windows 后端](windows.md)。

## 技术选型

| 能力 | Windows 实现 | macOS 对应 |
| --- | --- | --- |
| 原生扩展 | C++20 / C++/WinRT / CPython C API，setuptools | C / Objective-C / CPython C API |
| 显示器 | EnumDisplayMonitors / GetMonitorInfoW，进程内 ID | CoreGraphics |
| 截图 | Windows Graphics Capture，CreateForMonitor / CreateFreeThreaded | ScreenCaptureKit |
| 像素转换 | D3D11 video processor，CPU BT.709 备选 | CoreVideo |
| JPEG | Windows Imaging Component | ImageIO |
| H.264 / AAC / MP4 | Media Foundation Sink Writer | VideoToolbox / AVFoundation |
| 解码验证 | Media Foundation Source Reader | 平台原生解码测试 |
| 系统声音 | WASAPI shared-mode loopback，48 kHz 双声道 | ScreenCaptureKit audio |
| 键鼠 | SendInput / GetPhysicalCursorPos | CGEvent |
| 剪贴板 | Win32 CF_UNICODETEXT | pbcopy / pbpaste |

运行库不引入 FFmpeg、libav、PyAV、x264/x265，也不引入 NVENC、AMF、oneVPL 等厂商 SDK。
MF 通过驱动使用硬件属于系统 API 实现细节。C++20 是本机 Windows SDK / MSVC 组合下
C++/WinRT 的构建选择；构建继续使用 uv 和 setuptools。

目标范围：Windows 10 22H2、Windows 11 23H2 及以后，x64 / ARM64。该范围表示目标
兼容性，不表示所有版本均已实测。WGC monitor interop 从 Windows 10 1903 起提供。
[CreateForMonitor 文档](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createformonitor)

## 实施结构

- `native_code/windows/include/backend.h`：COM、线程状态、错误边界和句柄所有权。
- `native_code/windows/src/input.cpp`：显示器、物理坐标、多屏路径、键鼠、剪贴板。
- `native_code/windows/src/capture.cpp`：每屏独立 WGC 会话、最新帧缓存、BGRA/JPEG/NV12。
- `native_code/windows/src/recording.cpp`：WASAPI、MF、时钟、过载策略和文件发布。
- `native_code/windows/src/module.cpp`：与 macOS 相同的私有扩展接口。
- Python 层保留现有公开函数、async 取消清理和导入路径，按平台选择键码与粘贴快捷键。
- MCP 开放 Windows 桌面工具，返回明确的坐标单位和录制丢帧统计。

## 多显示器设计

Windows 输入统一使用虚拟桌面物理像素。显示器原点允许为负数，UI 缩放与输入坐标
分别表达。原尺寸截图像素加上屏幕原点即可转换成输入坐标；macOS 仍保持 points。

平滑移动/拖拽先验证目标位于实际显示器内，再经过相邻屏幕的可见接缝。负坐标、不同
DPI、上下错位和虚拟桌面外接矩形中的空白区都需要独立验证。拖拽实际提交 down/move/up，
取消时在 finally 中释放按钮；不把设置窗口位置当作跨屏拖拽验收。

滚轮方向统一描述内容移动方向，在 Windows 原生边界转换符号。Windows distance
表示 wheel steps，实际滚动行数由系统和应用决定。

## 录制过载设计

1. MF 请求硬件 transforms；初始化失败时尝试软件配置，不保证强制硬件。
2. WGC 只保留最新帧。编码工作线程保存待提交样本和下一样本，不积累无界原始帧队列。
3. 保留 Sink Writer 默认节流。编码阻塞后按 QPC 时钟跳到当前帧槽，计入 frames_dropped。
4. 延长上一视频样本的 duration，使文件保持真实经过时间；停止后最后一帧截到停止时刻。
5. 音频与视频共用 QPC 原点。音频保留 100 ms 包到达余量、最多五秒队列；过载超界失败。
6. 文件写到同目录唯一临时文件，finalize 成功后不覆盖地发布。失败/取消清理临时文件。

使用 D3D11 优先转换为 NV12 后读回，允许 CPU 转换，当前并非全程零拷贝。
[MF 硬件 transform 属性](https://learn.microsoft.com/en-us/windows/win32/medfound/mf-readwrite-enable-hardware-transforms)
与 [Sink Writer 节流](https://learn.microsoft.com/en-us/windows/win32/medfound/mf-sink-writer-disable-throttling)
说明了本实现使用的系统策略开关。

## 实施与验收状态

| 阶段 | 交付 | 当前状态 |
| --- | --- | --- |
| 接口与构建 | Windows 扩展、ABI 区分、Python/MCP 接入 | 已实现 |
| 输入与双屏 | 显示器、键鼠、剪贴板、跨屏路径 | 本机 40 次跨屏拖拽通过 |
| 截图 | 两个并行 capture handle、BGRA/JPEG | 两块屏幕实测通过 |
| 录制 | H.264/AAC MP4、软件编码、过载丢帧 | 合成编码/解码与实际录屏通过 |
| 生命周期 | 并发停止、析构/取消、禁止覆盖 | 自动化通过 |
| CPython | 3.10 / 3.14t，无 GIL 验证 | 本机 x64 通过 |
| 分发 | 无测试 hooks 的 wheel、sdist | 本地构建检查；未发布 |
| ARM64 / Win10 | 平台兼容验收 | ARM64 原生编译/合成测试 CI 通过；Win10、ARM64 桌面交互尚未实测 |

验收设备：Windows 11 build 26200 x64、RTX 4070 Laptop；3840×2160 / 200% 主屏，
2560×1600 / 150% 副屏，副屏原点 (3840,1134)。40 次跨屏窗口拖拽包含实际 DPI 切换。
音频余量修正后，测试音录制约 3.099 秒，4K/30 fps 写入 93 帧、丢帧 0，解码非静音。

后续在对应设备/CI 补齐：Windows 10、ARM64 实机、长时间负载、HDR 色彩、多 GPU 热插拔、
休眠恢复和更多键盘布局。当前 HDR 输出仅按 SDR 路径处理；拓扑/设备变更可能要求重启
采集。Windows MCP 的 pywin32 依赖暂缺 3.14t 安装包，cryptography 暂缺 ARM64 wheel；
MCP 在 x64 普通 Python 上验证，ARM64 CI 保留基础库与 wheel 检查，不构建 OpenSSL。
