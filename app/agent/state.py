"""工作流状态。

对 Java 版的实质改进：Java 把整个 `WorkflowContext` POJO 塞进单个 state key 里
就地 mutate，代码注释里明确警告「在节点外 append buildErrorHistory 会被状态快照丢弃」。
这里改成扁平 TypedDict + reducer：累积型字段用 `operator.add` 合并，节点只返回增量，
不存在「改了但没生效」这类陷阱。
"""

import operator
from typing import Annotated, Any, TypedDict

from app.core.paths import CodeGenType
from app.llm.schemas import ImageResource, QualityResult


class WorkflowState(TypedDict, total=False):
    # ---- 输入 ----
    original_prompt: str
    # appId 由工作流入口生成，整个运行期间不变：
    # 它同时是对话记忆的隔离键和产出目录名，自修复回流必须复用同一个值。
    app_id: int

    # ---- 过程产物 ----
    current_step: str
    image_list: list[ImageResource]
    image_list_str: str
    enhanced_prompt: str
    generation_type: CodeGenType
    generated_code_dir: str
    build_result_dir: str
    error_message: str

    # ---- 质检回路 ----
    quality_result: QualityResult | None
    quality_retry_count: int

    # ---- 构建自修复回路 ----
    build_error_message: str | None
    build_retry_count: int

    # ---- 评测观测点（累积，靠 reducer 合并）----
    build_error_history: Annotated[list[str], operator.add]
    fix_prompt_related_files_by_round: Annotated[list[list[str]], operator.add]


def initial_state(prompt: str, app_id: int) -> WorkflowState:
    return {
        "original_prompt": prompt,
        "app_id": app_id,
        "current_step": "",
        "image_list": [],
        "image_list_str": "",
        "enhanced_prompt": "",
        "generated_code_dir": "",
        "build_result_dir": "",
        "quality_result": None,
        "quality_retry_count": 0,
        "build_error_message": None,
        "build_retry_count": 0,
        "build_error_history": [],
        "fix_prompt_related_files_by_round": [],
    }


def snapshot(state: WorkflowState) -> dict[str, Any]:
    """把状态转成可 JSON 序列化的字典，供 SSE 与 /workflow/execute 返回。"""
    gen_type = state.get("generation_type")
    quality = state.get("quality_result")
    return {
        "currentStep": state.get("current_step", ""),
        "originalPrompt": state.get("original_prompt", ""),
        "enhancedPrompt": state.get("enhanced_prompt", ""),
        "generationType": gen_type.value if gen_type else None,
        "generatedCodeDir": state.get("generated_code_dir", ""),
        "buildResultDir": state.get("build_result_dir", ""),
        "buildErrorMessage": state.get("build_error_message"),
        "buildRetryCount": state.get("build_retry_count", 0),
        "appId": state.get("app_id"),
        "imageList": [img.model_dump(mode="json") for img in state.get("image_list", [])],
        "qualityResult": quality.model_dump() if quality else None,
        "errorMessage": state.get("error_message"),
    }
