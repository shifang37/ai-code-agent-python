"""预览沙箱的两道防线：路径穿越防护 + CSP。

这是整个服务唯一直接把 LLM 生成的 HTML/JS 投给浏览器的地方，
两道防线任何一道失效都是可利用的漏洞，所以必须有回归测试兜住。
"""

import pytest
from fastapi.testclient import TestClient

from app.api.static_router import PREVIEW_CSP, inject_editor_agent
from app.core.paths import CODE_OUTPUT_ROOT_DIR
from app.main import app


@pytest.fixture
def preview_dir():
    d = CODE_OUTPUT_ROOT_DIR / "html_testpreview"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text("<html><body><h1>hi</h1></body></html>", encoding="utf-8")
    # 预览根目录外的“机密”文件，用于验证穿越防护
    (CODE_OUTPUT_ROOT_DIR.parent / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    yield d


def test_预览页带沙箱_csp_且不含_allow_same_origin(preview_dir):
    with TestClient(app) as client:
        resp = client.get("/api/static/html_testpreview/index.html")
    assert resp.status_code == 200
    csp = resp.headers["content-security-policy"]
    assert csp == PREVIEW_CSP
    # 一旦加上 allow-same-origin，预览页就能读主站 Cookie 并以登录态调 API
    assert "allow-same-origin" not in csp


def test_预览页注入可视化编辑器脚本(preview_dir):
    with TestClient(app) as client:
        resp = client.get("/api/static/html_testpreview/index.html")
    assert "/api/visual-editor-agent.js" in resp.text


@pytest.mark.parametrize(
    "attack",
    [
        # 明文 ../ 会被 HTTP 客户端和代理提前归一化，到不了处理器；
        # 真正需要防的是编码后的形式——它原样穿过路由，直到我们自己解析。
        "html_testpreview/%2e%2e/%2e%2e/secret.txt",
        "html_testpreview/%2E%2E%2F%2E%2E%2Fsecret.txt",
        "html_testpreview/sub/%2e%2e/%2e%2e/%2e%2e/secret.txt",
    ],
)
def test_编码路径穿越被拦截为_403(preview_dir, attack):
    with TestClient(app) as client:
        resp = client.get(f"/api/static/{attack}")
    assert resp.status_code == 403
    assert "TOP SECRET" not in resp.text


def test_明文路径穿越到不了处理器(preview_dir):
    """明文 ../ 由客户端/路由层归一化掉，落到 404 而非泄露文件。"""
    with TestClient(app) as client:
        resp = client.get("/api/static/html_testpreview/../../secret.txt")
    assert resp.status_code == 404
    assert "TOP SECRET" not in resp.text


def test_注入脚本优先插在_body_前():
    html = "<html><body><p>x</p></body></html>"
    out = inject_editor_agent(html)
    assert out.index("visual-editor-agent.js") < out.index("</body>")


def test_重复注入不会叠加():
    once = inject_editor_agent("<html><body></body></html>")
    assert inject_editor_agent(once) == once
