"""PDF 候选事实抽取 CLI（数字版 PDF；候选 -> 人工确认）。

用法：
  python scripts/ingest_pdf.py --pdf "2024年报.pdf" --stock 000063 --year 2024
  python scripts/ingest_pdf.py --pdf "2024年报.pdf" --stock 000063 --year 2024 --commit
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import get_logger  # noqa: E402
from src.evidence.pdf_parser import extract_pdf_facts  # noqa: E402
from src.evidence.store import EvidenceStore  # noqa: E402

log = get_logger()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--stock", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--ref", default=None)
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--commit", action="store_true", help="写入证据库（candidate，待人工确认）")
    args = ap.parse_args()

    facts = extract_pdf_facts(args.pdf, args.stock, args.year, source_ref=args.ref or "",
                              max_pages=args.max_pages)
    if not facts:
        print("（未抽取到候选事实）")
        return
    for e in facts:
        print(f"{e.fact_type:<20} "
              f"{(e.value_num if e.value_num is not None else e.value_text)!s:>18} "
              f"{e.unit or ''} | {e.source_ref} | {e.extraction_method} | "
              f"caliber={e.caliber or '-'} | conf={e.extraction_confidence}")

    if args.commit:
        st = EvidenceStore()
        ids = [st.add(e) for e in facts]
        log.info("已写入 %s 条候选事实（candidate），请用 review_evidence.py 确认", len(ids))
        for i in ids[:5]:
            log.info("  %s", i)


if __name__ == "__main__":
    main()
