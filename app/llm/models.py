"""模型工厂 —— DeepSeek 走 OpenAI 兼容接口。

Java 版把这四个模型声明成 prototype 作用域的 Spring bean 以规避并发问题；
LangChain 的 ChatOpenAI 实例本身是无状态的（每次调用新建 HTTP 请求），
所以这里用模块级单例即可，不需要每次新建。
"""

from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import settings


def _base_kwargs() -> dict:
    return {
        "base_url": settings.llm_base_url,
        "api_key": settings.llm_api_key,
        "max_retries": settings.llm_max_retries,
        "timeout": 600,
    }


@lru_cache
def get_chat_model() -> ChatOpenAI:
    """通用对话模型（HTML / 多文件生成、质量检查）。"""
    return ChatOpenAI(
        model=settings.llm_chat_model,
        max_tokens=settings.llm_max_tokens,
        **_base_kwargs(),
    )


@lru_cache
def get_streaming_chat_model() -> ChatOpenAI:
    """流式对话模型。LangChain 里流式与否由调用方 `astream` 决定，
    这里单独留一个工厂只是为了与 Java 版配置项一一对应。"""
    return ChatOpenAI(
        model=settings.llm_chat_model,
        max_tokens=settings.llm_max_tokens,
        streaming=True,
        **_base_kwargs(),
    )


@lru_cache
def get_reasoning_model() -> ChatOpenAI:
    """推理模型（deepseek-reasoner），用于 Vue 工程的工具调用循环。"""
    return ChatOpenAI(
        model=settings.llm_reasoning_model,
        max_tokens=settings.llm_reasoning_max_tokens,
        temperature=settings.llm_reasoning_temperature,
        streaming=True,
        **_base_kwargs(),
    )


@lru_cache
def get_routing_model() -> ChatOpenAI:
    """路由模型，只做一次三分类，温度压到 0 保证可复现。"""
    return ChatOpenAI(
        model=settings.llm_routing_model,
        temperature=0,
        **_base_kwargs(),
    )
