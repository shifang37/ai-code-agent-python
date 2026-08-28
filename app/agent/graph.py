"""LangGraph 工作流定义 —— 移植 Java 版 CodeGenWorkflow。

主图：
    START → image_collector → prompt_enhancer → router → code_generator
          → code_quality_check ─┬─ build ──→ project_builder ─┬─ success → END
                                ├─ skip_build → END           ├─ retry → code_generator
                                └─ fail → code_generator      └─ abort → END

修复子图（故障注入评测用）：从既有代码目录直接进 project_builder，只跑自修复回路。
"""

import itertools
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    MAX_BUILD_RETRIES,
    MAX_QUALITY_RETRIES,
    code_generator_node,
    code_quality_check_node,
    image_collector_node,
    project_builder_node,
    prompt_enhancer_node,
    quality_check_failed,
    router_node,
)
from app.agent.state import WorkflowState, initial_state, snapshot
from app.core.paths import CodeGenType, code_output_dir
from app.llm.guardrails import find_violation

logger = logging.getLogger(__name__)

# appId 与 Java 版同样以当前毫秒时间戳起步，保证与旧产出目录不撞名
_app_id_counter = itertools.count(int(time.time() * 1000))


def next_app_id() -> int:
    """生成工作流运行的 appId。

    它同时是对话记忆的隔离键和产出目录名——Java 版曾把它硬编码成 0，导致多次运行
    共用一份记忆，滑动窗口把 tool_calls 消息挤掉后留下孤儿 tool 结果，被 API 直接拒绝。
    """
    return next(_app_id_counter)


# ---------------------------------------------------------------- 条件边


def route_after_quality_check(state: WorkflowState) -> str:
    """质检后：不合格就回去重生成，合格则按类型决定要不要构建。"""
    quality = state.get("quality_result")
    if quality is None or quality_check_failed(quality):
        if state.get("quality_retry_count", 0) > MAX_QUALITY_RETRIES:
            # Java 版这里没有上限、可无限循环。超限就放行，交给真实编译器裁决。
            logger.warning("质检重试已达上限 %d 次，跳过质检回路", MAX_QUALITY_RETRIES)
            return _route_build_or_skip(state)
        logger.info("质检未通过，回流到代码生成节点")
        return "fail"
    return _route_build_or_skip(state)


def _route_build_or_skip(state: WorkflowState) -> str:
    """只有 Vue 工程需要 npm 构建；HTML / 多文件是静态产物，直接结束。"""
    return "build" if state.get("generation_type") is CodeGenType.VUE_PROJECT else "skip_build"


def route_after_build(state: WorkflowState) -> str:
    """构建后：成功即结束；失败在预算内回流自修复，超预算则放弃。"""
    if not state.get("build_error_message"):
        return "success"
    retry_count = state.get("build_retry_count", 0)
    if retry_count <= MAX_BUILD_RETRIES:
        logger.info("构建失败，第 %d 次回流自修复", retry_count)
        return "retry"
    logger.error("构建失败且重试已达上限 %d 次，放弃", MAX_BUILD_RETRIES)
    return "abort"


# ---------------------------------------------------------------- 图构建


