"""工作流节点 —— 逐个移植 Java 版 langgraph4j/node/ 下的 6 个节点。

每个节点只返回**增量** dict，累积字段交给 state.py 里的 reducer 合并。
"""

import logging
import re
from pathlib import Path

from app.agent import codegen
from app.agent.builder import BuildResult, build_project_with_result
from app.agent.state import WorkflowState
from app.core.paths import CodeGenType, code_output_dir
from app.llm.schemas import ImageResource, QualityResult
from app.llm.services import check_code_quality, plan_image_collection, route_code_gen_type
from app.media import collect_images

logger = logging.getLogger(__name__)

# ---- 自修复闭环的边界参数 ----
MAX_BUILD_RETRIES = 3
# Java 版的质检回路没有计数器、理论上可无限循环（评测因此默认关掉质检）。
# 这里补上上限，超限即放行进入构建阶段，由真实编译器给出终局判断。
MAX_QUALITY_RETRIES = 2

# 修复提示词中最多附带的相关源文件数 / 单文件内容上限
MAX_RELATED_FILES = 3
MAX_FILE_CONTENT_LENGTH = 4000

# 从构建报错中提取源文件路径（如 src/components/Foo.vue、src/main.ts）
SOURCE_FILE_PATTERN = re.compile(r"(?:[\w.-]+[/\\])*[\w.-]+\.(?:vue|ts|tsx|js|jsx|json|css|scss|html)")

# 质检时纳入的代码文件扩展名
CODE_EXTENSIONS = (".html", ".htm", ".css", ".js", ".json", ".vue", ".ts", ".jsx", ".tsx")
SKIP_DIRS = {"node_modules", "dist", "target", ".git"}


# ---------------------------------------------------------------- 图片收集


async def image_collector_node(state: WorkflowState) -> dict:
    """先让模型规划要收集哪些素材，再并发执行四类收集任务。

    整个节点是尽力而为：任何一路失败只记日志，不阻断主流程（与 Java 版一致）。
    """
    logger.info("执行节点: 图片收集")
    images: list[ImageResource] = []
    try:
        plan = await plan_image_collection(state["original_prompt"])
        images = await collect_images(plan)
        logger.info("并发图片收集完成，共收集到 %d 张图片", len(images))
    except Exception:
        logger.exception("图片收集失败")
    return {"current_step": "图片收集", "image_list": images}


# ---------------------------------------------------------------- 提示词增强


async def prompt_enhancer_node(state: WorkflowState) -> dict:
    """把收集到的素材列表拼进原始提示词。纯字符串拼接，不调模型。"""
    logger.info("执行节点: 提示词增强")
    parts = [state["original_prompt"]]
    images = state.get("image_list") or []
    image_list_str = state.get("image_list_str") or ""

    if images or image_list_str:
        parts.append("\n\n## 可用素材资源\n")
        parts.append("请在生成网站使用以下图片资源，将这些图片合理地嵌入到网站的相应位置中。\n")
        if images:
            for img in images:
                parts.append(f"- {img.category.text}：{img.description}（{img.url}）\n")
        else:
            parts.append(image_list_str)

    enhanced = "".join(parts)
    logger.info("提示词增强完成，增强后长度: %d 字符", len(enhanced))
    return {"current_step": "提示词增强", "enhanced_prompt": enhanced}


# ---------------------------------------------------------------- 智能路由


async def router_node(state: WorkflowState) -> dict:
    """选择生成模式。失败回落 HTML —— 由 route_code_gen_type 内部兜底。"""
    logger.info("执行节点: 智能路由")
    gen_type = await route_code_gen_type(state["original_prompt"])
    logger.info("AI智能路由完成，选择类型: %s (%s)", gen_type.value, gen_type.text)
    return {"current_step": "智能路由", "generation_type": gen_type}


# ---------------------------------------------------------------- 代码生成


