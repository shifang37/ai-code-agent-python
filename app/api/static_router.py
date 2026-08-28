"""静态预览路由 —— 对齐 Java 版 StaticResourceController。

这是整个服务的安全边界：它把 **LLM 生成的、未经审查的 HTML/JS** 直接投给浏览器。
两道防线缺一不可：

1. 路径穿越防护：规范化后必须仍在预览根目录内，否则 403。
2. CSP `sandbox`（**不含 allow-same-origin**）：让预览页跑在 opaque origin 里。
   即使被恶意提示词诱导生成了窃取脚本，它也读不到主站 Cookie / localStorage，
   更没法用登录态调主站 API。

因为 iframe 变成了跨源，主站不能再直接摸 contentDocument，可视化编辑改走
postMessage 协议——所以每个 HTML 响应都要注入 visual-editor-agent.js。
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.core.paths import CODE_OUTPUT_ROOT_DIR

logger = logging.getLogger(__name__)

router = APIRouter(tags=["static"])

PREVIEW_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals"
EDITOR_AGENT_SCRIPT_TAG = '<script src="/api/visual-editor-agent.js"></script>'

_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

CONTENT_TYPES = {
    ".html": "text/html; charset=UTF-8",
    ".htm": "text/html; charset=UTF-8",
    ".css": "text/css; charset=UTF-8",
    ".js": "application/javascript; charset=UTF-8",
    ".json": "application/json; charset=UTF-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def _content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def inject_editor_agent(html: str) -> str:
    """把编辑器代理脚本尽量插在 </body> 前。"""
    if EDITOR_AGENT_SCRIPT_TAG in html:
        return html
    for marker in ("</body>", "</html>"):
        idx = html.rfind(marker)
        if idx >= 0:
            return html[:idx] + EDITOR_AGENT_SCRIPT_TAG + html[idx:]
    return html + EDITOR_AGENT_SCRIPT_TAG


@router.get("/visual-editor-agent.js")
async def visual_editor_agent():
    """可视化编辑器代理脚本，由预览页跨源加载。"""
    return FileResponse(_STATIC_DIR / "visual-editor-agent.js", media_type="application/javascript; charset=UTF-8")


@router.get("/static/{deploy_key}")
async def serve_static_root(deploy_key: str, request: Request):
    """不带尾斜杠的目录访问：301 到带斜杠版本，否则页面内相对路径会解析错。"""
    return RedirectResponse(url=str(request.url) + "/", status_code=301)


@router.get("/static/{deploy_key}/{resource_path:path}")
async def serve_static_resource(deploy_key: str, resource_path: str):
    root = CODE_OUTPUT_ROOT_DIR.resolve()
    relative = resource_path or "index.html"
    if relative.endswith("/"):
        relative += "index.html"

    target = (root / deploy_key / relative).resolve()

    # 防线 1：规范化后必须仍在预览根目录内
    if root not in target.parents:
        logger.warning("拦截预览目录穿越尝试: %s/%s", deploy_key, resource_path)
        return Response(status_code=403)

    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        return Response(status_code=404)

    content_type = _content_type(target)
    if content_type.startswith("text/html"):
        try:
            html = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return Response(status_code=500)
        # 防线 2：CSP sandbox，注意没有 allow-same-origin
        return Response(
            content=inject_editor_agent(html),
            media_type=content_type,
            headers={
                "Content-Security-Policy": PREVIEW_CSP,
                "X-Content-Type-Options": "nosniff",
            },
        )

    return FileResponse(target, media_type=content_type, headers={"X-Content-Type-Options": "nosniff"})
