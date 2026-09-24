"""单企业税务筹划分析入口。

用法:
    python scripts/run_plan.py --stock 000002 --year 2024
    python scripts/run_plan.py --stock 000002 --year 2024 --no-llm
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import CONFIG, get_logger

log = get_logger()


def main():
    """命令行入口：解析参数 -> 运行完整流水线 -> 打印摘要（可选 JSON）。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", required=True, help="6 位股票代码")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true", help="强制规则模式")
    ap.add_argument("--top-k", type=int, default=None, help="每个 query 检索条数（默认读 config.rag.top_k）")
    ap.add_argument("--rounds", type=int, default=None, help="红蓝辩论轮次（默认读 config.debate.rounds）")
    ap.add_argument("--json", action="store_true", help="打印完整 JSON")
    args = ap.parse_args()

    # 延迟导入：避免仅查看帮助时加载重型依赖
    from src.pipeline.plan_pipeline import run_plan
    result = run_plan(args.stock, args.year, use_llm=not args.no_llm,
                      top_k=args.top_k, rounds=args.rounds)

    log.info("=" * 60)
    log.info("企业：%s %s（%s）", result["name"], result["stock_code"], result["industry"])
    log.info("方向来源：%s", result["opportunity_source"])
    log.info("-" * 60)
    for p in result["plans"]:
        log.info("方向：%s", p.get("direction"))
        log.info("  结论：%s (%s)", p.get("final_decision"), p.get("decision_code") or "-")
        log.info("  预计税务影响：%s%s", p.get("estimated_tax_impact"),
                 "（估算）" if p.get("amount_is_estimate") else "")
        lin = p.get("lineage", {})
        if lin:
            log.info("  政策依据：%s", [x.get("doc_no") or x.get("title")
                                        for x in lin.get("policies", [])][:3])
            log.info("  计算：%s", (lin.get("calculation") or {}).get("results"))
            log.info("  缺口：%s", lin.get("data_gaps"))
            if lin.get("evidence_required"):
                log.info("  待补充证据：%s 项", len(lin["evidence_required"]))
    log.info("-" * 60)
    log.info("汇总：%s", result["summary"])
    out_dir = Path(CONFIG["paths"]["outputs"]) / f"{result['stock_code']}_{result['year']}"
    log.info("输出目录：%s", out_dir)
    log.info("  - report.md（人工阅读）")
    log.info("  - profile.json / run_meta.json")
    log.info("  - plan_*.json（逐方向证据链）")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