def build_codegen_graph(checkpointer=None):
    """完整代码生成工作流。"""
    graph = StateGraph(WorkflowState)
    graph.add_node("image_collector", image_collector_node)
    graph.add_node("prompt_enhancer", prompt_enhancer_node)
    graph.add_node("router", router_node)
    graph.add_node("code_generator", code_generator_node)
    graph.add_node("code_quality_check", code_quality_check_node)
    graph.add_node("project_builder", project_builder_node)

    graph.add_edge(START, "image_collector")
    graph.add_edge("image_collector", "prompt_enhancer")
    graph.add_edge("prompt_enhancer", "router")
    graph.add_edge("router", "code_generator")
    graph.add_edge("code_generator", "code_quality_check")
    graph.add_conditional_edges(
        "code_quality_check",
        route_after_quality_check,
        {"build": "project_builder", "skip_build": END, "fail": "code_generator"},
    )
    graph.add_conditional_edges(
        "project_builder",
        route_after_build,
        {"success": END, "retry": "code_generator", "abort": END},
    )
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def build_repair_graph(include_quality_check: bool = False, checkpointer=None):
    """修复子图：从既有代码目录起步，只跑「构建 → 修复 → 再构建」这一段。

    故障注入评测用它来度量自修复能力，不重复走生成全流程。
    """
    graph = StateGraph(WorkflowState)
    graph.add_node("project_builder", project_builder_node)
    graph.add_node("code_generator", code_generator_node)

    graph.add_edge(START, "project_builder")

    if include_quality_check:
        graph.add_node("code_quality_check", code_quality_check_node)
        graph.add_edge("code_generator", "code_quality_check")
        graph.add_conditional_edges(
            "code_quality_check",
            route_after_quality_check,
            {"build": "project_builder", "skip_build": END, "fail": "code_generator"},
        )
    else:
        graph.add_edge("code_generator", "project_builder")

    graph.add_conditional_edges(
        "project_builder",
        route_after_build,
        {"success": END, "retry": "code_generator", "abort": END},
    )
    return graph.compile(checkpointer=checkpointer or MemorySaver())


# ---------------------------------------------------------------- 执行入口

# 递归上限：每轮自修复要走 code_generator + project_builder(+ quality) 两三步，
# 留足 MAX_BUILD_RETRIES 轮的余量。
_RECURSION_LIMIT = 60


def _config(app_id: int) -> dict:
    return {"configurable": {"thread_id": str(app_id)}, "recursion_limit": _RECURSION_LIMIT}


async def execute_workflow(prompt: str, app_id: int | None = None) -> dict[str, Any]:
    """同步执行整条工作流，返回最终状态快照。"""
    violation = find_violation(prompt)
    if violation:
        raise ValueError(violation)

    app_id = app_id or next_app_id()
    graph = build_codegen_graph()
    final = await graph.ainvoke(initial_state(prompt, app_id), config=_config(app_id))
    return snapshot(final)


async def execute_workflow_stream(prompt: str) -> AsyncIterator[tuple[str, dict]]:
    """流式执行，逐节点 yield `(事件名, 负载)`。

    事件名与 Java 版 SSE 保持一致：workflow_start / step_completed /
    workflow_completed / workflow_error，前端解析逻辑无需改动。
    """
    violation = find_violation(prompt)
    if violation:
        yield "workflow_error", {"error": violation}
        return

    app_id = next_app_id()
    graph = build_codegen_graph()
    state = initial_state(prompt, app_id)

    yield "workflow_start", {"appId": app_id, "prompt": prompt}

    step_number = 0
    latest: dict = dict(state)
    try:
        async for chunk in graph.astream(state, config=_config(app_id), stream_mode="values"):
            latest = chunk
            step_number += 1
            yield "step_completed", {"stepNumber": step_number, "currentStep": chunk.get("current_step", "")}
        yield "workflow_completed", snapshot(latest)
    except Exception as e:
        logger.exception("工作流执行失败")
        yield "workflow_error", {"error": str(e)}


async def execute_repair(seed: WorkflowState, include_quality_check: bool = False) -> dict[str, Any]:
    """从既有产物起步执行修复子图（评测入口）。

    入口强校验种子目录必须等于 `code_output_dir(type, appId)`：否则第二轮起
    code_generator 会按公式重算目录，去修另一棵目录树，评测结论直接失真。
    """
    app_id = seed.get("app_id")
    gen_type = seed.get("generation_type")
    if not app_id or gen_type is None:
        raise ValueError("修复子图的种子状态必须带 app_id 与 generation_type")

    expected = str(code_output_dir(gen_type, app_id))
    actual = seed.get("generated_code_dir", "")
    if actual != expected:
        raise ValueError(f"种子目录与约定不一致：期望 {expected}，实际 {actual}")

    graph = build_repair_graph(include_quality_check)
    final = await graph.ainvoke(dict(seed), config=_config(app_id))
    return {**snapshot(final), "buildErrorHistory": final.get("build_error_history", [])}
