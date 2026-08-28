"""代码生成外观 —— 移植 core/AiCodeGeneratorFacade + core/handler/。

对外暴露一个异步生成器 `generate_and_save_code_stream(...)`，逐块 yield
**已格式化好的展示文本**（不是 JSON 信封）。这与 Java 版的分层不同：
Java 里模型层先产出 `{"type":"tool_request",...}` JSON，再由 JsonMessageStreamHandler
转成展示文本；两层之间的 JSON 只是内部胶水，前端从来看不到。Python 直接一步到位。

三种生成模式：
- HTML / MULTI_FILE：普通流式文本，流结束后整段解析并写盘（与 Java 版一致，
  代码是在 `doOnComplete` 时才落盘的——这也是前端只在流正常结束后才刷新预览的原因）。
- VUE_PROJECT：ReAct 工具循环，模型自己调 writeFile 等工具逐个文件写盘。
"""

import json
import logging
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from app.agent import code_saver
from app.agent.builder import build_project
from app.agent.tools import build_file_tools, format_tool_executed, format_tool_request
from app.core.paths import CodeGenType, code_output_dir
from app.core.response import BusinessException, ErrorCode
from app.llm import prompts_loader as P
from app.llm.models import get_reasoning_model, get_streaming_chat_model

logger = logging.getLogger(__name__)

# 与 Java 版 maxSequentialToolsInvocations(20) 对应
MAX_TOOL_ITERATIONS = 20


def _system_prompt_for(gen_type: CodeGenType) -> str:
    return {
        CodeGenType.HTML: P.load_prompt(P.CODEGEN_HTML),
        CodeGenType.MULTI_FILE: P.load_prompt(P.CODEGEN_MULTI_FILE),
        CodeGenType.VUE_PROJECT: P.load_prompt(P.CODEGEN_VUE_PROJECT),
    }[gen_type]


async def _stream_plain_code(user_message: str, gen_type: CodeGenType, app_id: int) -> AsyncIterator[str]:
    """HTML / 多文件模式：边流边攒，流结束后解析并落盘。"""
    model = get_streaming_chat_model()
    # 用户内容只作为 HumanMessage 字面量传入，绝不经模板渲染——
    # 否则输入里的 Vue mustache `{{ }}` 会被当成变量占位符解析。
    messages = [SystemMessage(content=_system_prompt_for(gen_type)), HumanMessage(content=user_message)]

    buffer: list[str] = []
    async for chunk in model.astream(messages):
        text = chunk.content
        if isinstance(text, str) and text:
            buffer.append(text)
            yield text

    try:
        complete = "".join(buffer)
        parsed = code_saver.parse_code(complete, gen_type)
        saved = code_saver.save_code(parsed, gen_type, app_id)
        logger.info("保存成功，路径为：%s", saved)
    except Exception as e:
        # 与 Java 版一致：保存失败只记日志，不把已经流给用户的内容回滚成错误
        logger.error("保存失败: %s", e)


async def _stream_vue_project(user_message: str, app_id: int, internal_pipeline: bool) -> AsyncIterator[str]:
    """Vue 工程模式：ReAct 工具循环，模型逐个文件写盘。

    Java 版为了拿到流式的工具调用分片，在仓库里 shadow 了 8 个 langchain4j 内部类。
    LangGraph 原生就把 `messages` 流拆成 AIMessageChunk / ToolMessage，不需要打补丁。
    """
    tools = build_file_tools(app_id)
    tool_by_name = {t.name: t for t in tools}
    agent = create_react_agent(
        get_reasoning_model(),
        tools,
        prompt=SystemMessage(content=P.load_prompt(P.CODEGEN_VUE_PROJECT)),
    )

    seen_tool_ids: set[str] = set()
    # tool_call_id -> 累积的入参 JSON 片段。工具入参是分块流过来的，
    # 必须攒齐才能解析；ToolMessage 只带 tool_call_id，回头靠它取回入参。
    pending_args: dict[str, str] = {}
    tool_names: dict[str, str] = {}

    stream = agent.astream(
        {"messages": [HumanMessage(content=user_message)]},
        config={"recursion_limit": MAX_TOOL_ITERATIONS * 2},
        stream_mode="messages",
    )

    async for message, _metadata in stream:
        # 1) 模型正文增量
        if isinstance(message, AIMessageChunk):
            if isinstance(message.content, str) and message.content:
                yield message.content
            # 2) 工具调用：同一个 tool_call id 会分多个 chunk 到达，只在首次出现时提示
            for call in message.tool_call_chunks or []:
                call_id = call.get("id")
                if call_id:
                    pending_args[call_id] = pending_args.get(call_id, "") + (call.get("args") or "")
                    if call.get("name"):
                        tool_names[call_id] = call["name"]
                    if call_id not in seen_tool_ids:
                        seen_tool_ids.add(call_id)
                        yield format_tool_request(tool_names.get(call_id, ""))
            continue

        # 3) 工具执行结果
        if isinstance(message, ToolMessage):
            tool_name = message.name or tool_names.get(message.tool_call_id, "")
            if tool_name in tool_by_name:
                yield format_tool_executed(tool_name, _parse_args(pending_args.get(message.tool_call_id, "")))

    if not internal_pipeline:
        # 内部管道（工作流）由 project_builder 节点统一构建并捕获报错，这里不重复构建
        await build_project(code_output_dir(CodeGenType.VUE_PROJECT, app_id))


def _parse_args(raw: str) -> dict:
    """工具入参只用于生成展示文案，解析失败退化为空 dict 即可，不该中断生成。"""
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def generate_and_save_code_stream(
    user_message: str,
    gen_type: CodeGenType | None,
    app_id: int,
    internal_pipeline: bool = False,
) -> AsyncIterator[str]:
    """统一入口：按类型生成并保存代码（流式）。

    Args:
        internal_pipeline: 是否为工作流内部调用。为 True 时跳过用户输入护栏
            （修复提示词携带编译报错，长度远超 1000 字上限），且不重复触发构建。
    """
    if gen_type is None:
        raise BusinessException(ErrorCode.SYSTEM_ERROR, "生成类型为空")

    if gen_type in (CodeGenType.HTML, CodeGenType.MULTI_FILE):
        async for chunk in _stream_plain_code(user_message, gen_type, app_id):
            yield chunk
    elif gen_type is CodeGenType.VUE_PROJECT:
        async for chunk in _stream_vue_project(user_message, app_id, internal_pipeline):
            yield chunk
    else:
        raise BusinessException(ErrorCode.SYSTEM_ERROR, f"不支持的生成类型：{gen_type}")
