# 屏幕与系统声音录制

下文编码和固定帧率行为描述 macOS。Windows 使用相同 Python 接口，硬件优先、
允许软件回退，过载时丢帧而保留真实时间轴；质量采用码率映射。具体说明见
[Windows 后端](windows.md)。

`start_recording()` 录制指定显示器的完整画面及系统播放的声音，包含录制进程自身
播放的声音。不会采集麦克风。需要 macOS 13+、屏幕录制权限和可用的硬件 H.264 编码器。

```python
import asyncio
from pathlib import Path
from scapkit_computer_use import list_displays, start_recording, stop_recording

async def main():
    display = next(d for d in list_displays() if d["is_main"])
    handle = await start_recording(
        display["id"], Path.home() / "Desktop" / "recording.mp4", fps=30
    )
    try:
        await asyncio.sleep(10)  # 也可在此执行其他异步操作
    finally:
        result = await stop_recording(handle)
    print(result.path)
    print(f"{result.width}×{result.height}, {result.duration_s:.3f}s, {result.size_bytes} bytes")

asyncio.run(main())
```

## 参数和结果

`display_id` 使用 `list_displays()` 返回的 ID。输出路径接受 `str` 或返回字符串的
`os.PathLike`（包括 `pathlib.Path`），父目录必须已经存在，目标文件必须不存在。
保存路径在调用时固定为绝对路径，不创建父目录。文件后缀不决定容器，实际输出始终是 MP4；
建议使用 `.mp4` 后缀，方便 QuickTime 等应用识别。

`fps` 默认 30，接受 `1..2147483647` 的整数，不接受布尔值或小数。
参数范围是时间基准的表示范围，实际可达到的帧率和分辨率受硬件能力限制。
处理持续落后或编码器拒绝配置时录制报错，不会无限缓存。

`stop_recording()` 返回不可变的 `RecordingResult`：

| 字段 | 含义 |
| --- | --- |
| `path` | 已完成 MP4 的绝对 `pathlib.Path` |
| `size_bytes` | 实际文件字节数 |
| `duration_s` | 录制媒体时间轴的时长，单位秒 |
| `width`, `height` | 输出像素尺寸 |
| `fps` | 指定的输出帧率 |

视频使用原生显示像素尺寸，奇数宽高在右侧/底部补齐到偶数，输出 SDR H.264。
这里的 `width/height` 在两平台上都是**视频像素**：macOS 的鼠标和显示器几何仍用
逻辑 points，Windows 则用虚拟桌面物理像素。录制分辨率不改变输入坐标单位，Windows
UI 缩放也不改变输入坐标。不要用含补边的视频宽高推算点击位置；根据录像执行当前
桌面操作前先获取新截图，并按其显示器原点和图片比例换算，见
[MCP 坐标与输出分辨率](mcp-server.md#坐标与输出分辨率)。

VideoToolbox 显式要求硬件编码。`video_quality` 默认 `0.75`，不设置视频码率或数据率限制。
可传入其他 `video_quality`（有限数值 `0.0..1.0`，不接受布尔值）调整压缩质量。
数值越高保留的细节越多，编码器按分辨率和内容分配码率；可先用 `0.5` 做对比：

```python
handle = await start_recording(display_id, output_path, fps=30, video_quality=0.5)
```

它设置 VideoToolbox 的 `Quality`，不设置平均/恒定码率或峰值限制。`None` 保留编码器
默认策略。`0.5`、`0.75` 可分别作为普通和较高质量的比较起点；这个数值不是 x264 的
CRF，也不是画质百分比，`1.0` 不承诺 H.264 无损。降低质量可能损失运动画面、文字等细节。
不同硬件的支持情况可能不同：指定质量时会检查当前编码器支持情况，不支持则明确报错。
本机 Apple Silicon H.264 已验证；未验证 Intel 行为。质量参数仅限关键字传入。

参考：[Apple Quality 参数](https://developer.apple.com/documentation/videotoolbox/kvtcompressionpropertykey_quality)。
音频为 48 kHz 双声道 AAC。没有系统声音的时段写入静音。
文件大小取决于内容、分辨率、质量设置和硬件编码器，需要用实际场景衡量。

## 首帧、静止画面和停止

录制拥有自己的 ScreenCaptureKit stream，无需先启动截图。启动会等待第一个完整画面；
等待成功才开始计时并返回 handle。权限、显示器、硬件编码器或首帧等待失败时抛异常。

最新画面在原生队列上缓存。帧时钟按 `1 / fps` 推进，画面静止且 ScreenCaptureKit
不再输出新画面时，仍会重复编码缓存帧。音频与视频共享 host-clock 起点，避免分别归零
造成音画偏移。尾部保留一个待提交帧，在下一帧边界或停止时确定 duration；因此最后一帧
可能短于 `1 / fps`，以匹配实际停止时刻。

编码允许最多八帧在途，为启动和短暂性能波动留出余量，并限制编码器的帧延迟，
在采集期间提交后继续接收新画面；
不会每提交一帧就强制排空编码器。
停止会确定唯一截止时间、停止采集、排空编码器和写入队列，然后完成 MP4。
完成前写入目标同目录的唯一临时文件，成功后以不覆盖方式发布。录制期间若有其他程序
创建了同名目标文件，停止会报错并保留它。错误路径清理本次临时文件。

同一 handle 可并发或重复调用停止，得到同一次收尾的结果或错误。`CaptureHandle`
与 `RecordingHandle` 不能混用。协程取消时会先等待启动后的清理或停止收尾，再传播
`CancelledError`；清理本身失败时保留异常链。等待原生操作不会占用 asyncio 线程，
也不会持有 GIL。执行器已关闭时的最后清理兜底可能同步执行。

必须显式停止才能保证产生完成文件。丢弃 handle 只会尽力停止和清理，不发布一个成功
录制结果。解释器退出、进程被强制终止或断电不保证临时文件自动清除。

## 验证边界

自动化测试使用内存中的画面与 PCM，执行真实 VideoToolbox/AAC 编码和原生解码，
模拟 stream 错误并检查时间轴、停止、取消及文件保护；不会读取真实屏幕或声音。
这些检查不能证明实际 ScreenCaptureKit 的交付行为、QuickTime 播放效果或实际桌面
场景的音画同步。实际测试前由用户准备环境并明确授权，步骤见
[手工录制验收](../tests/manual/recording.md)。
