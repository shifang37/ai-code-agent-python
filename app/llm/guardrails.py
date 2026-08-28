"""输入安全护栏 —— 逐条移植 Java 版 PromptSafetyInputGuardrail。

只作用于**用户原始输入**。内部管道消息（增强提示词、携带编译报错的自修复提示词）
必须跳过：编译输出动辄数千字，会直接撞上 1000 字上限。
"""

import re

MAX_INPUT_LENGTH = 1000

SENSITIVE_WORDS = [
    "忽略之前的指令",
    "ignore previous instructions",
    "ignore above",
    "破解",
    "hack",
    "绕过",
    "bypass",
    "越狱",
    "jailbreak",
]

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:previous|above|all)\s+(?:instructions?|commands?|prompts?)", re.IGNORECASE),
    re.compile(r"(?:forget|disregard)\s+(?:everything|all)\s+(?:above|before)", re.IGNORECASE),
    re.compile(r"(?:pretend|act|behave)\s+(?:as|like)\s+(?:if|you\s+are)", re.IGNORECASE),
    re.compile(r"system\s*:\s*you\s+are", re.IGNORECASE),
    re.compile(r"new\s+(?:instructions?|commands?|prompts?)\s*:", re.IGNORECASE),
]


def find_violation(text: str | None) -> str | None:
    """返回违规原因；合法则返回 None。"""
    if text is None:
        return "输入内容不能为空"
    if len(text) > MAX_INPUT_LENGTH:
        return f"输入内容过长，不要超过 {MAX_INPUT_LENGTH} 字"
    if not text.strip():
        return "输入内容不能为空"

    lowered = text.lower()
    for word in SENSITIVE_WORDS:
        if word.lower() in lowered:
            return "输入包含不当内容，请修改后重试"

    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return "检测到恶意输入，请求被拒绝"

    return None
