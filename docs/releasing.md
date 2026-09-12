# CI 与 PyPI 发布

## 自动检查

`.github/workflows/ci.yml` 在推送 `master`、向 `master` 提交 PR 以及手动触发时运行。
矩阵为 macOS 15 / 26 arm64，Python 3.10、3.11、3.12、3.13、3.14、3.13t、3.14t。
每个环境检查 Python/类型 stub 语法、C/Objective-C 在 12.3 / 15.0 部署目标下的
语法和 API 可用性，并执行合成/参数/取消安全测试。
CI 不申请屏幕录制、辅助功能权限，也不运行真实桌面测试。

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

1. 修改 `pyproject.toml` 中的版本号；当前 `0.0.2` 已在 PyPI 发布，不能重复使用。
2. 运行本地安全测试，提交并推送到 `master`，等待 CI 成功。
3. 从相应提交创建与版本号一致的 tag，例如版本 `0.0.3` 对应 `v0.0.3`。
4. 在 GitHub 发布该 tag 的 Release。`release.published` 事件触发 `release.yml`。
5. Workflow 验证 tag/版本、运行检查、生成 sdist 与 macOS 15+ arm64 wheels；
   构建与验证全部成功后才上传 PyPI。失败时到 Actions 查看具体 job。

普通 CPython 和 free-threaded CPython 使用各自 ABI 的 wheel；不发布 x64 或 Windows wheel。
macOS 15+ arm64 仅限定官方 wheel 的发行范围。源码构建默认部署目标为 macOS 12.3，
不限制 CPU 架构；较早系统和 Intel Mac 的实际运行不在当前 CI 验证范围内。
发布构建禁用 `SCAPKIT_TESTING`。下载 sdist 的用户可通过 setuptools 构建，源码包必须
包含 `.m`、`.c`、`.h`，避免本地成功而源码安装缺少 Objective-C 文件。

配置或本地构建成功不等于远程发布成功；首次 release 仍需查看 Actions 和 PyPI 的结果。

参考：[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)、
[GitHub 托管 runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)、
[cibuildwheel](https://cibuildwheel.pypa.io/en/stable/options/)。
