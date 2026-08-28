"""图片素材工具 —— 移植 langgraph4j/tools/。

四路来源：Pexels 内容图、undraw 插画、Mermaid 架构图（渲染后传 COS）、
DashScope 文生 Logo。

**全部可降级**：缺少对应的 API key 时直接返回空列表并记一条 warning，
绝不阻断主流程——素材是锦上添花，拿不到也应该能生成网站。
"""

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from app.core.config import settings
from app.llm.schemas import ImageCategory, ImageCollectionPlan, ImageResource
from app.media.cos import upload_file

logger = logging.getLogger(__name__)

PEXELS_API_URL = "https://api.pexels.com/v1/search"

# undraw 的搜索数据接口路径里带 Next.js 构建哈希，官方每次发版都会变。
# Java 版把哈希写死在代码里，结果早已 404（实测硬编码的 mMWmJSt23qpgo8cLTD_pB
# 已失效，当前是 9SMsYpCjXCftNdh3cu_8Q）——而且失败被当成「网络问题」静默降级，
# 插画功能其实一直是全废的。这里改成运行时从搜索页抓取并缓存，遇 404 刷新一次重试。
UNDRAW_SEARCH_PAGE = "https://undraw.co/search"
UNDRAW_DATA_URL = "https://undraw.co/_next/data/{build_id}/search/{q}.json?term={q}"
UNDRAW_BUILD_ID_PATTERN = re.compile(r'"buildId":"([^"]+)"')

SEARCH_COUNT = 12
HTTP_TIMEOUT = 10.0

_undraw_build_id: str | None = None
_undraw_lock = asyncio.Lock()


@asynccontextmanager
async def _http_client(**kwargs):
    """带 IPv4 回退的 HTTP 客户端。

    httpx 按 DNS 返回顺序逐个尝试地址，但**不做 Happy Eyeballs（RFC 8305）回退**：
    这些图床的 DNS 常把 IPv6 排在前面，而不少网络环境到它们的 IPv6 不通，
    于是 httpx 直接抛 ConnectError 而不去试后面的 IPv4 —— curl 同样的地址却一直正常，
    因为 curl 会做双栈竞速。表现就是「图片收集时灵时不灵」，随 DNS 顺序漂移。

    这里先按默认行为走（IPv6 可用时照常用），连接失败再用绑定 IPv4 的传输重试一次。
    """
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, **kwargs) as client:
        yield client


async def _get_with_fallback(url: str, **request_kwargs) -> httpx.Response:
    """发一个 GET，遇连接错误时用强制 IPv4 的传输重试一次。"""
    client_kwargs = {"follow_redirects": True}
    try:
        async with _http_client(**client_kwargs) as client:
            return await client.get(url, **request_kwargs)
    except httpx.ConnectError:
        logger.info("连接失败（多半是 IPv6 不通），改用 IPv4 重试: %s", url)
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with _http_client(transport=transport, **client_kwargs) as client:
            return await client.get(url, **request_kwargs)


@asynccontextmanager
async def _undraw_client():
    """undraw 一次搜索要打多个请求（抓 buildId + 若干候选词），共用一个连接池。

    先探一次连通性决定用不用 IPv4 传输——比每个请求各自重试省事，
    也避免 buildId 抓取成功、后续查询却失败的割裂状态。
    """
    kwargs: dict = {"follow_redirects": True}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, **kwargs) as probe:
            await probe.head(UNDRAW_SEARCH_PAGE)
    except httpx.ConnectError:
        logger.info("undraw 连接失败（多半是 IPv6 不通），改用 IPv4")
        kwargs["transport"] = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    except Exception:
        pass  # 非连接类错误交给真正的请求去暴露

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, **kwargs) as client:
        yield client



async def search_content_images(query: str) -> list[ImageResource]:
    """Pexels 内容图搜索。"""
    if not settings.pexels_api_key:
        logger.warning("未配置 PEXELS_API_KEY，跳过内容图搜索")
        return []
    try:
        resp = await _get_with_fallback(
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


async def _fetch_undraw_build_id(client: httpx.AsyncClient) -> str | None:
    """从 undraw 搜索页抓当前的 Next.js 构建哈希。"""
    resp = await client.get(UNDRAW_SEARCH_PAGE)
    resp.raise_for_status()
    match = UNDRAW_BUILD_ID_PATTERN.search(resp.text)
    if match is None:
        logger.warning("未能从 undraw 页面解析出 buildId，页面结构可能已变更")
        return None
    return match.group(1)


async def _get_undraw_build_id(client: httpx.AsyncClient, force_refresh: bool = False) -> str | None:
    global _undraw_build_id
    async with _undraw_lock:
        if _undraw_build_id is None or force_refresh:
            _undraw_build_id = await _fetch_undraw_build_id(client)
        return _undraw_build_id


async def _undraw_query(client: httpx.AsyncClient, term: str) -> list[dict]:
    """查一个词。命中 404 说明缓存的 buildId 已过期，刷新一次再重试。"""
    build_id = await _get_undraw_build_id(client)
    if build_id is None:
        return []

    resp = await client.get(UNDRAW_DATA_URL.format(build_id=build_id, q=term))
    if resp.status_code == 404:
        logger.info("undraw buildId 已过期，刷新后重试")
        build_id = await _get_undraw_build_id(client, force_refresh=True)
        if build_id is None:
            return []
        resp = await client.get(UNDRAW_DATA_URL.format(build_id=build_id, q=term))

    resp.raise_for_status()
    return resp.json().get("pageProps", {}).get("initialResults") or []


def _undraw_fallback_terms(query: str) -> list[str]:
    """把多词短语拆成候选单词。

    undraw 的搜索只对短词有效：实测 `coffee` 有 12 条，
    `coffee culture illustration` 是 0 条。而图片规划模型产出的是描述性长短语
    （对 Pexels 正好，对 undraw 全落空），所以要降级到单词重试。
    `illustration` 之类的词本身就是品类名，拿去搜没有意义，先剔掉。
    """
    noise = {"illustration", "illustrations", "icon", "image", "picture", "art"}
    words = [w for w in re.split(r"[\s,_-]+", query.lower()) if len(w) > 2 and w not in noise]
    # 保序去重，最多试 3 个，避免为一条插画打太多次请求
    seen: dict[str, None] = {}
    for w in words:
        seen.setdefault(w, None)
    return list(seen)[:3]


async def search_illustrations(query: str) -> list[ImageResource]:
    """undraw 插画搜索（爬 Next.js 数据接口，无需 key）。

    两层健壮性：构建哈希运行时抓取并在 404 时刷新；长短语搜不到就降级到单词重试。
    """
    try:
        async with _undraw_client() as client:
            results = await _undraw_query(client, query)
            if not results:
                for term in _undraw_fallback_terms(query):
                    if term == query.lower():
                        continue  # 单词查询已经试过，别重复打一次
                    results = await _undraw_query(client, term)
                    if results:
                        logger.info("undraw 长短语无结果，降级用 %r 命中 %d 条", term, len(results))
                        break
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
    tasks += [search_content_images(t.query) for t in plan.valid_content_image_tasks]
    tasks += [search_illustrations(t.query) for t in plan.valid_illustration_tasks]
    tasks += [generate_mermaid_diagram(t.mermaidCode, t.description) for t in plan.valid_diagram_tasks]
    tasks += [generate_logos(t.description) for t in plan.valid_logo_tasks]

    if not tasks:
        return []

    collected: list[ImageResource] = []
    for result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(result, BaseException):
            logger.error("图片收集子任务失败: %s", result)
            continue
        collected.extend(result)
    return collected
