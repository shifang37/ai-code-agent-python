"""结构化输出返回 None 的处理，以及 undraw 长短语降级。

这两个都是「静默失效」类缺陷：不抛错、不报警，功能就是不干活。
图片收集曾因此全废——`plan_image_collection` 返回 None，节点里的
`None.contentImageTasks` 抛 AttributeError，又被节点的宽异常捕获吞掉，
最终表现只是「收集到 0 张图」，看起来像网络问题。
"""

import pytest

from app.core.paths import CodeGenType
from app.llm import services
from app.llm.schemas import ImageCollectionPlan, QualityResult
from app.media.images import _undraw_fallback_terms


class _NoneModel:
    """模拟模型不调工具、直接回文本时 with_structured_output 返回 None 的情形。"""

    async def ainvoke(self, _messages):
        return None


@pytest.fixture
def none_model(monkeypatch):
    monkeypatch.setattr(services, "_structured", lambda model, schema: _NoneModel())


async def test_路由拿到_none_时回落_html(none_model):
    assert await services.route_code_gen_type("做个页面") is CodeGenType.HTML


async def test_质检拿到_none_时放行(none_model):
    """与节点的异常分支保持一致：裁判失灵就放行，交给真实编译器裁决。"""
    result = await services.check_code_quality("<html></html>")
    assert isinstance(result, QualityResult)
    assert result.isValid is True


async def test_图片计划拿到_none_时返回空计划(none_model):
    plan = await services.plan_image_collection("做个咖啡店官网")
    # 必须是空计划而不是 None——调用方会直接访问它的属性
    assert isinstance(plan, ImageCollectionPlan)
    assert plan.contentImageTasks == []
    assert plan.illustrationTasks == []


# ---------------------------------------------------------------- undraw 降级


def test_长短语拆成候选单词():
    """undraw 只对短词有效，长短语必须降级重试。"""
    assert _undraw_fallback_terms("coffee culture illustration") == ["coffee", "culture"]


def test_品类噪音词被剔除():
    """illustration / icon 这类词是品类名，拿去搜没有意义。"""
    terms = _undraw_fallback_terms("team illustration icon image")
    assert "illustration" not in terms
    assert "icon" not in terms
    assert terms == ["team"]


def test_候选词去重且限量():
    terms = _undraw_fallback_terms("coffee coffee shop team barista cafe")
    assert terms == ["coffee", "shop", "team"]  # 保序去重，最多 3 个


def test_过短的词被跳过():
    assert _undraw_fallback_terms("a of go team") == ["team"]
