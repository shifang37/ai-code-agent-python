"""统一响应信封与业务异常 —— 与 Java 版 BaseResponse / ErrorCode 逐字对齐。

前端 `src/request.ts` 依赖 `code == 0` 判定成功、`code == 40100` 触发跳登录，
因此这里的数值不能改。
"""

from enum import IntEnum
from typing import Any, Generic, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

T = TypeVar("T")


class ErrorCode(IntEnum):
    SUCCESS = 0
    PARAMS_ERROR = 40000
    NOT_LOGIN_ERROR = 40100
    NO_AUTH_ERROR = 40101
    FORBIDDEN_ERROR = 40300
    NOT_FOUND_ERROR = 40400
    TOO_MANY_REQUEST = 42900
    SYSTEM_ERROR = 50000
    OPERATION_ERROR = 50001

    @property
    def default_message(self) -> str:
        return _DEFAULT_MESSAGES[self]


_DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.SUCCESS: "ok",
    ErrorCode.PARAMS_ERROR: "请求参数错误",
    ErrorCode.NOT_LOGIN_ERROR: "未登录",
    ErrorCode.NO_AUTH_ERROR: "无权限",
    ErrorCode.FORBIDDEN_ERROR: "禁止访问",
    ErrorCode.NOT_FOUND_ERROR: "请求数据不存在",
    ErrorCode.TOO_MANY_REQUEST: "请求过于频繁",
    ErrorCode.SYSTEM_ERROR: "系统内部异常",
    ErrorCode.OPERATION_ERROR: "操作失败",
}


class BaseResponse(BaseModel, Generic[T]):
    code: int = 0
    data: T | None = None
    message: str = "ok"


def ok(data: Any = None) -> dict:
    return {"code": 0, "data": data, "message": "ok"}


class BusinessException(Exception):
    """等价于 Java 版 BusinessException，由全局处理器转成 200 + 错误信封。"""

    def __init__(self, code: ErrorCode | int, message: str | None = None):
        error_code = ErrorCode(code) if not isinstance(code, ErrorCode) else code
        self.code = int(error_code)
        self.message = message or error_code.default_message
        super().__init__(self.message)


def throw_if(condition: bool, code: ErrorCode, message: str | None = None) -> None:
    """等价于 Java 版 ThrowUtils.throwIf。"""
    if condition:
        raise BusinessException(code, message)


def register_exception_handlers(app) -> None:
    """业务异常与未捕获异常统一返回 HTTP 200 + 信封，与 Java 版行为一致。"""
    import logging

    logger = logging.getLogger(__name__)

    @app.exception_handler(BusinessException)
    async def _business(_: Request, exc: BusinessException) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={"code": exc.code, "data": None, "message": exc.message},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("未捕获异常", exc_info=exc)
        return JSONResponse(
            status_code=200,
            content={
                "code": int(ErrorCode.SYSTEM_ERROR),
                "data": None,
                "message": ErrorCode.SYSTEM_ERROR.default_message,
            },
        )
