"""自修复闭环的核心逻辑：条件边路由、报错文件定位、修复提示词构造。

这些是纯函数，不需要真跑 LLM 或 npm，但决定了整个自修复回路的正确性——
Java 版在这里踩过的坑（rfind vs find、重试上限）都要有测试锁住。
"""

from app.agent.graph import route_after_build, route_after_quality_check
from app.agent.nodes import (
    MAX_BUILD_RETRIES,
    MAX_QUALITY_RETRIES,
    _build_fix_prompt,
    _collect_related_files,
    _resolve_related_file,
    quality_check_failed,
)
from app.core.paths import CodeGenType
from app.llm.schemas import QualityResult

# ---------------------------------------------------------------- 条件边


def test_构建成功即结束():
    assert route_after_build({"build_error_message": None}) == "success"


def test_构建失败在预算内回流自修复():
    assert route_after_build({"build_error_message": "boom", "build_retry_count": 1}) == "retry"
    assert route_after_build({"build_error_message": "boom", "build_retry_count": MAX_BUILD_RETRIES}) == "retry"


def test_构建重试超预算则放弃():
    state = {"build_error_message": "boom", "build_retry_count": MAX_BUILD_RETRIES + 1}
    assert route_after_build(state) == "abort"


def test_质检通过后仅_vue_工程进构建():
    passed = QualityResult(isValid=True)
    assert route_after_quality_check({"quality_result": passed, "generation_type": CodeGenType.VUE_PROJECT}) == "build"
    assert route_after_quality_check({"quality_result": passed, "generation_type": CodeGenType.HTML}) == "skip_build"


def test_质检不通过回流重生成():
    failed = QualityResult(isValid=False, errors=["缺少标题"])
    state = {"quality_result": failed, "quality_retry_count": 0, "generation_type": CodeGenType.HTML}
    assert route_after_quality_check(state) == "fail"


def test_质检重试超上限时放行交给编译器裁决():
    """Java 版此处无计数器、可无限循环；Python 版补了上限，超限必须放行。"""
    failed = QualityResult(isValid=False, errors=["缺少标题"])
    state = {
        "quality_result": failed,
        "quality_retry_count": MAX_QUALITY_RETRIES + 1,
        "generation_type": CodeGenType.VUE_PROJECT,
    }
    assert route_after_quality_check(state) == "build"


def test_质检失败判定要求错误列表非空():
    assert not quality_check_failed(None)
    assert not quality_check_failed(QualityResult(isValid=True))
    # isValid=False 但没给具体错误，无法构造修复提示词，不算失败
    assert not quality_check_failed(QualityResult(isValid=False, errors=[]))
    assert quality_check_failed(QualityResult(isValid=False, errors=["e"]))


# ---------------------------------------------------------------- 报错文件定位


def test_部署路径含_src_段时仍能定位报错文件(tmp_path):
    """回归 Java 版 commit 2fd956a：用 find 会锁到部署路径里的 src 上，静默丢文件。

    这里刻意把项目根放在一个自身含 `src` 段的路径下，模拟容器 `WORKDIR /usr/src/app`。
    """
    project_root = tmp_path / "usr" / "src" / "app" / "vue_project_1"
    (project_root / "src" / "components").mkdir(parents=True)
    target = project_root / "src" / "components" / "Foo.vue"
    target.write_text("<template></template>", encoding="utf-8")

    # 报错里带的是被正则从中间截断的绝对路径
    candidate = "usr/src/app/vue_project_1/src/components/Foo.vue"
    assert _resolve_related_file(project_root, candidate) == target


def test_报错文件内容被附进修复提示词(tmp_path):
    project_root = tmp_path / "vue_project_2"
    (project_root / "src").mkdir(parents=True)
    (project_root / "src" / "App.vue").write_text("BROKEN CONTENT", encoding="utf-8")

    error = "error during build:\nsrc/App.vue:3:1: Unclosed block"
    related, paths = _collect_related_files(str(project_root), error)

    assert paths == ["src/App.vue"]
    assert "BROKEN CONTENT" in related


def test_依赖目录与构建产物被排除(tmp_path):
    project_root = tmp_path / "vue_project_3"
    (project_root / "node_modules" / "vite").mkdir(parents=True)
    (project_root / "node_modules" / "vite" / "index.js").write_text("x", encoding="utf-8")
    (project_root / "dist").mkdir()
    (project_root / "dist" / "app.js").write_text("x", encoding="utf-8")

    error = "failed at node_modules/vite/index.js and dist/app.js"
    _, paths = _collect_related_files(str(project_root), error)
    assert paths == []


def test_超长文件内容被截断(tmp_path):
    project_root = tmp_path / "vue_project_4"
    (project_root / "src").mkdir(parents=True)
    (project_root / "src" / "Big.vue").write_text("x" * 10000, encoding="utf-8")

    related, _ = _collect_related_files(str(project_root), "src/Big.vue: boom")
    assert "内容过长已截断" in related
    assert len(related) < 6000


def test_修复提示词包含报错与轮次(tmp_path):
    state = {
        "build_error_message": "失败阶段: npm run build\nUnclosed block",
        "build_retry_count": 2,
        "generated_code_dir": str(tmp_path),
    }
    prompt, _ = _build_fix_prompt(state)
    assert "只修改导致报错的文件" in prompt
    assert "第 2 次失败" in prompt
    assert "Unclosed block" in prompt
