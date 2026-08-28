"""Vue 项目构建器 —— 移植 core/builder/VueProjectBuilder。

自修复闭环的**真实信号源**：这里跑的是真的 `npm install && npm run build`，
失败时把合并后的 stdout+stderr（尾部截断 5000 字）交回状态，由条件边路由回
代码生成节点做定点修复。日志尾部才是报错所在，所以截断必须保留末尾。

## 安全边界

这里执行的是 **LLM 生成的、未经审查的代码**：`vite.config.js` 由模型写出，
`npm run build` 会直接执行它。因此本模块做了三层收敛（见 `_child_env`）：

1. `npm install --ignore-scripts` —— 掐掉任意 npm 包的 preinstall/install/postinstall，
   这是「装个包就能在宿主机执行任意命令」的主入口。实测生成项目照常构建成功
   （现代 esbuild 通过 optionalDependencies 分发预编译二进制，不依赖 postinstall）。
2. **环境变量白名单** —— 默认情况下子进程会继承父进程全部环境变量（实测 106 个），
   其中包含 LLM_API_KEY、数据库口令、COS 密钥。模型生成的构建脚本一句
   `process.env.LLM_API_KEY` 就能读走并外传。这里只透传 node/npm 跑得起来所必需的变量。
3. 超时强杀 —— 防止构建挂死占住进程。

**仍未覆盖的风险**：`npm run build` 本身仍在宿主机上执行不可信 JS，可以读写当前用户
有权访问的文件、发起网络请求。要真正封住，需要容器 / 虚拟机 / 独立低权限账户级别的隔离，
那是部署形态的事，代码层面做不到。生产环境务必补上。
"""

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_OUTPUT_LENGTH = 5000
INSTALL_TIMEOUT = 300  # 5 分钟
BUILD_TIMEOUT = 180  # 3 分钟

# 子进程环境变量白名单：只放 node/npm 启动与解析路径所必需的。
# 任何带密钥语义的变量（*_API_KEY、DB_*、COS_*、REDIS_*…）都不在此列。
_ENV_ALLOWLIST_WINDOWS = (
    "PATH", "PATHEXT", "COMSPEC", "SystemRoot", "SystemDrive", "windir",
    "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "ProgramData",
    "ProgramFiles", "ProgramFiles(x86)", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)
_ENV_ALLOWLIST_POSIX = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "USER")


def _child_env() -> dict[str, str]:
    """构造构建子进程的最小环境。

    只白名单放行，不用黑名单——黑名单永远会漏（新增一个密钥变量就破防），
    而 node/npm 需要的变量是有限且稳定的。
    """
    names = _ENV_ALLOWLIST_WINDOWS if sys.platform == "win32" else _ENV_ALLOWLIST_POSIX
    env = {name: os.environ[name] for name in names if name in os.environ}
    # 关掉 npm 的更新提示与彩色输出，让回给模型的报错更干净
    env["NO_UPDATE_NOTIFIER"] = "1"
    env["NO_COLOR"] = "1"
    env["CI"] = "1"
    return env



def _skip_install() -> bool:
    """故障注入评测开关：复用共享 node_modules，跳过 npm install。

    对应 Java 版 `-Dfault.eval.skipInstall`。这个开关误带进真实部署会让所有生成
    项目都缺依赖，而 `npm run build` 报的「vite 不存在」与代码缺陷无法区分，
    自修复会白烧 3 轮 LLM——所以打开时必须留一条 warning 日志便于定位。
    """
    return os.environ.get("FAULT_EVAL_SKIP_INSTALL", "").lower() == "true"


@dataclass
class BuildResult:
    success: bool
    failed_stage: str = ""
    output: str = ""

    @staticmethod
    def ok() -> "BuildResult":
        return BuildResult(success=True)

    @staticmethod
    def fail(stage: str, output: str) -> "BuildResult":
        return BuildResult(success=False, failed_stage=stage, output=output)


def _npm() -> str:
    return "npm.cmd" if sys.platform == "win32" else "npm"


def _tail(output: str) -> str:
    if len(output) <= MAX_OUTPUT_LENGTH:
        return output
    return "...(前面日志已截断)...\n" + output[-MAX_OUTPUT_LENGTH:]


async def _run(working_dir: Path, args: list[str], timeout: int) -> BuildResult:
    """执行命令，合并 stderr 到 stdout（避免双流读取死锁），超时强杀。"""
    command = " ".join(args)
    logger.info("在目录 %s 中执行命令: %s", working_dir, command)
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(working_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # 只给最小环境：构建脚本是模型写的，不能让它看到密钥
            env=_child_env(),
        )
    except OSError as e:
        logger.error("执行命令失败: %s, 错误信息: %s", command, e)
        return BuildResult.fail(command, f"执行命令异常: {e}")

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        logger.error("命令执行超时（%s秒），强制终止进程", timeout)
        process.kill()
        await process.wait()
        return BuildResult.fail(command, f"命令执行超时（{timeout}秒）")

    output = _tail(stdout.decode(errors="replace"))
    if process.returncode == 0:
        logger.info("命令执行成功: %s", command)
        return BuildResult.ok()
    logger.error("命令执行失败，退出码: %s，输出:\n%s", process.returncode, output)
    return BuildResult.fail(command, f"退出码 {process.returncode}，输出:\n{output}")


async def build_project_with_result(project_path: str | Path) -> BuildResult:
    """构建并返回详细结果（失败时携带编译报错，供自修复闭环使用）。"""
    project_dir = Path(project_path)
    if not project_dir.is_dir():
        logger.error("项目目录不存在: %s", project_dir)
        return BuildResult.fail("前置检查", f"项目目录不存在: {project_dir}")
    if not (project_dir / "package.json").exists():
        logger.error("package.json 文件不存在: %s", project_dir / "package.json")
        return BuildResult.fail("前置检查", "package.json 文件不存在，项目结构不完整")

    logger.info("开始构建 Vue 项目: %s", project_dir)

    if _skip_install():
        logger.warning("FAULT_EVAL_SKIP_INSTALL=true，已跳过 npm install：仅评测场景应出现此日志")
    else:
        install = await _run(project_dir, [_npm(), "install", "--ignore-scripts"], INSTALL_TIMEOUT)
        if not install.success:
            logger.error("npm install 执行失败")
            return BuildResult.fail("npm install", install.output)

    build = await _run(project_dir, [_npm(), "run", "build"], BUILD_TIMEOUT)
    if not build.success:
        logger.error("npm run build 执行失败")
        return BuildResult.fail("npm run build", build.output)

    dist_dir = project_dir / "dist"
    if not dist_dir.exists():
        logger.error("构建完成但 dist 目录未生成: %s", dist_dir)
        return BuildResult.fail("校验 dist", "构建命令执行成功但 dist 目录未生成，请检查构建输出配置")

    logger.info("Vue 项目构建成功，dist 目录: %s", dist_dir)
    return BuildResult.ok()


async def build_project(project_path: str | Path) -> bool:
    return (await build_project_with_result(project_path)).success
