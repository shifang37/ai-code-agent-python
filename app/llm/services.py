"""声明式 AI 服务 —— 对应 Java 版那几个 langchain4j `@AiService` 接口。

统一范式：`ChatPromptTemplate.from_messages([("system", 字面提示词), ("human", "{input}")])`
是**不能用的**，因为提示词与用户输入都可能含花括号。这里一律手工构造消息列表：
    [SystemMessage(content=系统提示词), HumanMessage(content=用户输入)]
用户内容永远只作为 HumanMessage 的 content 字面量，不经任何模板渲染。
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.paths import CodeGenType
from app.llm import prompts_loader as P
from app.llm.models import get_chat_model, get_routing_model
from app.llm.schemas import ImageCollectionPlan, QualityResult, RoutingResult

logger = logging.getLogger(__name__)


def _messages(system_prompt: str, user_text: str) -> list:
    return [SystemMessage(content=system_prompt), HumanMessage(content=user_text)]


def _structured(model, schema):
    """统一用 function_calling 做结构化输出。

    **不能用默认的 json_schema**：DeepSeek 直接返回
    `400 This response_format type is unavailable now`。而路由服务对异常是
    fail-open 回落 HTML 的，一旦这里报错，症状就是「所有需求都被判成 HTML 模式」
    ——错得很安静，务必保持 function_calling。

    另一个坑：模型偶尔会不调用工具、直接回文本，此时 `ainvoke` 返回 **None** 而不是抛错。
    每个调用方都必须显式处理 None，否则 `None.xxx` 抛的 AttributeError 会被上层的
    宽异常捕获吞掉，表现为「功能静默失效」——图片收集就这么废过。
    """
    return model.with_structured_output(schema, method="function_calling")


async def route_code_gen_type(user_prompt: str) -> CodeGenType:
    """智能路由：根据需求描述选生成模式。任何异常都回落到 HTML（与 Java 版一致）。"""
    try:
        model = _structured(get_routing_model(), RoutingResult)
        result: RoutingResult | None = await model.ainvoke(_messages(P.load_prompt(P.CODEGEN_ROUTING), user_prompt))
        if result is None:
            logger.warning("路由未返回结构化结果，回落到 HTML 模式")
            return CodeGenType.HTML
        return result.codeGenType
    except Exception:
        logger.exception("路由失败，回落到 HTML 模式")
        return CodeGenType.HTML


async def check_code_quality(code_content: str) -> QualityResult:
    """代码质量检查。调用方负责 fail-open 语义——见 code_quality_check 节点。"""
    model = _structured(get_chat_model(), QualityResult)
    result: QualityResult | None = await model.ainvoke(_messages(P.load_prompt(P.CODE_QUALITY_CHECK), code_content))
    if result is None:
        # 与节点里的异常分支保持一致：裁判失灵就放行，交给真实编译器裁决
        logger.warning("质检未返回结构化结果，按通过处理")
        return QualityResult(isValid=True)
    return result


async def plan_image_collection(user_prompt: str) -> ImageCollectionPlan:
    """规划要收集哪些图片素材。失败时返回空计划，不阻断主流程。"""
    try:
        model = _structured(get_chat_model(), ImageCollectionPlan)
        result: ImageCollectionPlan | None = await model.ainvoke(
            _messages(P.load_prompt(P.IMAGE_COLLECTION_PLAN), user_prompt)
        )
        if result is None:
            logger.warning("图片收集计划未返回结构化结果，跳过素材收集")
            return ImageCollectionPlan()
        return result
    except Exception:
        logger.warning("图片收集计划生成失败，跳过素材收集", exc_info=True)
        return ImageCollectionPlan()