async def code_generator_node(state: WorkflowState) -> dict:
    """自修复闭环的核心：按当前失败状态挑选提示词，生成并落盘。"""
    logger.info("执行节点: 代码生成")

    app_id = state.get("app_id")
    if not app_id:
        raise RuntimeError("app_id 未初始化，无法隔离对话记忆与产出目录")

    gen_type: CodeGenType = state["generation_type"]
    user_message, related_files = _build_user_message(state)

    logger.info("开始生成代码，类型: %s (%s)", gen_type.value, gen_type.text)
    # 内部管道：提示词由系统构造，用户输入已在工作流入口校验过，跳过输入护栏
    async for _chunk in codegen.generate_and_save_code_stream(user_message, gen_type, app_id, internal_pipeline=True):
        pass

    # 目录每轮重算，是全流程唯一真源；修复子图入口也据此校验种子
    generated_dir = code_output_dir(gen_type, app_id)
    logger.info("AI 代码生成完成，生成目录: %s", generated_dir)

    update: dict = {
        "current_step": "代码生成",
        "generated_code_dir": str(generated_dir),
        # 构建报错已消费，置空避免影响后续轮次
        "build_error_message": None,
    }
    if related_files is not None:
        update["fix_prompt_related_files_by_round"] = [related_files]
    return update


def _build_user_message(state: WorkflowState) -> tuple[str, list[str] | None]:
    """挑选本轮的用户消息，并返回修复提示词里实际附带的文件相对路径（供评测度量）。

    优先级：构建报错（真实编译器信号）> 质检失败（LLM 裁判信号）> 增强提示词。
    """
    build_error = state.get("build_error_message")
    if build_error and build_error.strip():
        return _build_fix_prompt(state)

    quality = state.get("quality_result")
    if quality_check_failed(quality):
        return _quality_fix_prompt(quality), None

    return state.get("enhanced_prompt") or state["original_prompt"], None


def _build_fix_prompt(state: WorkflowState) -> tuple[str, list[str]]:
    """构建失败的定点修复提示词：编译报错 + 报错涉及的源文件当前内容。"""
    build_error = state["build_error_message"]
    retry_count = state.get("build_retry_count", 0)

    related, paths = _collect_related_files(state.get("generated_code_dir", ""), build_error or "")

    prompt = (
        "上一轮生成的项目执行 npm 构建失败，请根据下面的真实编译报错定点修复相关文件，"
        "不要重写整个项目，只修改导致报错的文件。\n\n"
        f"## 构建报错信息（第 {retry_count} 次失败）\n"
        f"```\n{build_error}\n```\n"
    )
    if related:
        prompt += f"\n## 报错涉及的文件当前内容\n{related}"
    prompt += "\n请修复上述编译错误，确保 npm run build 能够成功执行。"
    return prompt, paths


def _collect_related_files(generated_code_dir: str, build_error: str) -> tuple[str, list[str]]:
    """从报错里提取源文件路径，读回当前内容附进提示词。"""
    if not generated_code_dir:
        return "", []
    try:
        project_root = Path(generated_code_dir).resolve()
    except OSError:
        logger.warning("解析生成目录失败: %s", generated_code_dir)
        return "", []

    resolved: dict[str, Path] = {}  # 保序去重
    for match in SOURCE_FILE_PATTERN.finditer(build_error):
        if len(resolved) >= MAX_RELATED_FILES:
            break
        candidate = match.group().replace("\\", "/")
        if "node_modules" in candidate or "dist/" in candidate or candidate == "package-lock.json":
            continue
        file = _resolve_related_file(project_root, candidate)
        if file is None:
            continue
        rel = _to_project_relative(project_root, file)
        if rel and rel not in resolved:
            resolved[rel] = file

    chunks = []
    for rel, file in resolved.items():
        try:
            content = file.read_text(encoding="utf-8")
            if len(content) > MAX_FILE_CONTENT_LENGTH:
                content = content[:MAX_FILE_CONTENT_LENGTH] + "\n...(内容过长已截断)"
            chunks.append(f"### {rel}\n```\n{content}\n```\n")
        except OSError:
            logger.warning("读取报错相关文件失败: %s", file)

    return "".join(chunks), list(resolved.keys())


def _resolve_related_file(project_root: Path, candidate: str) -> Path | None:
    """把报错里的路径解析到项目根下的真实文件。

    先按相对项目根解析；再兜底处理「报错携带绝对路径、正则从中间截断」的情形，
    取最后一个 `src/` 起的尾段重试。

    **必须用 rfind 而非 find**：项目部署路径自身可能含 `src` 段
    （容器 `WORKDIR /usr/src/app`、检出在 `~/src` 下），取首次出现会锁到部署路径的
    src 上，解析失败并静默丢掉真正的报错文件——Java 版就踩过这个坑（commit 2fd956a）。
    """
    under_root = project_root / candidate
    if under_root.is_file():
        return under_root

    idx = candidate.rfind("src/")
    if idx >= 0:
        tail = project_root / candidate[idx:]
        if tail.is_file():
            return tail
    return None


