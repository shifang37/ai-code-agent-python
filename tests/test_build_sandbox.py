"""构建子进程的安全收敛。

`npm run build` 执行的是 **模型生成的** `vite.config.js`——也就是说构建阶段一定会在
宿主机上跑不可信 JS。代码层面能做的是收窄它能拿到的东西：默认继承的环境变量里
带着 LLM_API_KEY、数据库口令、COS 密钥（实测 106 个变量全量继承），
一句 `process.env.LLM_API_KEY` 就能读走外传。这些测试锁住白名单不被改回全量继承。
"""

import sys

import pytest

from app.agent import builder

SECRET_VARS = [
    "LLM_API_KEY",
    "DB_PASSWORD",
    "COS_SECRET_KEY",
    "COS_SECRET_ID",
    "DASHSCOPE_API_KEY",
    "PEXELS_API_KEY",
    "REDIS_PASSWORD",
    "SESSION_SECRET",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
]


@pytest.fixture
def secrets_in_env(monkeypatch):
    for name in SECRET_VARS:
        monkeypatch.setenv(name, f"leaked-{name}")


def test_密钥不会传给构建子进程(secrets_in_env):
    env = builder._child_env()
    leaked = [name for name in SECRET_VARS if name in env]
    assert leaked == [], f"这些密钥泄漏给了构建进程: {leaked}"
    # 值层面再兜一道，防止密钥被塞进别名变量
    assert not [k for k, v in env.items() if v.startswith("leaked-")]


def test_环境是白名单而非黑名单(secrets_in_env, monkeypatch):
    """黑名单迟早会漏——新增一个密钥变量就破防。这里验证的是「未知变量默认不透传」。"""
    monkeypatch.setenv("SOME_FUTURE_CREDENTIAL", "top-secret")
    assert "SOME_FUTURE_CREDENTIAL" not in builder._child_env()


def test_保留_node_运行所需的变量():
    env = builder._child_env()
    # PATH 没了就找不到 node/npm，构建必然失败
    assert "PATH" in env
    if sys.platform == "win32":
        # Windows 上缺 SystemRoot 会让 node 起不来
        assert "SystemRoot" in env
    else:
        assert "HOME" in env


def test_关闭彩色输出让报错更干净():
    """构建报错要回流给模型做定点修复，ANSI 颜色码是纯噪音。"""
    env = builder._child_env()
    assert env["NO_COLOR"] == "1"
    assert env["CI"] == "1"


async def test_install_使用_ignore_scripts(tmp_path, monkeypatch):
    """任意 npm 包的 postinstall 是「装个包即在宿主机执行任意命令」的主入口。

    实测生成项目在 --ignore-scripts 下照常构建成功（现代 esbuild 用
    optionalDependencies 分发预编译二进制，不依赖 postinstall）。
    """
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "dist").mkdir()

    calls: list[list[str]] = []

    async def fake_run(working_dir, args, timeout):
        calls.append(args)
        return builder.BuildResult.ok()

    monkeypatch.setattr(builder, "_run", fake_run)
    monkeypatch.delenv("FAULT_EVAL_SKIP_INSTALL", raising=False)

    result = await builder.build_project_with_result(tmp_path)

    assert result.success
    install_args = next(a for a in calls if "install" in a)
    assert "--ignore-scripts" in install_args

