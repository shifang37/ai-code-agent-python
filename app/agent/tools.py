"""文件操作工具 —— 移植 Java 版 ai/tools/ 下的 5 个 @Tool。

设计差异：Java 版靠 langchain4j 的 `@ToolMemoryId` 把 appId 注入每次工具调用。
LangChain 没有等价机制，而 Agent 本来就是按 appId 构建的，所以这里用
`build_file_tools(app_id)` 工厂把工作目录闭包进去——模型看到的签名里没有 appId，
少一个参数也就少一个模型填错的机会。

所有相对路径都在 `tmp/code_output/vue_project_{appId}` 下解析，并强制校验
最终路径仍位于项目根内，防止模型写出 `../../` 逃逸（Java 版没有这道校验）。
"""

import logging
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool

from app.core.paths import CODE_OUTPUT_ROOT_DIR

logger = logging.getLogger(__name__)

# 目录树展示时忽略的名称/扩展名
IGNORED_NAMES = {
    "node_modules", ".git", "dist", "build", ".DS_Store",
    ".env", "target", ".mvn", ".idea", ".vscode", "coverage",
}
IGNORED_EXTENSIONS = {".log", ".tmp", ".cache", ".lock"}

# 不允许模型删除的关键文件
IMPORTANT_FILES = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "vite.config.js", "vite.config.ts", "vue.config.js",
    "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json",
    "index.html", "main.js", "main.ts", "app.vue", ".gitignore", "readme.md",
}

# 工具名 -> 中文显示名，供 SSE 信封与聊天记录渲染（对应 Java 版 BaseTool.getDisplayName）
TOOL_DISPLAY_NAMES = {
    "writeFile": "写入文件",
    "readFile": "读取文件",
    "modifyFile": "修改文件",
    "deleteFile": "删除文件",
    "readDir": "读取目录",
}


def project_root_for(app_id: int) -> Path:
    return CODE_OUTPUT_ROOT_DIR / f"vue_project_{app_id}"


def _resolve(root: Path, relative_path: str | None) -> Path | None:
    """把相对路径解析到项目根内。越界返回 None。"""
    root = root.resolve()
    candidate = Path(relative_path or "")
    target = candidate if candidate.is_absolute() else root / candidate
    try:
        target = target.resolve()
    except OSError:
        return None
    if target != root and root not in target.parents:
        return None
    return target


def _should_ignore(name: str) -> bool:
    return name in IGNORED_NAMES or any(name.endswith(ext) for ext in IGNORED_EXTENSIONS)


