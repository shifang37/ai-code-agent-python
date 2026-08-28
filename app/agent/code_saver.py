"""代码解析与落盘 —— 移植 core/parser/ 与 core/saver/。

HTML / 多文件模式下模型输出的是带围栏的 markdown，需要抽出代码块再分文件写盘：
- HTML       → index.html
- MULTI_FILE → index.html / style.css / script.js
"""

import logging
import re
from pathlib import Path

from app.core.paths import CodeGenType, code_output_dir
from app.core.response import BusinessException, ErrorCode
from app.llm.schemas import HtmlCodeResult, MultiFileCodeResult

logger = logging.getLogger(__name__)

HTML_CODE_PATTERN = re.compile(r"```html\s*\n([\s\S]*?)```", re.IGNORECASE)
CSS_CODE_PATTERN = re.compile(r"```css\s*\n([\s\S]*?)```", re.IGNORECASE)
JS_CODE_PATTERN = re.compile(r"```(?:js|javascript)\s*\n([\s\S]*?)```", re.IGNORECASE)


def _extract(content: str, pattern: re.Pattern) -> str | None:
    match = pattern.search(content)
    return match.group(1) if match else None


def parse_html_code(content: str) -> HtmlCodeResult:
    """抽出 ```html 围栏内容；没有围栏时把整段输出当作 HTML（与 Java 版一致）。"""
    html = _extract(content, HTML_CODE_PATTERN)
    return HtmlCodeResult(htmlCode=html.strip() if html and html.strip() else content.strip())


def parse_multi_file_code(content: str) -> MultiFileCodeResult:
    html = _extract(content, HTML_CODE_PATTERN)
    css = _extract(content, CSS_CODE_PATTERN)
    js = _extract(content, JS_CODE_PATTERN)
    return MultiFileCodeResult(
        htmlCode=html.strip() if html else "",
        cssCode=css.strip() if css else "",
        jsCode=js.strip() if js else "",
    )


def parse_code(content: str, gen_type: CodeGenType):
    if gen_type is CodeGenType.HTML:
        return parse_html_code(content)
    if gen_type is CodeGenType.MULTI_FILE:
        return parse_multi_file_code(content)
    raise BusinessException(ErrorCode.SYSTEM_ERROR, f"不支持的解析类型：{gen_type.value}")


def _write(dir_path: Path, filename: str, content: str | None) -> None:
    """内容为空则跳过——多文件模式下 css/js 可以缺省。"""
    if content and content.strip():
        (dir_path / filename).write_text(content, encoding="utf-8")


def save_code(result, gen_type: CodeGenType, app_id: int) -> Path:
    """落盘并返回目录。目录名由 code_output_dir 统一给出，全流程唯一真源。"""
    if result is None:
        raise BusinessException(ErrorCode.SYSTEM_ERROR, "代码结果对象不能为空")
    if not getattr(result, "htmlCode", "").strip():
        raise BusinessException(ErrorCode.SYSTEM_ERROR, "HTML代码内容不能为空")

    target = code_output_dir(gen_type, app_id)
    target.mkdir(parents=True, exist_ok=True)

    if gen_type is CodeGenType.HTML:
        _write(target, "index.html", result.htmlCode)
    elif gen_type is CodeGenType.MULTI_FILE:
        _write(target, "index.html", result.htmlCode)
        _write(target, "style.css", result.cssCode)
        _write(target, "script.js", result.jsCode)
    else:
        raise BusinessException(ErrorCode.SYSTEM_ERROR, f"不支持的保存类型：{gen_type.value}")

    logger.info("保存成功，路径为：%s", target)
    return target
