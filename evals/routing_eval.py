"""路由准确率评测 —— 移植 Java 版 RoutingEvalTest。

数据集为 60 条人工标注的中文需求（HTML / MULTI_FILE / VUE_PROJECT 各 20 条），
重复 3 轮取均值。Java 基线：均值 97.8%（区间 95.0–100.0）。

    python -m evals.routing_eval [轮数]
"""

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from app.core.paths import CodeGenType
from app.llm.services import route_code_gen_type

EVAL_DIR = Path(__file__).parent
REPORT_DIR = EVAL_DIR.parent / "docs" / "eval"
DATASET = EVAL_DIR / "routing-eval-dataset.json"

# 数据集里的标签是 Java 枚举名，映射到本项目的枚举值
LABEL_TO_TYPE = {
    "HTML": CodeGenType.HTML,
    "MULTI_FILE": CodeGenType.MULTI_FILE,
    "VUE_PROJECT": CodeGenType.VUE_PROJECT,
}

# 并发度：太高会撞上 DeepSeek 的速率限制，反而拖慢整体
CONCURRENCY = 6


async def _one(sem: asyncio.Semaphore, prompt: str, expected: CodeGenType) -> dict:
    async with sem:
        actual = await route_code_gen_type(prompt)
    return {
        "prompt": prompt,
        "expected": expected.value,
        "actual": actual.value,
        "correct": actual is expected,
    }


async def run_round(dataset: list[dict], round_no: int) -> tuple[float, list[dict]]:
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(
        *(_one(sem, item["prompt"], LABEL_TO_TYPE[item["expected"]]) for item in dataset)
    )
    correct = sum(r["correct"] for r in results)
    accuracy = correct / len(results) * 100
    print(f"第 {round_no} 轮：{correct}/{len(results)} = {accuracy:.1f}%")
    return accuracy, list(results)


async def main(rounds: int = 3) -> None:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    print(f"数据集 {len(dataset)} 条，跑 {rounds} 轮\n")

    accuracies: list[float] = []
    all_results: list[dict] = []
    for i in range(1, rounds + 1):
        accuracy, results = await run_round(dataset, i)
        accuracies.append(accuracy)
        for r in results:
            all_results.append({**r, "round": i})

    mean = sum(accuracies) / len(accuracies)
    print(f"\n均值 {mean:.1f}%（区间 {min(accuracies):.1f}–{max(accuracies):.1f}）")

    # 混淆矩阵：看错分往哪个方向偏
    confusion = Counter((r["expected"], r["actual"]) for r in all_results if not r["correct"])
    if confusion:
        print("\n错分分布（期望 -> 实际：次数）")
        for (exp, act), count in confusion.most_common():
            print(f"  {exp} -> {act}: {count}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / "routing-eval-python.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in all_results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n明细已写入 {out}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
