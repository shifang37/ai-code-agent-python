"""封面截图服务 —— 移植 service/impl/ScreenshotServiceImpl + WebScreenshotUtils。

生成完成后截取应用预览主页作为封面。**完全可降级**：
未安装 selenium 或没有 COS 配置时直接跳过，不影响生成流程。
"""

import asyncio
import logging
import uuid
from pathlib import Path

from app.core.paths import CODE_PREVIEW_HOST, SCREENSHOT_ROOT_DIR, CodeGenType, code_output_dir
from app.media.cos import upload_file

logger = logging.getLogger(__name__)

WINDOW_SIZE = (1600, 900)
PAGE_LOAD_TIMEOUT = 30


def _capture(url: str, output: Path) -> bool:
    """同步截图。selenium 是可选依赖，缺失时静默跳过。"""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        logger.warning("未安装 selenium（pip install '.[screenshot]'），跳过封面截图")
        return False

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument(f"--window-size={WINDOW_SIZE[0]},{WINDOW_SIZE[1]}")

    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.get(url)
        output.parent.mkdir(parents=True, exist_ok=True)
        return driver.save_screenshot(str(output))
    except Exception as e:
        logger.warning("截图失败 %s: %s", url, e)
        return False
    finally:
        if driver is not None:
            driver.quit()


def preview_url_for(gen_type: CodeGenType, app_id: int) -> str | None:
    """算出可截图的预览地址；产物不存在则返回 None。"""
    dir_name = f"{gen_type.value}_{app_id}"
    source = code_output_dir(gen_type, app_id)
    if gen_type is CodeGenType.VUE_PROJECT:
        # Vue 工程生成完即已构建，截构建产物页
        if not (source / "dist" / "index.html").exists():
            logger.warning("Vue 项目构建产物不存在，跳过封面截图，appId: %s", app_id)
            return None
        return f"{CODE_PREVIEW_HOST}/{dir_name}/dist/index.html"
    if not (source / "index.html").exists():
        return None
    return f"{CODE_PREVIEW_HOST}/{dir_name}/index.html"


async def generate_app_cover(gen_type: CodeGenType, app_id: int) -> str | None:
    """截图并上传 COS，返回封面 URL。任一环节不可用都返回 None，绝不抛错。"""
    url = preview_url_for(gen_type, app_id)
    if url is None:
        return None

    output = SCREENSHOT_ROOT_DIR / f"{uuid.uuid4().hex}.png"
    if not await asyncio.to_thread(_capture, url, output):
        return None

    try:
        return await upload_file(f"/cover/{output.name}", output)
    finally:
        output.unlink(missing_ok=True)
