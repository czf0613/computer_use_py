# CI 与 PyPI 发布

## 自动检查

`.github/workflows/ci.yml` 在推送 `master`、向 `master` 提交 PR 以及手动触发时运行。
矩阵为 macOS 15 / 26 arm64，Python 3.10、3.11、3.12、3.13、3.14、3.13t、3.14t。
每个环境检查 Python/类型 stub 语法、C/Objective-C 在 13.0 / 15.0 部署目标下的
语法和 API 可用性，并执行合成/参数/取消安全测试。
CI 不申请屏幕录制、辅助功能权限，也不运行真实桌面测试。

`.github/workflows/windows.yml` 验证 Windows x64 的 3.10、3.14、3.13t、3.14t，
以及 ARM64 的 3.14、3.14t，检查原生编译、合成媒体/离线输入、无 GIL 约定和生产 wheel。
普通 x64 Python 额外验证
MCP；ARM64 的 cryptography wheel 和 Windows free-threaded 的 pywin32 wheel 缺失
不影响基础库发布，也不通过构建 OpenSSL 绕过。验证边界见 [Windows 后端](windows.md)。

两个检查 workflow 都支持 `workflow_call`，发布流程会针对发布提交重新运行它们。

## Trusted Publishing 配置

PyPI 项目 `scapkit_computer_use` 的 Publishing 页面应绑定：

| 字段 | 值 |
| --- | --- |
| Owner | `czf0613` |
| Repository | `computer_use_py` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

GitHub 使用同名 environment `pypi`。发布 job 在 Linux 上执行官方 PyPI action，
仅此 job 获得 `id-token: write`，使用短期 OIDC 身份，无需保存 PyPI API token。
修改仓库路径、workflow 文件名或 environment 时必须同步更新 PyPI 绑定。

## 发布流程

1. 修改 `pyproject.toml` 中的版本号并同步 `uv.lock`；选择 PyPI 上未使用的版本号。
2. 运行本地安全测试，提交并推送到 `master`，等待 CI 成功。
3. 从相应提交创建与版本号一致的 tag，例如版本 `0.2.0` 对应 `v0.2.0`。
4. 在 GitHub 发布该 tag 的 Release。`release.published` 事件触发 `release.yml`。
5. Workflow 验证 tag/版本、运行检查、生成 sdist、macOS 15+ arm64 与 Windows x64/ARM64 wheels；
   构建与验证全部成功后才上传 PyPI。失败时到 Actions 查看具体 job。

每个平台/架构构建七种 ABI：CPython 3.10、3.11、3.12、3.13、3.14，以及
3.13t、3.14t，共 **21 个 wheel + 1 个 sdist**。普通 CPython 和 free-threaded CPython
使用各自 ABI 的 wheel；不发布 macOS Intel 或 Windows 32-bit wheel。
macOS 15+ arm64 限定官方 macOS wheel 的发行范围。源码构建默认部署目标为 macOS 13.0，
不限制 CPU 架构；较早系统和 Intel Mac 的实际运行不在当前 CI 验证范围内。
Windows 两种架构在对应的原生 runner 上从同一个 sdist 构建并安装测试每个 wheel，
检查导入、架构、无测试接口及 free-threaded 模式不启用 GIL；同时核对 wheel ABI 集合、
PE 二进制架构、类型文件、MCP 系统信息模块与操作指南和包元数据。
这些检查不操作桌面，不扩展 Windows 10 或 ARM64 桌面交互的验收范围。
发布构建禁用 `SCAPKIT_TESTING`。下载 sdist 的用户可通过 setuptools 构建，源码包必须
包含 macOS 的 `.m`、`.c`、`.h` 和 Windows 的 `.cpp`、`.h`，并排除生成的二进制文件。

## 发布前预检

在 Actions 中手动运行 `Publish to PyPI`（选择准备发布的分支），或使用：

```sh
gh workflow run release.yml --ref master
```

`workflow_dispatch` 会完成与正式发布相同的检查、源码包和 wheel 构建，并保留
`pypi-sdist`、`pypi-wheels`、`pypi-wheels-win_amd64`、`pypi-wheels-win_arm64`
四份 artifact；它不要求已有 release tag，也**不会上传 PyPI**。
`publish` job 仅允许 `release.published` 事件，且依赖全部 macOS/Windows 检查与构建成功。
手动预检不能代替正式发布时的 tag/版本一致性检查。

发布结果请核对对应版本的 GitHub Actions 和 PyPI 页面。

参考：[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)、
[GitHub 托管 runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)、
[cibuildwheel](https://cibuildwheel.pypa.io/en/stable/options/)。
