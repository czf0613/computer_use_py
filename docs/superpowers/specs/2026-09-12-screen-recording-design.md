# 屏幕与系统音频录制设计

状态：用户已批准实现；实际屏幕/系统声音测试前必须停下等待用户准备。

后续修订：用户完成首次实际录制后要求降低体积，并明确采用质量控制而非固定码率。
新增可选 `video_quality=0..1`，默认 `0.75`（用户验收后确定），显式 `None` 保留编码器策略；仅设置支持的 VideoToolbox
Quality 属性。逐帧同步排空改为最多两帧在途、最终停止排空；详细证据见
`docs/2026-09-12-recording-validation.md`。以下原始设计中的“不设置质量”仅适用于显式传入 `None` 的调用。

## 目标与公开接口

录制用户指定显示器的完整画面，以及电脑正在播放的系统声音，不采集麦克风。
输出为 QuickTime 兼容的 MP4：硬件 H.264 视频、AAC 音频。视频编码不设置码率、
质量或数据率限制，先通过实际文件大小评估默认输出，再决定是否另行支持 HEVC。

拟提供与现有异步接口风格一致的 API：

```python
handle = await start_recording(display_id, output_path, fps=30)
try:
    await do_something()
finally:
    result = await stop_recording(handle)

print(result.path, result.size_bytes, result.duration_s)
```

`display_id` 来自 `list_displays()`。`output_path` 接受字符串或字符串型 PathLike，
父目录必须存在，目标文件已存在时报错。`fps` 为正整数，默认 30；必须能表示为
CoreMedia 的正 int32 timescale。无效参数在启动采集或创建文件之前报错。
`RecordingHandle` 与现有 `CaptureHandle` 区分，不能互相传用。
停止返回不可变的 `RecordingResult`，包含绝对路径、文件大小、实际媒体时长、
像素宽高和指定帧率。

## 技术选择

1. 采用独立的 ScreenCaptureKit stream、直接的 `VTCompressionSession` 和
   `AVAssetWriter`。可控制硬件要求、缓存帧时间戳及编码收尾，代价是显式管理并发和所有权。
2. 由 AVAssetWriter 接收原始视频并管理视频编码，可以减少代码，但直接控制
   VideoToolbox session 和验证实际硬件编码路径更复杂。
3. 系统 `SCRecordingOutput` 简化封装，但要求 macOS 15+，公开配置也未提供这里
   需要的显式 VideoToolbox session 控制。本次采用第一种方案。

录制拥有独立的 native handle 和 stream，用户无需事先调用 `start_capture()`。
现有截图缓存是 capture handle 私有的，不是全局缓存；录制复用其在串行队列上
retain 最新完整帧的所有权规则，不直接借用另一个截图 handle 的裸指针。
既有截图 API 不改变语义，也不隐式开启音频采集。

## 缓存、首帧与录制起点

ScreenCaptureKit 回调只更新最新完整 `CVPixelBuffer`；idle/无效回调不清空已有缓存。
启动录制时先建立 stream、硬件编码 session 和必要的音频配置，再等待首个完整画面。
不能以已成功启动 stream 代替已获取首帧；首帧等待必须有超时，失败时停止并释放资源。

持有首帧后建立媒体起点 `T0`，创建 PTS 为 0 的待编码首帧，并确保第一个编码样本为
可独立解码的关键帧。下一输出边界或停止请求确定其 duration 后再提交编码。
`start_recording()` 仅在 stream、硬件 encoder、writer 对象及首帧准备成功后返回，
后续编码或 writer 输入初始化失败由停止接口报告。
初始化前的等待不计入录制时长；早于 `T0` 的系统音频裁掉，跨越 `T0` 的块按采样边界裁剪。
音频不使用自己的第一个回调时间重新归零，否则会产生音画偏移。

## 静止画面与帧率

按固定输出帧率设计，帧的 PTS 为 `n / fps`，通常 duration 为 `1 / fps`。
原生录制时钟独立于 ScreenCaptureKit 回调：每个输出时刻使用持有的最新完整帧，
没有新画面时继续使用缓存，避免把 ScreenCaptureKit 的回调频率误认为视频输出帧率。
采集侧同时将 `minimumFrameInterval` 设为 `1 / fps`，避免不必要的高频采集。

仅在开始和结束各补一帧也能表达静止画面的持续时间，但那是可变帧率方案，不能保证
固定 30 fps；若以后采用此方案，需要明确改变 `fps` 的语义。

采集帧队列、编码在途帧和音频缓冲必须有界。处理持续落后时停止并报错，不无限堆积
内存，也不通过缩短时间戳掩盖音画不同步。编码后的回调不能同步等待提交编码的队列。

## 停止与末帧

停止请求确定唯一截止时刻 `T1`，与 `T0` 使用相同的 host clock 时间基准。
在所属队列上冻结截止状态、停止输出时钟并 retain 最后一个完整帧；迟到采集回调
不得替换用于收尾的快照或写入截止时间以后的内容。

