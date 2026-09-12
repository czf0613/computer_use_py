# 进程执行

公共入口为 `await run_subprocess(executable, args=(), cwd=None, env=None,
use_stream=False, *, encoding=None, errors="strict")`。

非流式返回 `SubprocessResult(returncode, stdout, stderr)`，同时支持属性访问和三元组
解包。非零退出码正常返回，启动失败抛出相应 `OSError`。stdin 使用空输入，避免等待
终端交互；stdout/stderr 并发排空后才返回。

流式返回 `SubprocessStream`，目标程序仍可运行。其 `stdin.write(str)` / `drain()` /
`close()` / `wait_closed()` 用于输入，`stdout` 和 `stderr` 提供异步 `read(size)`、
`readline()`、逐行迭代及 `at_eof()`。`read(size)` 的 size 是字符数；有文本可读时
返回至多 size 个字符，不等到凑满。默认 `read()` 则读取到 EOF。进程对象提供
`pid`、`returncode`、`wait()`、`terminate()`、`kill()` 和 `communicate(input=None)`。
`communicate` 会关闭 stdin、并发收集剩余两路文本并等待退出，即使之前已经读过一部分文本也可使用。

读取 stdout 和 stderr 时应并发消费，或直接使用 `communicate()`；只等待进程退出
可能因输出管道写满而死锁。流式对象返回后由调用方管理，不会自动终止长时间运行的程序。
每一路流仅允许一个消费者，所有操作绑定创建它的 asyncio event loop；不跨线程共享流对象。

非流式执行、shell 初始化以及 `communicate()` 被取消时，会终止并回收子进程。
POSIX 子进程使用独立 session，取消时终止该进程组；Windows 终止直接子进程，
不承诺终止所有后代。清理会关闭本端管道，避免因后代持有管道而无限等待；
取消时未读的输出会丢弃。Windows 必须使用支持 subprocess 的 Proactor event loop。

## 环境来源

- macOS：通过系统账户记录取得 HOME、用户名称和登录 shell，使用固定基础 PATH 与
  UTF-8 locale 启动干净的 login + interactive shell。例如 zsh 会读取 `.zprofile` 和
  `.zshrc`。再由一个隔离的短生命周期 Python 子进程导出该 shell 的环境，保留多行与 Unicode 值，
  忽略启动 banner。这个导出进程不等于调用方的 Python 进程。
- Windows：`OpenProcessToken` + `CreateEnvironmentBlock(..., bInherit=False)` 读取
  当前用户和系统环境；环境块为 UTF-16，使用后释放环境块并关闭 token handle。
  然后启动禁用延迟展开的系统 cmd，执行 AutoRun 配置，通过 stdin 发送环境导出命令，
  避免命令行引号和路径中的 `!` 干扰。PowerShell profile 在调用方显式运行 PowerShell 时由它读取。
- 每次调用重新加载，无全局环境缓存。`env` 最后新增/覆盖，Windows 名称忽略大小写。
  macOS 的 `PWD` 默认与目标 working directory 一致，也可由 `env` 显式覆盖。
  shell 初始化失败或超过 10 秒会抛异常，不会回退到当前 Python 进程的环境。
  当前 Python 内临时设置的 PATH、虚拟环境、代理等不会被自动复制；需要时在 `env` 中明确指定。

可执行文件与参数由 `create_subprocess_exec` 传递；不会自动解释管道、变量展开、重定向或通配符。
需要这些能力时显式选择 shell。例如 `/bin/zsh -c ...` 或 `cmd.exe /c ...`。
Windows 程序名称提前按加载后的 PATH 解析，避免 CreateProcess 使用调用方进程的 PATH；
`.bat` / `.cmd` 文件需显式通过 cmd 执行。

## 编码与验证

macOS 默认 UTF-8。Windows 默认 `GetConsoleOutputCP()`，无控制台时用 `GetOEMCP()`。
这是命令行工具的默认选择，不能保证每个程序使用相同编码；可用 `encoding` 指定
UTF-8、GBK、ANSI 代码页或 UTF-16。解码错误默认抛出 `UnicodeDecodeError`，
也可设置 `errors="replace"`。流使用独立的增量编解码器，正确处理分块字符、BOM 和 CRLF。

`tests/test_process.py` 使用临时 shell 配置、真实本地子进程和管道，覆盖环境隔离、
参数/cwd、PATH、多字节文本、大输出、取消回收及并发调用。Windows 环境块、资源释放和
路径逻辑有本地模拟验证；当前没有 Windows 实机验证，也不扩展 macOS-only 的 CI runner 矩阵。

参考：[Python asyncio subprocess](https://docs.python.org/3.10/library/asyncio-subprocess.html)、
[zsh 启动文件](https://zsh.sourceforge.io/Doc/Release/Files.html)、
[CreateEnvironmentBlock](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-createenvironmentblock)、
[cmd AutoRun](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/cmd)、
[GetConsoleOutputCP](https://learn.microsoft.com/en-us/windows/console/getconsoleoutputcp)。
