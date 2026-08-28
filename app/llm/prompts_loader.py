"""提示词加载 —— 中文提示词原样复制自 Java 版 resources/prompt/，一字未改。

关键：这些文本里含有 `{` `}`（JSON 示例、Vue mustache），**绝不能**走
`str.format` / f-string / `ChatPromptTemplate.from_template`，否则会被当模板变量解析。
统一用 `("system", <字面量>)` + `MessagesPlaceholder`，用户内容只以 HumanMessage 传入。
"""

from functools import lru_cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent / "prompts"


@lru_cache
def load_prompt(name: str) -> str:
    """按文件名加载提示词。注意 Java 版曾因漏写 `.txt` 后缀静默禁用了质检节点，
    这里找不到文件直接抛错，不做静默降级。"""
    path = _PROMPT_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"提示词文件不存在: {path}")
    return path.read_text(encoding="utf-8")


CODEGEN_HTML = "codegen-html-system-prompt.txt"
CODEGEN_MULTI_FILE = "codegen-multi-file-system-prompt.txt"
CODEGEN_VUE_PROJECT = "codegen-vue-project-system-prompt.txt"
CODEGEN_ROUTING = "codegen-routing-system-prompt.txt"
CODE_QUALITY_CHECK = "code-quality-check-system-prompt.txt"
IMAGE_COLLECTION_PLAN = "image-collection-plan-system-prompt.txt"
IMAGE_COLLECTION = "image-collection-system-prompt.txt"
