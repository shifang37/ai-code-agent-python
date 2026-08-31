"""故障注入自修复评测 —— 移植 Java 版 FaultInjectionSelfHealEvalTest。

流程（cold 臂，度量「无生成记忆」时的能力下界）：
    复制基线 → 注入确定性故障 → 探针构建必须失败（有效性门禁）
    → 驱动修复子图 → 看最终能否构建成功、用了几轮

Java 基线：cold 305/305 = 100% 恢复率。

    python -m evals.fault_injection_eval [用例数]

依赖：基线项目与共享 node_modules 需在 tmp/code_output/ 下就位（见 --prepare）。
"""

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

from app.agent.graph import execute_repair, next_app_id
from app.core.paths import CODE_OUTPUT_ROOT_DIR, CodeGenType, code_output_dir
from evals import fault_injector as FI

REPORT_DIR = Path(__file__).parent.parent / "docs" / "eval"

# Windows 控制台默认 GBK，评测输出含中文与进度符号，先把 stdout 切到 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 单个样本的整体超时（含多轮 LLM 修复 + 构建）
CASE_TIMEOUT = 2700


def prepare_shared_node_modules(baseline_dir: Path) -> bool:
    """把基线的 node_modules 提到 code_output 根下共享。

    Node 的模块解析会向上逐级查找 node_modules，所以放在父目录即可被所有注入副本复用。
    这样每个样本就能跳过 npm install（数分钟）——评测才跑得动。
    """
    shared = CODE_OUTPUT_ROOT_DIR / "node_modules"
    if shared.is_dir():
        return True
    source = baseline_dir / "node_modules"
    if not source.is_dir():
        print(f"基线缺少 node_modules，请先在 {baseline_dir} 执行 npm install")
        return False
    print("正在准备共享 node_modules（首次较慢）…")
    shutil.copytree(source, shared)
    return True


async def run_case(spec: FI.FaultSpec, baseline_dir: Path, keep_dirs: bool) -> dict:
    app_id = next_app_id()
    target = code_output_dir(CodeGenType.VUE_PROJECT, app_id)
    record: dict = {
        "id": spec.id,
        "category": spec.category.name,
        "categoryLabel": spec.category.value,
        "faultFile": spec.fault_file,
        "description": spec.description,
        "crossFile": spec.cross_file,
        "appId": app_id,
    }

    try:
        if not FI.copy_project(baseline_dir, target):
            return {**record, "valid": False, "invalidReason": "复制基线失败"}

        if not FI.inject(target, spec):
            return {**record, "valid": False, "invalidReason": "注入锚点未命中"}

        # 有效性门禁：注入后必须真的构建失败，否则样本无效
        probe = await FI.probe_build(target)
        if probe.success:
            return {**record, "valid": False, "invalidReason": "注入后构建仍成功（故障未被编译器捕获）"}
        record["injectedError"] = probe.error_prefix

        seed = {
            "original_prompt": "（故障注入评测：仅驱动自修复回路）",
            "enhanced_prompt": "（故障注入评测：仅驱动自修复回路）",
            "app_id": app_id,
            "generation_type": CodeGenType.VUE_PROJECT,
            "generated_code_dir": str(target),
            "build_retry_count": 0,
            "build_error_message": None,
            "quality_result": None,
            "quality_retry_count": 0,
            "image_list": [],
            "build_error_history": [],
            "fix_prompt_related_files_by_round": [],
        }

        started = time.monotonic()
        result = await asyncio.wait_for(execute_repair(seed), timeout=CASE_TIMEOUT)
        elapsed = time.monotonic() - started

        recovered = not result.get("buildErrorMessage")
        rounds = result.get("buildRetryCount", 0)
        return {
            **record,
            "valid": True,
            "recovered": recovered,
            "rounds": rounds,
            "elapsedSec": round(elapsed, 1),
            "buildErrorHistory": result.get("buildErrorHistory", []),
        }
    except TimeoutError:
        return {**record, "valid": True, "recovered": False, "rounds": -1, "invalidReason": "超时"}
    except Exception as e:
        return {**record, "valid": False, "invalidReason": f"异常: {e}"}
    finally:
        if not keep_dirs and target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


async def main(limit: int | None = None) -> None:
    baseline_dir = CODE_OUTPUT_ROOT_DIR / FI.BASELINE1
    if not baseline_dir.is_dir():
        print(f"基线项目不存在: {baseline_dir}")
        print("请把 Java 版的基线项目复制过来，或改用你自己的可构建 Vue 项目。")
        return
    if not prepare_shared_node_modules(baseline_dir):
        return

    # 复用共享 node_modules，跳过每例的 npm install
    os.environ["FAULT_EVAL_SKIP_INSTALL"] = "true"
    keep_dirs = os.environ.get("FAULT_EVAL_KEEP_DIRS", "").lower() == "true"

    cases = FI.baseline1_cold_cases()
    if limit:
        cases = cases[:limit]
    print(f"故障注入自修复评测（cold 臂），共 {len(cases)} 例\n")

    results = []
    for i, spec in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {spec.id} {spec.category.value} — {spec.description}")
        record = await run_case(spec, baseline_dir, keep_dirs)
        results.append(record)
        if not record.get("valid"):
            print(f"  [SKIP] 无效样本：{record.get('invalidReason')}")
        elif record["recovered"]:
            print(f"  [OK] 恢复成功，{record['rounds']} 轮，{record.get('elapsedSec')}s")
        else:
            print(f"  [FAIL] 未恢复（{record['rounds']} 轮）")

    valid = [r for r in results if r.get("valid")]
    recovered = [r for r in valid if r.get("recovered")]
    print("\n" + "=" * 50)
    print(f"有效样本 {len(valid)}/{len(results)}，恢复 {len(recovered)}/{len(valid)}")
    if valid:
        print(f"恢复率 {len(recovered) / len(valid) * 100:.1f}%")
    if recovered:
        rounds = [r["rounds"] for r in recovered]
        print(f"平均修复轮次 {sum(rounds) / len(rounds):.2f}（最多 {max(rounds)}）")

    # 按类别拆分，看哪类故障最难修
    by_category: dict[str, list] = {}
    for r in valid:
        by_category.setdefault(r["categoryLabel"], []).append(r)
    print("\n分类别：")
    for label, rs in sorted(by_category.items()):
        n_ok = sum(1 for r in rs if r.get("recovered"))
        print(f"  {label}: {n_ok}/{len(rs)}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    # 只跑了一部分用例时写到单独的文件：调试性的小批量重跑不该覆盖完整跑的明细，
    # 否则报告里的数字与磁盘产物对不上，事后无从复核。
    name = "fault-injection.jsonl" if limit is None else "fault-injection-partial.jsonl"
    out = REPORT_DIR / name
    with out.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n明细已写入 {out}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else None))
