"""构建自修复端到端评测 —— 移植 Java 版 BuildSelfHealEvalTest。

从零跑完整工作流直到 `npm run build` 成功，度量「首轮直通率」与「自修复后通过率」。
Java 基线：standard 组 10/10 首轮直通；complex 组 4/5 首轮直通、经 1 轮修复后 5/5。

    python -m evals.build_selfheal_eval [standard|complex|all]

注意：每例都会真跑 npm install + 生成，耗时以十分钟计，且会消耗大量 token。
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from app.agent.graph import execute_workflow

REPORT_DIR = Path(__file__).parent.parent / "docs" / "eval"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASE_TIMEOUT = 3600

# 常规组：典型 CRUD 型需求，用于度量首轮直通率
STANDARD_PROMPTS = [
    "用 Vue 做一个待办事项管理应用，支持任务增删改查、按分类筛选、完成状态切换和本地存储",
    "用 Vue 做一个个人记账本应用，可以记录每笔收支、按月份汇总统计并用图表展示",
    "用 Vue 做一个企业官网工程，多页面路由：首页、产品展示页、关于我们和联系表单页",
    "用 Vue 做一个博客前台应用，包含文章列表页、文章详情页和按标签筛选功能",
    "用 Vue 做一个天气查询应用，支持城市搜索、展示未来一周天气趋势和收藏常用城市",
    "用 Vue 做一个电商商品浏览应用，有商品列表、商品详情和购物车结算流程",
    "用 Vue 做一个任务看板应用，多列布局，支持卡片在不同状态列之间拖拽移动",
    "用 Vue 做一个在线简历生成器，左侧表单编辑个人信息和经历，右侧实时预览简历效果",
    "用 Vue 做一个图书管理系统前端，包含图书列表检索、借阅登记和归还管理",
    "用 Vue 做一个日程管理应用，月历视图展示日程，支持添加删除事件和按颜色分类",
]

# 高复杂度组：刻意选更容易一次构建失败的需求，用来真正压到自修复路径
COMPLEX_PROMPTS = [
    "用 Vue 做一个项目管理工具，包含甘特图时间轴视图、任务依赖关系连线和成员工时统计图表，"
    "需要多级嵌套路由（项目 → 迭代 → 任务详情）",
    "用 Vue + TypeScript 做一个可视化表单设计器，支持拖拽组件到画布、动态生成表单 schema、"
    "实时预览和 JSON 导入导出，右侧属性面板按组件类型动态渲染",
    "用 Vue 做一个多语言电商后台，商品管理支持规格 SKU 矩阵编辑（多维笛卡尔积展开）、"
    "批量导入导出和多图上传排序，并含权限路由守卫",
    "用 Vue 做一个在线代码编辑器，左侧文件树可增删重命名，中间编辑区支持多标签页和语法高亮，"
    "右侧 iframe 实时预览，并绑定常用快捷键",
    "用 Vue 做一个数据大屏，6 个联动图表（折线/饼图/地图/词云/漏斗/雷达），"
    "支持自适应分辨率缩放、定时轮询刷新和全屏切换",
]

GROUPS = {"standard": STANDARD_PROMPTS, "complex": COMPLEX_PROMPTS}


async def run_case(group: str, index: int, prompt: str) -> dict:
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(execute_workflow(prompt), timeout=CASE_TIMEOUT)
    except TimeoutError:
        return {"group": group, "index": index, "prompt": prompt, "success": False, "reason": "超时"}
    except Exception as e:
        return {"group": group, "index": index, "prompt": prompt, "success": False, "reason": f"异常: {e}"}

    elapsed = time.monotonic() - started
    retry_count = result.get("buildRetryCount", 0)
    return {
        "group": group,
        "index": index,
        "prompt": prompt,
        "appId": result.get("appId"),
        "generationType": result.get("generationType"),
        # buildRetryCount == 0 即首轮直通，没进过自修复回路
        "success": not result.get("buildErrorMessage"),
        "firstTry": retry_count == 0,
        "buildRetryCount": retry_count,
        "buildResultDir": result.get("buildResultDir"),
        "elapsedSec": round(elapsed, 1),
    }


async def main(group_name: str = "standard") -> None:
    if group_name == "all":
        groups = dict(GROUPS)
    elif group_name in GROUPS:
        groups = {group_name: GROUPS[group_name]}
    else:
        print(f"group 取值应为 standard / complex / all，实际: {group_name}")
        return

    results = []
    for group, prompts in groups.items():
        print(f"\n=== {group} 组，共 {len(prompts)} 例 ===")
        for i, prompt in enumerate(prompts, 1):
            print(f"[{group} {i}/{len(prompts)}] {prompt[:36]}…")
            record = await run_case(group, i, prompt)
            results.append(record)
            if record["success"]:
                mark = "首轮直通" if record.get("firstTry") else f"经 {record['buildRetryCount']} 轮修复"
                print(f"  [OK] 构建成功（{mark}），{record.get('elapsedSec')}s")
            else:
                print(f"  [FAIL] 构建失败：{record.get('reason', '重试耗尽')}")

    print("\n" + "=" * 50)
    for group in groups:
        rs = [r for r in results if r["group"] == group]
        ok = [r for r in rs if r["success"]]
        first = [r for r in ok if r.get("firstTry")]
        print(f"{group}: 构建成功 {len(ok)}/{len(rs)}，其中首轮直通 {len(first)}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    # 按组分文件写：跑 complex 不该把 standard 的明细冲掉，
    # 否则报告里引用的数字与磁盘上的产物对不上，事后无从复核。
    for group in groups:
        rows = [r for r in results if r["group"] == group]
        out = REPORT_DIR / f"build-eval-{group}.jsonl"
        with out.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n{group} 组明细已写入 {out}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "standard"))