def _to_project_relative(project_root: Path, file: Path) -> str | None:
    """规整为相对项目根的路径；解析到项目根之外则忽略。"""
    try:
        resolved = file.resolve()
        return resolved.relative_to(project_root).as_posix()
    except (OSError, ValueError):
        return None


def quality_check_failed(quality: QualityResult | None) -> bool:
    return quality is not None and not quality.isValid and bool(quality.errors)


def _quality_fix_prompt(quality: QualityResult) -> str:
    lines = ["\n\n## 上次生成的代码存在以下问题，请修复：\n"]
    lines += [f"- {e}\n" for e in quality.errors]
    if quality.suggestions:
        lines.append("\n## 修复建议：\n")
        lines += [f"- {s}\n" for s in quality.suggestions]
    lines.append("\n请根据上述问题和建议重新生成代码，确保修复所有提到的问题。")
    return "".join(lines)


# ---------------------------------------------------------------- 代码质量检查


async def code_quality_check_node(state: WorkflowState) -> dict:
    """LLM 裁判式质检。异常时 **fail-open**（isValid=True），不让裁判故障卡住流程。"""
    logger.info("执行节点: 代码质量检查")
    try:
        code_content = _read_and_concatenate_code_files(state.get("generated_code_dir", ""))
        if not code_content.strip():
            logger.warning("未找到可检查的代码文件")
            quality = QualityResult(
                isValid=False,
                errors=["未找到可检查的代码文件"],
                suggestions=["请确保代码生成成功"],
            )
        else:
            quality = await check_code_quality(code_content)
            logger.info("代码质量检查完成 - 是否通过: %s", quality.isValid)
    except Exception:
        logger.exception("代码质量检查异常")
        quality = QualityResult(isValid=True)

    update: dict = {"current_step": "代码质量检查", "quality_result": quality}
    if quality_check_failed(quality):
        update["quality_retry_count"] = state.get("quality_retry_count", 0) + 1
    return update


def _read_and_concatenate_code_files(code_dir: str) -> str:
    if not code_dir:
        return ""
    directory = Path(code_dir)
    if not directory.is_dir():
        logger.error("代码目录不存在或不是目录: %s", code_dir)
        return ""

    parts = ["# 项目文件结构和代码内容\n\n"]
    for file in sorted(directory.rglob("*")):
        if not file.is_file():
            continue
        rel = file.relative_to(directory)
        if file.name.startswith(".") or SKIP_DIRS & set(rel.parts):
            continue
        if not file.name.lower().endswith(CODE_EXTENSIONS):
            continue
        try:
            content = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        parts.append(f"## 文件: {rel.as_posix()}\n\n{content}\n\n")
    return "".join(parts)


# ---------------------------------------------------------------- 项目构建


async def project_builder_node(state: WorkflowState) -> dict:
    """跑真实 npm 构建。失败时把编译输出写回状态，由条件边决定是否回流自修复。"""
    logger.info("执行节点: 项目构建")
    generated_dir = state.get("generated_code_dir", "")

    try:
        result = await build_project_with_result(generated_dir)
    except Exception as e:
        logger.exception("Vue 项目构建异常")
        result = BuildResult.fail("构建执行", f"构建过程发生异常: {e}")

    if result.success:
        build_result_dir = str(Path(generated_dir) / "dist")
        logger.info("Vue 项目构建成功，dist 目录: %s", build_result_dir)
        return {
            "current_step": "项目构建",
            "build_result_dir": build_result_dir,
            "build_error_message": None,
        }

    retry_count = state.get("build_retry_count", 0) + 1
    error_message = f"失败阶段: {result.failed_stage}\n{result.output}"
    logger.error("Vue 项目第 %d 次构建失败，失败阶段: %s", retry_count, result.failed_stage)
    return {
        "current_step": "项目构建",
        "build_retry_count": retry_count,
        "build_error_message": error_message,
        # 快照本轮报错：build_error_message 会被 code_generator 消费置空，历史仍可回溯
        "build_error_history": [error_message],
        # 失败时先回退为源码目录，重试耗尽则以此作为最终结果
        "build_result_dir": generated_dir,
    }
