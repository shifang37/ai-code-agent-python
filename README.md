# ai-code-agent

一句话描述需求，AI 生成可运行的前端项目 —— 并且**能自己修好构建错误**。

基于 Python / FastAPI / LangChain / LangGraph 实现。

## 它是什么

给一句自然语言需求（"做一个待办事项应用，支持分类筛选和本地存储"），服务会：

1. 判断该用哪种形态生成 —— 单页 HTML、多文件静态站，还是完整的 Vue 工程；
2. 收集配套图片素材（内容图 / 插画 / Logo / 架构图），拼进提示词；
3. 生成代码并落盘；Vue 工程走工具调用循环，由模型逐个文件写入；
4. 跑**真实的** `npm install && npm run build`；
5. **构建失败就自己修** —— 把编译器报错和涉及的源文件回喂给模型做定点修复，最多 3 轮；
6. 产物可在线预览、一键部署、打包下载。

全程流式推送，前端能实时看到模型正在写哪个文件。

## 核心：由编译器驱动的自修复闭环

```
START → image_collector → prompt_enhancer → router → code_generator
      → code_quality_check ─┬─ build ──→ project_builder ─┬─ success → END
                            ├─ skip_build → END           ├─ retry → code_generator
                            └─ fail → code_generator      └─ abort → END
```

这是整个项目最有价值的部分。关键在于**信号来源是真实编译器，而不是让模型自我评价**：

- `project_builder` 跑真的 npm 构建，失败时把合并后的 stdout+stderr 尾部（截断 5000 字）
  写回状态 —— 报错总在日志末尾，所以截断必须保留尾部；
- 条件边把流程路由回 `code_generator`，后者构造定点修复提示词：编译报错原文 +
  **从报错中解析出的相关源文件当前内容**（最多 3 个，各截断 4000 字），
  并明确要求「不要重写整个项目，只修改导致报错的文件」；
- 重试上限 3 轮，耗尽则以源码目录作为最终结果。

跨文件故障也能修。实测中出现过这样一例：报错指向 `Login.vue`，
真正缺失的却是 `i18n.js` 里的 `export function t` —— 模型定位到了正确的文件并补上导出。

质检回路（LLM 裁判）也在图里，但上限只有 2 轮：裁判可能持续判不合格，
超限就放行进入构建阶段，交给编译器给终局判断。

## 快速开始

前置：Python 3.11+、Node.js（构建 Vue 工程用）、MySQL、Redis（可选，仅用于限流）。

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -e ".[dev]"

cp .env.example .env        # 至少填 LLM_API_KEY 和数据库连接
python -m uvicorn app.main:app --port 8123 --reload
```

接口文档：`http://localhost:8123/docs`，全部端点挂在 `/api` 前缀下。

可选依赖按需安装 —— 缺失时对应功能自动跳过，不影响主流程：

```bash
pip install -e ".[media]"       # 图片素材：COS 上传、DashScope 文生 Logo
pip install -e ".[screenshot]"  # 应用封面截图
```

## 生成模式

路由模型会根据需求自动选择：

| 模式 | 产出 | 适用 |
|---|---|---|
| `html` | 单个 `index.html`（样式脚本内联） | 落地页、倒计时、简历页这类单页 |
| `multi_file` | `index.html` + `style.css` + `script.js` | 需要分离关注点的静态站 |
| `vue_project` | 完整 Vue3 + Vite 工程，走工具调用循环逐文件写入 | 多页面、带路由和组件化的应用 |

只有 `vue_project` 会进入构建与自修复流程；前两种是静态产物，生成即完成。

## 接口

统一响应信封 `{code, data, message}`，`code == 0` 为成功。会话基于签名 Cookie。

| 端点 | 说明 |
|---|---|
| `POST /api/user/register` `/login` `/logout` | 用户体系 |
| `POST /api/app/add` | 建应用（自动路由生成模式） |
| `GET /api/app/chat/gen/code` | **SSE 流式生成**，限流 5 次 / 60 秒 |
| `POST /api/app/deploy` · `GET /api/app/download/{id}` | 部署 / 打包下载 |
| `GET /api/static/{key}/**` | 预览生成结果（沙箱隔离） |
| `GET /api/chatHistory/app/{id}` | 对话历史（游标分页） |
| `POST /api/workflow/execute` · `GET /api/workflow/execute-flux` | 直接驱动工作流 |

另有一组 `/admin/*` 管理端点。共 31 个，完整列表见 `/docs`。

## 架构

```
app/
├─ main.py              FastAPI 入口，/api 前缀，统一响应信封
├─ core/                配置、错误码、路径常量与 CodeGenType
├─ db/                  SQLAlchemy 模型（user / app / chat_history）
├─ llm/
│  ├─ models.py         四个 ChatOpenAI 工厂（对话 / 流式 / 推理 / 路由）
│  ├─ prompts/          7 个中文系统提示词
│  ├─ schemas.py        结构化输出 schema
│  ├─ services.py       智能路由 / 代码质检 / 图片规划
│  └─ guardrails.py     输入安全护栏（长度、敏感词、注入模式）
├─ agent/
│  ├─ state.py          工作流状态（TypedDict + reducer）
│  ├─ nodes.py          6 个节点 + 修复提示词构造
│  ├─ graph.py          图定义、条件边、执行入口
│  ├─ tools.py          5 个文件工具（读写改删 + 目录树）
│  ├─ codegen.py        按模式分派生成 + 流式事件
│  ├─ code_saver.py     代码解析与落盘
│  └─ builder.py        npm 构建（自修复的信号源）
├─ api/                 路由层 + 依赖项（登录态、限流）
├─ media/               图片素材工具（全部可降级）
└─ services/            用户 / 应用 / 对话历史 / 截图
evals/                  三套评测（见 docs/eval/README.md）
tests/                  57 个单元测试
```

