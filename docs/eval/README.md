# Python 重构版评测报告

对照基准：Java 版（langchain4j + langgraph4j）的同名评测。数据集、故障规格、
判定口径全部沿用，因此两列数字可直接比较。

模型：DeepSeek（`deepseek-chat` 路由 / `deepseek-reasoner` 代码生成），2026-08-28。

## 1. 路由准确率

60 条人工标注中文需求（HTML / MULTI_FILE / VUE_PROJECT 各 20 条），重复 3 轮。

| | Java 基线 | Python |
|---|---|---|
| 均值 | 97.8% | **98.3%** |
| 区间 | 95.0–100.0 | 98.3–98.3 |

三轮结果完全一致（59/60），错分全部是 `multi_file → vue_project` 同一条样本，
说明分类边界稳定、无随机抖动。

复现：`python -m evals.routing_eval 3`

### 迁移中发现并修复的问题

DeepSeek 不支持 OpenAI 的 `json_schema` 响应格式，直接返回
`400 This response_format type is unavailable now`。而路由服务对异常是
**fail-open 回落 HTML** 的，于是症状表现为「所有需求都被判成 HTML 模式」——
准确率 33%，但没有任何报错冒到调用方。

修复：所有结构化输出统一走 `with_structured_output(schema, method="function_calling")`
（`app/llm/services.py`）。这条约束已写进代码注释，避免后人改回默认值。

## 2. 故障注入自修复（cold 臂）

在已知可构建的基线 Vue 项目上注入确定性编译期故障，经有效性门禁
（注入后探针构建必须失败）后驱动修复子图。

| | Java 基线 | Python |
|---|---|---|
| 恢复率 | 305/305 = 100% | **20/20 = 100%** |
| 平均修复轮次 | 多数 1 轮 | 1.00（最多 1） |
| 无效样本 | 5（均为 I3，API 侧失败） | 0 |

分类别（Python）：

| 类别 | 结果 |
|---|---|
| T1 模板语法 | 4/4 |
| T2 脚本语法 | 3/3 |
| T3 样式语法 | 2/2 |
| I1 本地模块缺失 | 5/5 |
| I2 命名导出缺失（跨文件） | 2/2 |
| I3 依赖缺失 | 4/4 |

单例耗时 6–19 秒（复用共享 `node_modules`、跳过 `npm install`）。

复现：`python -m evals.fault_injection_eval`

**样本数说明**：Java 版 305 例是 20 条故障规格 × 多轮重复累积的结果；这里跑的是
单轮 20 例。规格集、注入手术、有效性门禁完全一致，但样本量小一个数量级，
置信区间相应更宽——它证明的是「闭环在全部 6 类故障上都能工作」，
而不是「恢复率精确等于 100%」。

**修复真实性抽查**：T1a（删除 `App.vue` 闭合 div）修复后与基线做 diff，模型并非
还原原文，而是写出了自己的版本（把 `router-view` 包进 `<main class="app-main">`
并补了配套样式规则），`dist/` 正常产出。确认是 LLM 经 `writeFile` 工具真实修复，
不是任何形式的缓存或回滚。

## 3. 构建端到端（build-selfheal）

脚本已移植（`evals/build_selfheal_eval.py`，standard 10 例 / complex 5 例，
提示词与 Java 版逐字一致），**本次未执行** —— 每例都要真跑 `npm install` +
完整生成，耗时以十分钟计。

复现：`python -m evals.build_selfheal_eval standard`

## 4. 未覆盖项

- **faithful 臂**（先完整生成再注入，用于对照「生成记忆」带来的增益）脚本未移植，
  cold 臂已足以度量能力下界。
- **图片素材工具**在本次评测环境中无法访问外网（Pexels / undraw 均 ConnectError），
  降级路径按预期生效：记录 warning、返回空列表、主流程继续。缺 key 或断网时的
  行为已验证，但工具本身的正确性未在真实网络下回归。
