"""工作流路由 —— 对齐 Java 版 WorkflowSseController。"""

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agent.graph import execute_workflow, execute_workflow_stream
from app.core.response import BusinessException, ErrorCode, ok

router = APIRouter(prefix="/workflow", tags=["workflow"])


@router.post("/execute")
async def execute(prompt: str):
    """同步执行整条工作流，返回最终状态快照。"""
    try:
        return ok(await execute_workflow(prompt))
    except ValueError as e:
        # 入口护栏拒绝属于参数问题，不是系统故障
        raise BusinessException(ErrorCode.PARAMS_ERROR, str(e)) from e


@router.get("/execute-flux")
async def execute_flux(prompt: str):
    """流式执行，逐节点推送进度。

    事件名与 Java 版一致：workflow_start / step_completed /
    workflow_completed / workflow_error。
    """

    async def event_stream():
        async for event_name, payload in execute_workflow_stream(prompt):
            data = json.dumps(payload, ensure_ascii=False, default=str)
            yield f"event: {event_name}\ndata: {data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
