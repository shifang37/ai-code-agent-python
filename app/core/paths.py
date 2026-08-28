"""路径常量与代码生成类型枚举 —— 对齐 Java 版 AppConstant / CodeGenTypeEnum。"""

from enum import StrEnum
from pathlib import Path

# 项目根目录（本文件位于 app/core/paths.py，向上两级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

CODE_OUTPUT_ROOT_DIR = PROJECT_ROOT / "tmp" / "code_output"
CODE_DEPLOY_ROOT_DIR = PROJECT_ROOT / "tmp" / "code_deploy"
SCREENSHOT_ROOT_DIR = PROJECT_ROOT / "tmp" / "screenshots"

CODE_DEPLOY_HOST = "http://localhost"
CODE_PREVIEW_HOST = "http://localhost:8123/api/static"

GOOD_APP_PRIORITY = 99
DEFAULT_APP_PRIORITY = 0

USER_LOGIN_STATE = "user_login"
DEFAULT_ROLE = "user"
ADMIN_ROLE = "admin"


class CodeGenType(StrEnum):
    HTML = "html"
    MULTI_FILE = "multi_file"
    VUE_PROJECT = "vue_project"

    @property
    def text(self) -> str:
        return {
            CodeGenType.HTML: "原生 HTML 模式",
            CodeGenType.MULTI_FILE: "原生多文件模式",
            CodeGenType.VUE_PROJECT: "Vue 工程模式",
        }[self]

    @classmethod
    def from_value(cls, value: str | None) -> "CodeGenType | None":
        if not value:
            return None
        try:
            return cls(value)
        except ValueError:
            return None


def code_output_dir(gen_type: CodeGenType, app_id: int) -> Path:
    """生成目录的唯一真源：`tmp/code_output/{type}_{appId}`。

    自修复闭环每一轮都按这个公式重算目录，修复子图入口也据此校验种子，
    否则第二轮起会去修另一棵目录树。
    """
    return CODE_OUTPUT_ROOT_DIR / f"{gen_type.value}_{app_id}"


def ensure_dirs() -> None:
    for d in (CODE_OUTPUT_ROOT_DIR, CODE_DEPLOY_ROOT_DIR, SCREENSHOT_ROOT_DIR):
        d.mkdir(parents=True, exist_ok=True)
