"""结构化输出 schema —— 对齐 Java 版 @Description 标注的模型类。

字段名保持驼峰（htmlCode / isValid …），因为提示词里给的 JSON 示例就是驼峰，
换成蛇形会和提示词对不上。Python 侧用 alias 访问蛇形属性。
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.paths import CodeGenType


class HtmlCodeResult(BaseModel):
    """生成 HTML 代码文件的结果"""

    htmlCode: str = Field(description="HTML代码")
    description: str = Field(default="", description="生成代码的描述")


class MultiFileCodeResult(BaseModel):
    """生成多个代码文件的结果"""

    htmlCode: str = Field(description="HTML代码")
    cssCode: str = Field(default="", description="CSS代码")
    jsCode: str = Field(default="", description="JS代码")
    description: str = Field(default="", description="生成代码的描述")


class QualityResult(BaseModel):
    """代码质量检查结果"""

    isValid: bool = Field(description="是否通过质检")
    errors: list[str] = Field(default_factory=list, description="错误列表")
    suggestions: list[str] = Field(default_factory=list, description="改进建议")


class RoutingResult(BaseModel):
    """代码生成类型路由结果"""

    codeGenType: CodeGenType = Field(description="推荐的代码生成类型")


# ---- 图片素材收集计划 ----


class ImageSearchTask(BaseModel):
    query: str = Field(description="内容图片搜索关键词")


class IllustrationTask(BaseModel):
    query: str = Field(description="插画搜索关键词")


class DiagramTask(BaseModel):
    mermaidCode: str = Field(description="Mermaid 图表代码")
    description: str = Field(default="", description="图表说明")


class LogoTask(BaseModel):
    description: str = Field(description="Logo 描述")


class ImageCollectionPlan(BaseModel):
    """图片素材收集计划"""

    contentImageTasks: list[ImageSearchTask] = Field(default_factory=list)
    illustrationTasks: list[IllustrationTask] = Field(default_factory=list)
    diagramTasks: list[DiagramTask] = Field(default_factory=list)
    logoTasks: list[LogoTask] = Field(default_factory=list)


class ImageCategory(StrEnum):
    CONTENT = "CONTENT"
    LOGO = "LOGO"
    ILLUSTRATION = "ILLUSTRATION"
    ARCHITECTURE = "ARCHITECTURE"

    @property
    def text(self) -> str:
        return {
            ImageCategory.CONTENT: "内容图片",
            ImageCategory.LOGO: "LOGO图片",
            ImageCategory.ILLUSTRATION: "插画图片",
            ImageCategory.ARCHITECTURE: "架构图片",
        }[self]


class ImageResource(BaseModel):
    """一条图片素材，供 prompt_enhancer 拼进增强提示词。"""

    category: ImageCategory
    description: str = ""
    url: str
