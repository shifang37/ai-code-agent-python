# ai-code-agent（Python / LangChain / LangGraph）

AI 代码生成 Agent 的 Python 重构版，与 Java 版（Spring Boot + langchain4j +
langgraph4j）**接口对等**：现有 Vue 前端不改一行即可切过来，且共用同一套
MySQL 表，两版可并存切换。

## 核心：带自修复闭环的代码生成工作流

```
START → image_collector → prompt_enhancer → router → code_generator
      → code_quality_check ─┬─ build ──→ project_builder ─┬─ success → END
                            ├─ skip_build → END           ├─ retry → code_generator
                            └─ fail → code_generator      └─ abort → END
```

`project_builder` 跑的是**真实的** `npm install && npm run build`。失败时把合并后的
编译输出写回状态，条件边路由回 `code_generator`，后者带着编译报错 + 报错涉及的
源文件当前内容构造定点修复提示词（「只修改导致报错的文件」），最多 3 轮。
这条闭环由真实编译器信号驱动，不是 LLM 自评——也是整个项目最有价值的部分。

## 快速开始

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -e ".[dev]"

cp .env.example .env        # 至少填 LLM_API_KEY 和数据库连接
python -m uvicorn app.main:app --port 8123 --reload
```

访问 `http://localhost:8123/docs` 查看接口文档。前端把 API 基址指到
`http://localhost:8123/api` 即可。

可选依赖按需装（缺失时对应功能自动降级跳过，不影响主流程）：

```bash
pip install -e ".[media]"       # 图片素材：COS 上传、DashScope 文生图
pip install -e ".[screenshot]"  # 应用封面截图
```

## 目录结构

```
app/
├─ main.py              FastAPI 入口，/api 前缀，统一 {code,data,message} 信封
├─ core/                配置、响应信封与错误码、路径常量与 CodeGenType
├─ db/                  SQLAlchemy 模型（映射既有 user/app/chat_history 表）
├─ llm/
│  ├─ models.py         四个 ChatOpenAI 工厂（DeepSeek 兼容接口）
│  ├─ prompts/          7 个中文提示词，原样复制自 Java 版
│  ├─ schemas.py        结构化输出 schema
│  ├─ services.py       路由 / 质检 / 图片规划
│  └─ guardrails.py     输入安全护栏
├─ agent/
│  ├─ state.py          WorkflowState + reducer
│  ├─ nodes.py          6 个节点 + 修复提示词构造
│  ├─ graph.py          图定义、条件边、执行入口
│  ├─ tools.py          5 个文件工具
│  ├─ codegen.py        按类型分派生成 + 流式
│  ├─ code_saver.py     代码解析与落盘
│  └─ builder.py        npm 构建（自修复的信号源）
├─ api/                 路由层 + 依赖项（登录态、限流）
├─ media/               图片素材工具（全部可降级）
└─ services/            用户 / 应用 / 对话历史 / 截图
evals/                  三套评测（见 docs/eval/README.md）
tests/                  42 个单元测试
```

## 相对 Java 版的改动

| | Java | Python | 原因 |
|---|---|---|---|
| 状态管理 | 整个 POJO 塞进单个 state key 就地 mutate | 扁平 TypedDict + `operator.add` reducer | Java 版注释里明确警告「节点外 append 会被状态快照丢弃」，改成 reducer 后这个陷阱不复存在 |
| 对话记忆 | Redis 记忆存储 + 从 MySQL 手工倒灌最近 20 条 | LangGraph checkpointer，`thread_id = appId` | 省掉「跳过最新一条再 reverse」那段易错逻辑；`chat_history` 表回归纯展示用途 |
| 流式工具调用 | shadow 了 8 个 langchain4j 内部类才拿到分片 | LangChain 原生支持 | 补丁在 Python 生态里没有必要 |
| 质检回路 | 无重试上限，理论上可无限循环 | `MAX_QUALITY_RETRIES = 2`，超限放行交编译器裁决 | Java 版的已知缺陷，评测因此默认关掉质检 |
| 文件工具 | 相对路径直接解析，无边界校验 | 强制校验最终路径仍在项目根内 | 模型写出 `../` 就能改到项目外 |
| 密钥 | 真实 key 提交进 git | 全部走环境变量，`.env` 已 gitignore | 见下方安全提示 |

## 安全

- **预览沙箱**：`/api/static/**` 是唯一把 LLM 生成的 HTML/JS 直接投给浏览器的出口，
  两道防线——路径穿越校验（403）与 CSP `sandbox`（**不含 `allow-same-origin`**，
  使预览页处于 opaque origin，读不到主站 Cookie / localStorage）。
  `tests/test_preview_sandbox.py` 锁住这两条。
- **构建沙箱缺失**：`npm install` / `npm run build` 以普通子进程在服务进程的权限下运行，
  没有容器或 jail。任意 npm 包的 postinstall 脚本会在宿主机执行。
  这一点与 Java 版相同，生产部署前应补上隔离。
- **密钥轮换**：Java 版仓库里提交过真实的 DeepSeek / Pexels / DashScope / 腾讯云 COS
  凭据（`application-local.yml`）。它们已进入 git 历史，**应当全部轮换**。

## 评测

| | Java 基线 | Python |
|---|---|---|
| 路由准确率 | 97.8% | 98.3% |
| 故障注入自修复（cold） | 305/305 = 100% | 20/20 = 100% |

详见 [docs/eval/README.md](docs/eval/README.md)，含样本量差异与复现命令。

```bash
python -m pytest                              # 单元测试
python -m evals.routing_eval 3                # 路由准确率
python -m evals.fault_injection_eval          # 故障注入自修复
python -m evals.build_selfheal_eval standard  # 构建端到端（耗时以十分钟计）
```