最后一帧的展示区间必须覆盖到 `T1`：保留一个尚未提交编码的尾帧，在下一帧边界或
停止时确定它的 duration；补齐遗漏的输出时刻，并按截止时间裁剪最后一个帧区间。
不能把最后一次 ScreenCaptureKit 回调的时间当成录制结束时间。
停止恰好位于帧边界时不额外写一帧；非常短的有效录制保留首帧及其实际 duration。
音频使用相同截止点，裁剪跨界 PCM 块；静音与无新视频帧均不能缩短录制时间轴。

收尾顺序：停止接收采集数据、提交末尾样本、调用
`VTCompressionSessionCompleteFrames` 排空编码器、排空写入队列、结束 writer session、
完成音视频输入并等待 `finishWritingWithCompletionHandler`。
全部成功之后，才发布最终 MP4 文件并返回结果。写入期间使用目标同目录的唯一临时文件，
以不覆盖方式发布；失败清理自己创建的临时文件，不删除调用方原有文件。

`stop_recording()` 幂等；并发停止等待同一次收尾并得到一致结果/错误。
取消不能遗弃正在启动的 stream 或正在收尾的 writer。显式停止等待有界，超时和迟到
completion 的资源仍由 native 完成对象持有；不得通过已释放的 handle 回调。
丢弃 handle 触发资源清理，不承诺隐式析构可生成成功的最终文件。

## 编码与音频

设置 `kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder = true`，
检查 session 创建、每次编码和输出回调错误。硬件不可用或不支持分辨率时报错。
不自动切换软件编码、HEVC 或降低分辨率。捕获原生像素尺寸；H.264 所需的偶数尺寸
最多在右侧/底部补一个像素，并在返回结果中报告实际输出尺寸。

视频采用 SDR、8-bit 4:2:0，明确且一致地处理颜色信息；保持录制尺寸固定。
AVAssetWriter 视频输入以 H.264 格式提示透传压缩样本，不进行第二次视频编码。

设置 `capturesAudio = YES`、`excludesCurrentProcessAudio = NO`，不排除任何应用的音频。
系统音频配置为 48 kHz、双声道；由 AVAssetWriter 将 PCM 编码为 AAC。
不注册麦克风输出；macOS 15+ 上显式设置 `captureMicrophone = NO`。
系统没有播放声音时保留静音时间；不申请麦克风权限。

## 版本、文件与验证

库最低 macOS 版本和源码默认 deployment target 统一为 13.0；Python 仍支持 3.10+，
包括 free-threaded 3.13/3.14。官方 wheel 保持 macOS 15+ arm64 的发行范围。
Windows 仍仅提供纯 Python subprocess API。MCP 工具扩展不在本次范围内。

实现以新的 `native_code/osx/src/recording.m` 和 `include/recording.h` 隔离原生录制状态，
在 `ext.c` 注册函数，通过新的 Python recording 模块提供异步包装及结果类型。
同步更新包导出、类型 stub、setuptools 构建与框架链接、IDE 提示、使用文档和 CI 安全测试集。

先写合成测试，再实现：

- 首帧等待、启动失败/超时/取消、完全静止录制、长尾静止、停止在帧边界和不足一帧。
- 合成 PCM 的音画起点偏移、长静音、截止处音频裁剪和 AAC 最终时长。
- 用真实 VideoToolbox 编码合成像素并解析/解码 MP4，检查 H.264、AAC、尺寸、PTS、duration
  和音视频时长；验证全静止录制不会退化为一帧时长。硬件不可用时明确报告验证限制。
- 模拟 writer 背压、编码失败、磁盘错误、并发/重复停止、迟到回调与丢弃 handle。
- Python 3.10 和 free-threaded 构建测试，验证导入没有开启 GIL；编译警告与静态分析。

自动化测试不得采集真实屏幕、系统声音或麦克风，不操作输入和剪贴板。
真实静止桌面、视频播放、音画同步、QuickTime 播放及文件大小对比属于单独授权的手工验收。

## 依据

- Apple SDK `SCStream.h`：系统音频 API 从 macOS 13.0 起可用，麦克风 API 从 15.0 起可用；
  `minimumFrameInterval` 限制回调速率，不保证每个时刻都有新画面。
- [VideoToolbox 硬件编码要求](https://developer.apple.com/documentation/videotoolbox/kvtvideoencoderspecification_requirehardwareacceleratedvideoencoder)
- [VTCompressionSessionEncodeFrame](https://developer.apple.com/documentation/videotoolbox/vtcompressionsessionencodeframe(_:imagebuffer:presentationtimestamp:duration:frameproperties:sourceframerefcon:infoflagsout:))
- Apple SDK `AVAssetWriterInput.h`：MP4 中透传压缩样本必须提供 sourceFormatHint。
- Apple SDK `AVAssetWriter.h`：结束 session 前完成样本提交，finishWriting 完成后检查最终状态。
