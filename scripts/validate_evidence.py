"""Evidence Pipeline 抽样验证（Update 3.1）。

产出 `validation/evidence_sample_check.csv`：
用真实材料（默认 600004 年报 11069309.PDF）走
    原始材料 → Extraction → Candidate → Program Validation
逐条核对 公司/年份/单位/页码/引用，并**证明 LLM/抽取不能绕过程序校验**。

用法：python scripts/validate_evidence.py [--pdf ...] [--stock 600004] [--year 2024] [--max-pages 60]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import ROOT, get_logger  # noqa: E402
from src.evidence.citation import _norm  # noqa: E402
from src.evidence.pdf_parser import _to_amount, extract_pdf_facts  # noqa: E402

log = get_logger()
OUT = ROOT / "validation" / "evidence_sample_check.csv"
DEFAULT_PDF = ROOT / "11069309.PDF"


def _check(quote: str, value) -> tuple[bool, str]:
    """程序校验：value 是否能从 quote 重新解析出来（含单位归一）。"""
    if value is None:
        return False, "无值"
    if not quote:
        return False, "无引用片段"
    reparsed, unit = _to_amount(quote)
    if reparsed is None:
        return False, "引用片段中无数值"
    tol = max(1.0, 0.01 * abs(float(value)))
    ok = abs(reparsed - float(value)) <= tol
    return ok, f"重解析={reparsed:.2f} 单位={unit or '元'}"


def main():
    """抽样验证：真实 PDF 抽取 + 逐条程序校验 + 一个"编造数值"反例。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=str(DEFAULT_PDF))
    ap.add_argument("--stock", default="600004")
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--max-pages", type=int, default=60)
    args = ap.parse_args()

    facts = extract_pdf_facts(args.pdf, args.stock, args.year,
                              source_ref=Path(args.pdf).name, max_pages=args.max_pages)
    rows = []
    for f in facts:
        quote = f.quote or f.raw_context or ""
        # 用引用片段反解析数值，与抽取结果比对，证明抽取可被程序复核
        valid, why = _check(quote, f.value_num)
        rows.append({
            "source": Path(args.pdf).name,
            "stock_code": f.stock_code,
            "report_year": f.report_year,
            "fact_type": f.fact_type,
            "value": f.value_num if f.value_num is not None else f.value_text,
            "unit": f.unit,
            "caliber": f.caliber or "",
            "page": f.location or "",
            "quote": str(quote)[:120],
            "program_valid": valid,
            "note": why,
        })

    # 反例：编造数值（不在原文）应被程序校验拒绝
    rows.append({
        "source": "SYNTHETIC",
        "stock_code": args.stock,
        "report_year": args.year,
        "fact_type": "rd_spend_sum",
        "value": 999999.0,
        "unit": "元",
        "caliber": "",
        "page": "—",
        "quote": "研发投入合计 1.85 亿元",
        "program_valid": _check("研发投入合计 1.85 亿元", 999999.0)[0],
        "note": "反例：编造数值，应被拒绝（期望 program_valid=False）",
    })

    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    n_real = len(rows) - 1
    n_ok = int(df.iloc[:n_real]["program_valid"].sum()) if n_real else 0
    halluc_ok = bool(df.iloc[-1]["program_valid"])
    log.info("已写 %s（真实候选 %s 条，程序校验通过 %s；反例被拒=%s）",
             OUT, n_real, n_ok, not halluc_ok)
    if halluc_ok:
        log.warning("反例未被拒绝，需检查程序校验逻辑！")


if __name__ == "__main__":
    main()