几个设计要点：

- **状态是扁平 TypedDict + reducer**。累积字段（构建报错历史等）用 `operator.add` 合并，
  节点只返回增量 —— 避免"就地改了对象但没随状态快照生效"这类陷阱。
- **对话记忆交给 LangGraph checkpointer**，`thread_id = appId`。
  `chat_history` 表只负责给前端做展示分页，不兼任记忆源。
- **appId 是唯一真源**。它同时决定对话记忆的隔离键与产出目录名
  （`tmp/code_output/{type}_{appId}`），自修复每轮都按同一公式重算目录，
  否则第二轮起会去修另一棵目录树。
- **提示词绝不经模板渲染**。用户输入里的 `{{ }}`（Vue mustache 很常见）
  一旦被当成模板变量解析就会直接报错，所以内容只作为 `HumanMessage` 字面量传入。
- **结构化输出统一走 function_calling**。部分模型不支持 `json_schema` 响应格式，
  且失败时上层往往是 fail-open 降级，症状会表现为"功能安静地不工作"。

## 配置

全部通过环境变量 / `.env` 注入，密钥不入代码库。

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` `LLM_BASE_URL` | 必填。默认指向 DeepSeek 的 OpenAI 兼容接口 |
| `LLM_CHAT_MODEL` `LLM_REASONING_MODEL` `LLM_ROUTING_MODEL` | 分别用于通用对话、代码生成、意图路由 |
| `DB_*` | MySQL 连接 |
| `REDIS_*` | 限流用；不可用时自动降级为进程内限流 |
| `PEXELS_API_KEY` `DASHSCOPE_API_KEY` `COS_*` | 图片素材相关，全部可选 |

完整清单见 `.env.example`。

## 安全

这个服务会执行 LLM 生成的、未经审查的代码，所以边界要划清楚。

- **预览沙箱**。`/api/static/**` 是唯一把生成的 HTML/JS 直接投给浏览器的出口，两道防线：
  路径穿越校验（越界 403），以及 CSP `sandbox`（**不含 `allow-same-origin`**，
  使预览页处于 opaque origin，读不到主站 Cookie / localStorage，也无法以登录态调用 API）。
  因为 iframe 已跨源，可视化编辑改走 postMessage 协议。
- **文件工具边界**。工具入参是模型生成的不可信输入，一律校验解析后仍在项目根内，
  越界直接拒绝 —— 否则模型写出 `../../` 就能读写项目外的文件。
- **构建收敛**。`vite.config.js` 本身就是模型写的代码，`npm run build` 会执行它，
  所以构建阶段必然在宿主机跑不可信 JS。代码层面做了三层收敛：
  1. `npm install --ignore-scripts` —— 掐掉任意 npm 包的 postinstall，
     这是"装个包即在宿主机执行任意命令"的主入口；
  2. **环境变量白名单** —— 子进程默认继承父进程全部环境变量（含 API Key、数据库口令），
     一句 `process.env.LLM_API_KEY` 就能读走外传；现只透传 node/npm 必需的 21 个；
  3. 超时强杀。

  **残留风险要说清楚**：构建仍在宿主机执行不可信 JS，可读写当前用户有权访问的文件、
  发起网络请求。要真正封住需要容器 / 虚拟机 / 独立低权限账户级别的隔离 ——
  那是部署形态的事，代码层面做不到，生产环境务必补上。
- **输入护栏**。用户原始输入过长度、敏感词与注入模式检查。
  内部管道消息（携带编译报错的修复提示词）不适用这套规则，否则会被长度上限误伤。
- **凭据管理**。所有密钥只从环境变量读取，`.env` 已在 `.gitignore` 中。
  若历史上曾把真实密钥提交进版本库，应当全部轮换 —— 删文件不等于删历史。

## 评测

三套量化评测，脚本在 `evals/`，报告在 [docs/eval/README.md](docs/eval/README.md)。

| 评测 | 方法 | 结果 |
|---|---|---|
| 意图路由准确率 | 60 条人工标注需求 × 3 轮 | 98.3% |
| 故障注入自修复 | 6 类编译期故障，注入后须先构建失败方计入 | 20/20 恢复，多数 1 轮 |
| 构建端到端 · standard | 10 条常规需求，从零生成到构建成功 | 10/10，全部首轮直通 |
| 构建端到端 · complex | 5 条高复杂度需求 | 5/5，其中 2 例经 1 轮自修复 |

```bash
python -m pytest                          # 单元测试
python -m evals.routing_eval 3            # 路由准确率
python -m evals.fault_injection_eval      # 故障注入自修复
python -m evals.build_selfheal_eval all   # 构建端到端（耗时以十分钟计）
```

故障注入评测需要一个已知可构建的基线 Vue 项目放在 `tmp/code_output/` 下，
详见 `evals/fault_injector.py` 顶部说明。

## 开发

```bash
python -m pytest        # 57 个单元测试，不需要网络和数据库
python -m ruff check app evals tests
```

测试重点盯住几处容易悄悄失效的地方：预览沙箱的两道防线、自修复的条件边与报错文件定位、
构建子进程的密钥隔离、结构化输出返回空值时的降级。这些缺陷的共同特征是**不抛错**，
只是功能默默不干活 —— 没有测试就会一直没人发现。