def build_file_tools(app_id: int) -> list[StructuredTool]:
    """构造绑定到某个 appId 的一组文件工具。"""
    root = project_root_for(app_id)

    def write_file(relativeFilePath: str, content: str) -> str:
        """写入文件到指定路径。

        Args:
            relativeFilePath: 文件的相对路径
            content: 要写入文件的内容
        """
        path = _resolve(root, relativeFilePath)
        if path is None:
            return f"错误：路径越出项目目录 - {relativeFilePath}"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            logger.info("成功写入文件: %s", path)
            # 只回相对路径，不把绝对路径泄露给模型/用户
            return f"文件写入成功: {relativeFilePath}"
        except OSError as e:
            logger.exception("文件写入失败: %s", relativeFilePath)
            return f"文件写入失败: {relativeFilePath}, 错误: {e}"

    def read_file(relativeFilePath: str) -> str:
        """读取指定路径的文件内容。

        Args:
            relativeFilePath: 文件的相对路径
        """
        path = _resolve(root, relativeFilePath)
        if path is None:
            return f"错误：路径越出项目目录 - {relativeFilePath}"
        if not path.is_file():
            return f"错误：文件不存在或不是文件 - {relativeFilePath}"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            logger.exception("读取文件失败: %s", relativeFilePath)
            return f"读取文件失败: {relativeFilePath}, 错误: {e}"

    def modify_file(relativeFilePath: str, oldContent: str, newContent: str) -> str:
        """修改文件内容，用新内容替换指定的旧内容。

        Args:
            relativeFilePath: 文件的相对路径
            oldContent: 要替换的旧内容
            newContent: 替换后的新内容
        """
        path = _resolve(root, relativeFilePath)
        if path is None:
            return f"错误：路径越出项目目录 - {relativeFilePath}"
        if not path.is_file():
            return f"错误：文件不存在或不是文件 - {relativeFilePath}"
        try:
            original = path.read_text(encoding="utf-8")
            if oldContent not in original:
                return f"警告：文件中未找到要替换的内容，文件未修改 - {relativeFilePath}"
            modified = original.replace(oldContent, newContent)
            if modified == original:
                return f"信息：替换后文件内容未发生变化 - {relativeFilePath}"
            path.write_text(modified, encoding="utf-8")
            logger.info("成功修改文件: %s", path)
            return f"文件修改成功: {relativeFilePath}"
        except OSError as e:
            logger.exception("修改文件失败: %s", relativeFilePath)
            return f"修改文件失败: {relativeFilePath}, 错误: {e}"

    def delete_file(relativeFilePath: str) -> str:
        """删除指定路径的文件。

        Args:
            relativeFilePath: 文件的相对路径
        """
        path = _resolve(root, relativeFilePath)
        if path is None:
            return f"错误：路径越出项目目录 - {relativeFilePath}"
        if not path.exists():
            return f"警告：文件不存在，无需删除 - {relativeFilePath}"
        if not path.is_file():
            return f"错误：指定路径不是文件，无法删除 - {relativeFilePath}"
        if path.name.lower() in IMPORTANT_FILES:
            return f"错误：不允许删除重要文件 - {path.name}"
        try:
            path.unlink()
            logger.info("成功删除文件: %s", path)
            return f"文件删除成功: {relativeFilePath}"
        except OSError as e:
            logger.exception("删除文件失败: %s", relativeFilePath)
            return f"删除文件失败: {relativeFilePath}, 错误: {e}"

    def read_dir(relativeDirPath: str = "") -> str:
        """读取目录结构，获取指定目录下的所有文件和子目录信息。

        Args:
            relativeDirPath: 目录的相对路径，为空则读取整个项目结构
        """
        path = _resolve(root, relativeDirPath)
        if path is None:
            return f"错误：路径越出项目目录 - {relativeDirPath}"
        if not path.is_dir():
            return f"错误：目录不存在或不是目录 - {relativeDirPath}"

        lines = ["项目目录结构:"]

        def walk(current: Path, depth: int) -> None:
            entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name))
            for entry in entries:
                if _should_ignore(entry.name):
                    continue
                indent = "  " * depth
                if entry.is_dir():
                    lines.append(f"{indent}{entry.name}/")
                    walk(entry, depth + 1)
                else:
                    lines.append(f"{indent}{entry.name}")

        walk(path, 1)
        return "\n".join(lines)

    return [
        StructuredTool.from_function(func=write_file, name="writeFile", parse_docstring=True),
        StructuredTool.from_function(func=read_file, name="readFile", parse_docstring=True),
        StructuredTool.from_function(func=modify_file, name="modifyFile", parse_docstring=True),
        StructuredTool.from_function(func=delete_file, name="deleteFile", parse_docstring=True),
        StructuredTool.from_function(func=read_dir, name="readDir", parse_docstring=True),
    ]


def format_tool_request(tool_name: str) -> str:
    """对应 Java 版 BaseTool.generateToolRequestResponse。"""
    display = TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
    return f"\n\n[选择工具] {display}\n\n"


def format_tool_executed(tool_name: str, arguments: dict[str, Any]) -> str:
    """对应 Java 版各工具的 generateToolExecutedResult，用于落库和前端展示。"""
    display = TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
    relative_path = arguments.get("relativeFilePath") or arguments.get("relativeDirPath") or ""

    if tool_name == "writeFile":
        suffix = Path(relative_path).suffix.lstrip(".")
        content = arguments.get("content", "")
        return f"\n\n[工具调用] {display} {relative_path}\n\n```{suffix}\n{content}\n```\n\n"
    if tool_name == "modifyFile":
        suffix = Path(relative_path).suffix.lstrip(".")
        old = arguments.get("oldContent", "")
        new = arguments.get("newContent", "")
        return (
            f"\n\n[工具调用] {display} {relative_path}\n\n"
            f"修改前：\n```{suffix}\n{old}\n```\n\n"
            f"修改后：\n```{suffix}\n{new}\n```\n\n"
        )
    return f"\n\n[工具调用] {display} {relative_path}\n\n"
