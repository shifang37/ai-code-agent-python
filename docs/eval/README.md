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

## 3. 构建端到端（build-selfheal，standard 组）

从零跑完整工作流直到 `npm run build` 成功，10 条典型 CRUD 型 Vue 需求。

| | Java 基线 | Python |
|---|---|---|
| 构建成功 | 10/10 | **10/10** |
| 首轮直通 | 10/10 | **10/10** |

单例耗时 108–237 秒（含真实 `npm install` + 完整生成 + 构建）。
逐个核对产物：10 个项目均产出 `dist/index.html` 与打包 JS，源码 9–17 个文件。

这一轮跑在**加固后的构建路径**上（`--ignore-scripts` + 环境变量白名单），
因此也顺带验证了沙箱加固没有破坏正常构建。

复现：`python -m evals.build_selfheal_eval standard`

## 4. 图片素材收集

初次评测时报告为「本机访问不到 Pexels/undraw，降级路径生效」——**这个结论是错的**，
复查后是四个真实缺陷，均已修复：

1. **图片收集计划返回 `None`**。`with_structured_output` 在模型不调工具、直接回文本时
   返回 None 而非抛错；节点里 `None.contentImageTasks` 抛的 AttributeError 又被
   节点自身的宽异常捕获吞掉，最终只表现为「收集到 0 张图」。三处结构化输出调用
   现已全部显式处理 None（`app/llm/services.py`）。
2. **undraw 接口的构建哈希已过期**。Java 版把 Next.js buildId 硬编码在 URL 里
   （`mMWmJSt23qpgo8cLTD_pB`），官方发版后变成 `9SMsYpCjXCftNdh3cu_8Q`，请求恒 404 ——
   也就是说这个功能在 Java 版里一直是全废的，只是失败被当成网络问题静默降级了。
   现改为运行时从搜索页抓取 buildId 并缓存，遇 404 刷新一次重试。
3. **一条填错的子任务废掉整个计划**。模型给 `diagramTasks` 填的是
   `{"query": "收入支出统计流程图"}`，而 schema 要求 `mermaidCode`；子任务字段是必填时，
   pydantic 会让**整个计划**校验失败，内容图、插画、Logo 全部一起丢。
   现在子任务字段全部可选，由 `valid_*` 属性逐条过滤，坏任务只影响它自己。
4. **httpx 不做 Happy Eyeballs 回退**。这些图床的 DNS 常把 IPv6 排在前面，而本机到
   它们的 IPv6 不通，httpx 直接抛 ConnectError 而不去试后面的 IPv4（curl 会做双栈竞速，
   所以 curl 一直正常）。表现是「时灵时不灵」，随 DNS 顺序漂移。
   现在连接失败会用绑定 IPv4 的传输重试一次。

另外 undraw 的搜索只对短词有效（`coffee` 有 12 条，`coffee culture illustration`
是 0 条），而规划模型产出的是描述性长短语（对 Pexels 正合适）。已加单词级降级重试。

修复后实测（记账本需求，即当初触发计划校验失败的那条）：**0 张 → 82 张**
（48 张 Pexels 内容图 + 34 张 undraw 插画）。
Logo 一路需要 `pip install '.[media]'` 装 dashscope，未装时按预期跳过。

回归测试见 `tests/test_structured_output_fallbacks.py`。

## 5. 未覆盖项

- **faithful 臂**（先完整生成再注入，用于对照「生成记忆」带来的增益）脚本未移植，
  cold 臂已足以度量能力下界。
- **complex 组**（5 条高复杂度需求，Java 基线 4/5 首轮直通、经 1 轮修复后 5/5）未执行。
  复现：`python -m evals.build_selfheal_eval complex`
- **Mermaid 架构图**需要本机装 `mmdc` 且配置 COS，未验证。
