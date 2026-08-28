"""图片素材工具 —— 移植 langgraph4j/tools/。

四路来源：Pexels 内容图、undraw 插画、Mermaid 架构图（渲染后传 COS）、
DashScope 文生 Logo。

**全部可降级**：缺少对应的 API key 时直接返回空列表并记一条 warning，
绝不阻断主流程——素材是锦上添花，拿不到也应该能生成网站。
"""

import asyncio
import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import httpx

from app.core.config import settings
from app.llm.schemas import ImageCategory, ImageCollectionPlan, ImageResource
from app.media.cos import upload_file

logger = logging.getLogger(__name__)

PEXELS_API_URL = "https://api.pexels.com/v1/search"
# undraw 这个接口带 Next.js 构建哈希，官方一发版就会失效——
# 拿不到结果属预期内，静默降级即可，不必当故障处理。
UNDRAW_API_URL = "https://undraw.co/_next/data/mMWmJSt23qpgo8cLTD_pB/search/{q}.json?term={q}"

SEARCH_COUNT = 12
HTTP_TIMEOUT = 10.0


async def search_content_images(query: str) -> list[ImageResource]:
    """Pexels 内容图搜索。"""
    if not settings.pexels_api_key:
        logger.warning("未配置 PEXELS_API_KEY，跳过内容图搜索")
        return []
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                PEXELS_API_URL,
                headers={"Authorization": settings.pexels_api_key},
                params={"query": query, "per_page": SEARCH_COUNT, "page": 1},
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
    except Exception as e:
        logger.error("Pexels API 调用失败: %s", e)
        return []

    return [
        ImageResource(
            category=ImageCategory.CONTENT,
            description=photo.get("alt") or query,
            url=photo["src"]["medium"],
        )
        for photo in photos
        if photo.get("src", {}).get("medium")
    ]


async def search_illustrations(query: str) -> list[ImageResource]:
    """undraw 插画搜索（爬 Next.js 数据接口，无需 key）。"""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(UNDRAW_API_URL.format(q=query))
            resp.raise_for_status()
            results = resp.json().get("pageProps", {}).get("initialResults") or []
    except Exception as e:
        logger.error("搜索插画失败：%s", e)
        return []

    return [
        ImageResource(
            category=ImageCategory.ILLUSTRATION,
            description=item.get("title") or "插画",
            url=item["media"],
        )
        for item in results[:SEARCH_COUNT]
        if item.get("media")
    ]


async def generate_mermaid_diagram(mermaid_code: str, description: str) -> list[ImageResource]:
    """用 mermaid-cli 渲染成 SVG 再传 COS。缺 mmdc 或缺 COS 配置时降级为空。"""
    if not mermaid_code.strip():
        return []
    if not settings.cos_enabled:
        logger.warning("未配置 COS，跳过架构图生成（渲染出的 SVG 无处存放）")
        return []

    mmdc = shutil.which("mmdc") or shutil.which("mmdc.cmd")
    if not mmdc:
        logger.warning("未安装 mermaid-cli（mmdc），跳过架构图生成")
        return []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        input_file = tmp_dir / "diagram.mmd"
        output_file = tmp_dir / "diagram.svg"
        input_file.write_text(mermaid_code, encoding="utf-8")
        try:
            await asyncio.to_thread(
                subprocess.run,
                [mmdc, "-i", str(input_file), "-o", str(output_file), "-b", "transparent"],
                check=True,
                capture_output=True,
                timeout=60,
            )
            if not output_file.exists() or output_file.stat().st_size == 0:
                logger.error("Mermaid CLI 执行失败：未产出 SVG")
                return []
            key = f"/mermaid/{uuid.uuid4().hex[:5]}/{output_file.name}"
            url = await upload_file(key, output_file)
        except Exception as e:
            logger.error("生成架构图失败: %s", e)
            return []

    if not url:
        return []
    return [ImageResource(category=ImageCategory.ARCHITECTURE, description=description, url=url)]


async def generate_logos(description: str) -> list[ImageResource]:
    """DashScope 文生图做 Logo。"""
    if not settings.dashscope_api_key:
        logger.warning("未配置 DASHSCOPE_API_KEY，跳过 Logo 生成")
        return []
    try:
        from dashscope import ImageSynthesis
    except ImportError:
        logger.warning("未安装 dashscope（pip install '.[media]'），跳过 Logo 生成")
        return []

    prompt = f"生成 Logo，Logo 中禁止包含任何文字！Logo 介绍：{description}"
    try:
        result = await asyncio.to_thread(
            ImageSynthesis.call,
            api_key=settings.dashscope_api_key,
            model=settings.dashscope_image_model,
            prompt=prompt,
            size="512*512",
            n=1,  # 生成 1 张足够，AI 并不知道哪张更好
        )
    except Exception as e:
        logger.error("生成 Logo 失败: %s", e)
        return []

    if result is None or result.output is None or not result.output.results:
        return []
    return [
        ImageResource(category=ImageCategory.LOGO, description=description, url=item["url"])
        for item in result.output.results
        if item.get("url")
    ]


async def collect_images(plan: ImageCollectionPlan) -> list[ImageResource]:
    """按计划并发执行四类收集任务。

    `return_exceptions=True`：任何一路挂掉都不该拖垮其余三路，
    与 Java 版逐个 CompletableFuture 吞异常的行为一致。
    """
    tasks = []
    tasks += [search_content_images(t.query) for t in plan.contentImageTasks]
    tasks += [search_illustrations(t.query) for t in plan.illustrationTasks]
    tasks += [generate_mermaid_diagram(t.mermaidCode, t.description) for t in plan.diagramTasks]
    tasks += [generate_logos(t.description) for t in plan.logoTasks]

    if not tasks:
        return []

    collected: list[ImageResource] = []
    for result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(result, BaseException):
            logger.error("图片收集子任务失败: %s", result)
            continue
        collected.extend(result)
    return collected
