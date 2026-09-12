# 原生扩展审查记录（2026-09-12）

本次范围：macOS C/Objective-C 扩展、Python async 封装、Python 3.10/free-threaded
兼容性、构建和发布配置。Windows 目录只有 CMake 占位配置，没有可审查的原生实现。

## 已修复的问题

| 问题 | 原因和影响 | 修复 |
| --- | --- | --- |
| 停止后悬空 capsule | `stop_capture` 直接 free，capsule 仍保存旧指针；重复停止/读帧可能 use-after-free | pointer 保持不变，关闭状态与实际析构分离，仅最后一个 capsule 引用销毁时 free |
| ARC 资源泄漏 | calloc 结构体中的 stream/delegate/dispatch queue 在 free 时不会自动释放；失败路径直接 free | 统一清理；强引用显式置 nil，缓存 CVPixelBuffer Release，销毁 mutex |
| 停止与回调竞态 | delegate 保存 raw handle，系统停止/超时不保证再无回调 | 在交付 queue 上断开 back-pointer 并清空缓存，迟到回调不再访问 handle |
| 异步超时处理缺失 | semaphore wait 结果被忽略，启动可能尚未完成就返回；completion 结果无可靠同步 | TimeoutError，completion 独立持有结果并同步访问，启动迟到时执行停止 |
| GIL 阻塞与 no-GIL 不兼容 | 原模块无 free-threading 声明，导入会重新启用 GIL；等待期间持有 GIL | 条件声明支持 no-GIL；等待/编码 detach thread state；队列和 mutex 保护共享状态 |
| 原生参数错误 | 忽略 PyArg_ParseTuple 失败，继续使用未初始化变量；stub 不能校验运行时参数 | 检查解析结果，保留原异常；限制 ID、key code、flags、quality 和滚动整数范围 |
| CoreGraphics/CoreVideo 失败处理 | mode/event/encoder 创建失败未检查；数据尺寸可能溢出；忽略 JPEG finalize 失败 | 检查返回值和尺寸，成对 unlock/release，在失败时抛出 Python 异常 |
| 复审发现的 GC 重入死锁 | 本次新增 reader mutex 后，锁内分配 Python 字典会让 GC 回调重入时等待自身锁 | Python 分配全部移到锁外，锁内只进行原生像素复制/编码；增加 GC 回调重入回归 |
| async 取消后按键卡住 | down 后 await 被取消或抛错，up 不执行 | mouse hold/drag 和 keyboard click 使用 finally 释放 |
| Python 3.10 C API 兼容 | 原 BGRA 的 y# 格式缺少旧版本要求的 PY_SSIZE_T_CLEAN | 构建统一定义；实测旧版解释器和 setuptools 最低版本 |
| 源码发行包不完整 | MANIFEST 没包含 Objective-C .m 文件 | 补齐源文件、类型 stub 和 py.typed；从 sdist 重建 wheel 验证 |
| 部署目标与标签不一致 | 编译器/解释器默认 deployment target 可能覆盖环境设定 | 编译和链接显式传入目标；源码默认 12.3 且不限制架构，发布 CI 单独指定 15.0 arm64 并校验 wheel tag、架构及 Mach-O minos |

## 验证范围

- Python 3.10、3.11、3.12、3.13、3.14、3.13t、3.14t：分别构建并执行安全测试。
  测试先核对实际解释器 ABI；不以 `uv --python` 的请求字符串代替实际结果。
- 最终安全测试集为 81 项；普通 3.10–3.14 各 81 通过，3.13t/3.14t 各 80 通过、
  1 跳过（普通解释器同步 GC 分配重入专用用例）。覆盖原生参数异常、并发缓存读写/停止、BGRA/JPEG 输出、
  停止后的访问与更新、析构、CVPixelBuffer release callback 计数、async 取消/异常。
  假 stream 额外覆盖成功/错误 stop、五秒超时后的迟到回调、析构后的回调，
  并统计 stream、delegate、回调持有者及 frame 回收。
- Python 3.10 额外使用声明的最低构建后端 setuptools 77.0.3。
- free-threaded 3.14t 上以 AddressSanitizer + UndefinedBehaviorSanitizer 构建运行
  测试得到 80 通过、1 跳过，未报告内存访问或未定义行为错误。
- Clang static analyzer 检查四个 native 实现文件，无诊断；actionlint 及 YAML 中
  的嵌入 Python 语法检查通过。
- 生产构建从 sdist 生成各 ABI 的 arm64 wheel；检查源码/metadata、Mach-O、类型文件，
  并确认生产扩展不包含测试 hooks、导入不启用 GIL。
- 校正源码兼容范围后，默认部署目标 12.3 的 arm64 与 x86_64 扩展均编译、链接通过，
  Mach-O minos 均为 12.3。在本机运行 arm64 合成测试：Python 3.10 为 81 通过，
  3.14t 为 80 通过、1 跳过。更新后的 CI 原生语法检查在 12.3 / 15.0 两个目标下通过。
  从更新后的 sdist 重建 Python 3.10 发布 wheel，确认标签仍为 `macosx_15_0_arm64`，
  Mach-O 为 arm64 / minos 15.0，生产扩展导入成功且不含测试 hooks。

## 尚未覆盖的边界

本机为 macOS 26.6.2 arm64、SDK 26.5。macOS 15 的实际 runner 验证由推送后的
GitHub Actions 执行。macOS 12.3–14 和 Intel Mac 未做实际运行测试，不能用
本地交叉编译和部署目标检查替代运行结果。

没有启动实际 ScreenCaptureKit 流、录制桌面、发出输入事件或请求系统权限。
合成测试不能覆盖 WindowServer、显示器热插拔、权限变化、真实系统 completion
永不返回等行为；异步 start/content 超时交错及分配/编码失败也未做故障注入。
假 stream 不直接统计生产 completion 或 dispatch queue 的析构。首次真实桌面集成测试应单独安排。原生 stop 是关闭 handle，
系统停止异步完成；并发的多步键鼠动作仍需调用方自行串行化。

`leaks --atExit` 的合成进程检查输出为 0 leaks，但同时提示进程调试权限受限，
该结果不能单独作为无泄漏证明。更可靠的本次证据是原生 ownership 审查、
合成 CVPixelBuffer 的最终释放计数和 sanitizer 执行；不宣称所有系统路径零泄漏。

CI 配置和 Trusted Publisher 绑定完成后，仍需将变更提交推送才会在远端运行。
本次未创建 release 或向 PyPI 上传版本。
