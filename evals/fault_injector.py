"""故障注入工具 —— 移植 Java 版 FaultInjector。

在已知可构建的基线 Vue 项目上做**确定性**编译期故障注入，用于测量自修复闭环的恢复率。

故障分类受基线构建脚本约束（只有 `vite build`，没有 vue-tsc 类型检查），
因此只注入 vite/rollup 能真实报错的编译期故障（语法 / 模块解析 / 样式）；
纯类型错误会被 esbuild 直接剥离，探针构建不失败，属于无效样本。
"""

import asyncio
import re
import shutil
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# 基线项目：Java 版评测用的同一个在线代码编辑器项目，全部锚点已探针验证过
BASELINE1 = "vue_project_1785320655264"

PROBE_TIMEOUT = 300

# 判定「注入确实让构建失败了」的报错特征
ERROR_PATTERN = re.compile(
    r"(error during build|SyntaxError|CssSyntaxError|Could not (load|resolve)|"
    r"is not exported|failed to resolve import|missing end tag|Unclosed block|ENOENT)",
    re.IGNORECASE,
)

ANSI_PATTERN = re.compile(r"\x1b?\[[;\d]*m")


class FaultCategory(StrEnum):
    T1_TEMPLATE_SYNTAX = "T1 模板语法"
    T2_SCRIPT_SYNTAX = "T2 脚本语法"
    T3_STYLE_SYNTAX = "T3 样式语法"
    I1_MISSING_LOCAL_IMPORT = "I1 本地模块缺失"
    I2_MISSING_NAMED_EXPORT = "I2 命名导出缺失(跨文件)"
    I3_MISSING_DEP = "I3 依赖缺失"


class Op(StrEnum):
    REPLACE = "REPLACE"
    DELETE = "DELETE"
    APPEND_AFTER = "APPEND_AFTER"
    REMOVE_EXPORT_FN = "REMOVE_EXPORT_FN"
    REMOVE_TRAILING_EXPORT = "REMOVE_TRAILING_EXPORT"


@dataclass(frozen=True)
class FaultSpec:
    id: str
    category: FaultCategory
    fault_file: str
    description: str
    cross_file: bool
    operator: Op
    find: str = ""
    replace: str = ""


@dataclass
class ProbeResult:
    success: bool
    error_prefix: str
    full_output: str


def baseline1_cold_cases() -> list[FaultSpec]:
    """基线 1 的 20 条冷记忆用例（17 个互异 + 3 个重复用于测方差）。"""
    C = FaultCategory
    return [
        # ---------- T1 模板语法 ----------
        FaultSpec("T1a", C.T1_TEMPLATE_SYNTAX, "src/App.vue",
                  "删除 .app-container 的闭合 div", False, Op.DELETE,
                  "<router-view />\n  </div>", ""),
        FaultSpec("T1b", C.T1_TEMPLATE_SYNTAX, "src/pages/Home.vue",
                  "删除 .home-page 的闭合 div", False, Op.DELETE,
                  "    </footer>\n  </div>\n</template>", "    </footer>\n</template>"),
        FaultSpec("T1c", C.T1_TEMPLATE_SYNTAX, "src/components/CodeEditor.vue",
                  "删除 .code-editor 根闭合 div", False, Op.DELETE,
                  "    </div>\n  </div>\n</template>", "    </div>\n</template>"),
        # ---------- T2 脚本语法 ----------
        FaultSpec("T2a", C.T2_SCRIPT_SYNTAX, "src/utils/store.js",
                  "删除 getAllFiles 函数体的左花括号", False, Op.DELETE,
                  "function getAllFiles() {", "function getAllFiles()"),
        FaultSpec("T2b", C.T2_SCRIPT_SYNTAX, "src/router/index.js",
                  "删除 createRouter 的入参左花括号", False, Op.DELETE,
                  "const router = createRouter({", "const router = createRouter("),
        FaultSpec("T2c", C.T2_SCRIPT_SYNTAX, "src/components/CodeEditor.vue",
                  "删除 currentContent computed 入参左花括号", False, Op.DELETE,
                  "const currentContent = computed(() => {", "const currentContent = computed(() =>"),
        # ---------- T3 样式语法 ----------
        FaultSpec("T3a", C.T3_STYLE_SYNTAX, "src/App.vue",
                  "删除 .app-container 样式块闭合花括号", False, Op.DELETE,
                  "  display: flex;\n  flex-direction: column;\n}",
                  "  display: flex;\n  flex-direction: column;"),
        FaultSpec("T3b", C.T3_STYLE_SYNTAX, "src/pages/Home.vue",
                  "删除 .footer 样式块闭合花括号", False, Op.DELETE,
                  "  border-top: 1px solid var(--home-border);\n  background: #ffffff;\n}",
                  "  border-top: 1px solid var(--home-border);\n  background: #ffffff;"),
        # ---------- I1 本地模块缺失 ----------
        FaultSpec("I1a", C.I1_MISSING_LOCAL_IMPORT, "src/router/index.js",
                  "Home.vue 导入路径拼错", False, Op.REPLACE,
                  "'@/pages/Home.vue'", "'@/pages/Homee.vue'"),
        FaultSpec("I1b", C.I1_MISSING_LOCAL_IMPORT, "src/router/index.js",
                  "Editor.vue 导入路径拼错", False, Op.REPLACE,
                  "'@/pages/Editor.vue'", "'@/pages/Editore.vue'"),
        FaultSpec("I1c", C.I1_MISSING_LOCAL_IMPORT, "src/main.js",
                  "global.css 导入路径拼错", False, Op.REPLACE,
                  "import './styles/global.css'", "import './styles/global.cs'"),
        FaultSpec("I1d", C.I1_MISSING_LOCAL_IMPORT, "src/components/CodeEditor.vue",
                  "store.js 导入路径拼错", False, Op.REPLACE,
                  "from '@/utils/store.js'", "from '@/utils/stoer.js'"),
        # ---------- I2 命名导出缺失（跨文件） ----------
        FaultSpec("I2a", C.I2_MISSING_NAMED_EXPORT, "src/utils/highlight.js",
                  "删除 highlight 导出函数（CodeEditor.vue 仍在引用）", True, Op.REMOVE_EXPORT_FN,
                  "highlight", ""),
        FaultSpec("I2b", C.I2_MISSING_NAMED_EXPORT, "src/utils/store.js",
                  "删除末尾 export 块（CodeEditor.vue 仍在引用）", True, Op.REMOVE_TRAILING_EXPORT, "", ""),
        # ---------- I3 依赖缺失 ----------
        FaultSpec("I3a", C.I3_MISSING_DEP, "src/App.vue",
                  "引入未安装的 lodash", False, Op.APPEND_AFTER,
                  "<script setup>", "import _ from 'lodash'"),
        FaultSpec("I3b", C.I3_MISSING_DEP, "src/main.js",
                  "引入未安装的 axios", False, Op.APPEND_AFTER,
                  "import router from './router'", "import axios from 'axios'"),
        FaultSpec("I3c", C.I3_MISSING_DEP, "src/components/CodeEditor.vue",
                  "引入未安装的 axios", False, Op.APPEND_AFTER,
                  "import { highlight } from '@/utils/highlight.js'", "import axios from 'axios'"),
        # ---------- 重复用例，测随机方差 ----------
        FaultSpec("T1a-r1", C.T1_TEMPLATE_SYNTAX, "src/App.vue",
                  "重复：删除 .app-container 的闭合 div", False, Op.DELETE,
                  "<router-view />\n  </div>", ""),
        FaultSpec("I1a-r1", C.I1_MISSING_LOCAL_IMPORT, "src/router/index.js",
                  "重复：Home.vue 导入路径拼错", False, Op.REPLACE,
                  "'@/pages/Home.vue'", "'@/pages/Homee.vue'"),
        FaultSpec("I3a-r1", C.I3_MISSING_DEP, "src/App.vue",
                  "重复：引入未安装的 lodash", False, Op.APPEND_AFTER,
                  "<script setup>", "import _ from 'lodash'"),
    ]


# ---------------------------------------------------------------- 注入手术


def _replace_once(content: str, find: str, replace: str) -> str | None:
    if not find:
        return None
    idx = content.find(find)
    if idx < 0:
        return None
    return content[:idx] + replace + content[idx + len(find):]


def _append_line_after(content: str, anchor: str, new_line: str) -> str | None:
    if not anchor:
        return None
    idx = content.find(anchor)
    if idx < 0:
        return None
    line_end = content.find("\n", idx)
    if line_end < 0:
        line_end = len(content)
    return content[: line_end + 1] + new_line + "\n" + content[line_end + 1:]


def _remove_export_function(content: str, name: str) -> str | None:
    """删掉整个 `export function <name>(...) {...}` 块。

    引用方仍在 import 它 → rollup 报 not exported，构成跨文件根因故障。
    """
    start = content.find(f"export function {name}(")
    if start < 0:
        return None
    open_idx = content.find("{", start)
    if open_idx < 0:
        return None
    depth = 0
    i = open_idx
    while i < len(content):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0:
        return None
    return content[:start] + f"// [fault injected: export {name} removed]\n" + content[i + 1:]


def _remove_trailing_export_block(content: str) -> str | None:
    idx = content.rfind("\nexport {")
    if idx < 0:
        return None
    return content[:idx] + "\n// [fault injected: export block removed]\n"


def inject(project_dir: Path, spec: FaultSpec) -> bool:
    """按规格注入故障。锚点未命中返回 False（该样本作废，不计入分母）。"""
    file = project_dir / spec.fault_file
    if not file.is_file():
        print(f"  注入失败：文件不存在 {spec.fault_file}")
        return False
    content = file.read_text(encoding="utf-8")

    if spec.operator is Op.REPLACE or spec.operator is Op.DELETE:
        mutated = _replace_once(content, spec.find, spec.replace)
    elif spec.operator is Op.APPEND_AFTER:
        mutated = _append_line_after(content, spec.find, spec.replace)
    elif spec.operator is Op.REMOVE_EXPORT_FN:
        mutated = _remove_export_function(content, spec.find)
    else:
        mutated = _remove_trailing_export_block(content)

    if mutated is None or mutated == content:
        print(f"  注入失败：锚点未命中 [{spec.id}] {spec.fault_file}")
        return False

    file.write_text(mutated, encoding="utf-8")
    return True


# ---------------------------------------------------------------- 探针构建


def _npm() -> str:
    return "npm.cmd" if sys.platform == "win32" else "npm"


async def probe_build(project_dir: Path) -> ProbeResult:
    """直接跑 npm run build。

    这是**有效性门禁**：注入后构建必须失败，否则说明这个故障根本没被编译器捕获
    （典型如纯类型错误被 esbuild 剥离），该样本无效、必须剔除，
    否则「自修复成功率」会被一堆本来就能构建的样本灌水。
    """
    try:
        process = await asyncio.create_subprocess_exec(
            _npm(), "run", "build",
            cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=PROBE_TIMEOUT)
    except TimeoutError:
        return ProbeResult(False, "probe timeout", "")
    except OSError as e:
        return ProbeResult(False, f"probe error: {e}", "")

    output = stdout.decode(errors="replace")
    return ProbeResult(process.returncode == 0, extract_error_prefix(output), output)


def extract_error_prefix(output: str) -> str:
    for line in output.splitlines():
        trimmed = line.strip()
        if trimmed and ERROR_PATTERN.search(trimmed):
            clean = ANSI_PATTERN.sub("", trimmed)
            return clean[:120] + "…" if len(clean) > 120 else clean
    first = output.splitlines()[0] if output.splitlines() else ""
    return first[:120] + "…" if len(first) > 120 else first


# ---------------------------------------------------------------- 项目复制


def copy_project(baseline: Path, target: Path, include_node_modules: bool = False) -> bool:
    """把基线复制成注入副本。默认不带 node_modules/dist —— 复用父级共享依赖。"""
    skip = {"dist", ".git"}
    if not include_node_modules:
        skip.add("node_modules")
    try:
        target.mkdir(parents=True, exist_ok=True)
        for src in baseline.rglob("*"):
            rel = src.relative_to(baseline)
            if skip & set(rel.parts):
                continue
            dst = target / rel
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        return True
    except OSError as e:
        print(f"  复制基线失败: {e}")
        return False
