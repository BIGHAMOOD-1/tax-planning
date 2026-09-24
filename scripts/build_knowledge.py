"""构建/更新知识库索引（政策库 + 案例库）。

用法:
    python scripts/build_knowledge.py                 # 增量构建/更新（复用已有向量）
    python scripts/build_knowledge.py --rebuild       # 删除索引后全量重建
    python scripts/build_knowledge.py --backend local # 强制本地 TF-IDF
    python scripts/build_knowledge.py --limit 500     # 仅向量化前 500 个待处理 chunk（测试用）
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import CONFIG, get_logger

log = get_logger()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["auto", "local", "embedding"], default="auto")
    ap.add_argument("--rebuild", action="store_true", help="删除现有索引后全量重建")
    ap.add_argument("--limit", type=int, default=None, help="仅处理前 N 个待向量化 chunk")
    args = ap.parse_args()

    out_dir = Path(CONFIG["rag"]["index_dir"])
    if args.rebuild and out_dir.exists():
        shutil.rmtree(out_dir)
        log.info("已删除旧索引 %s", out_dir)

    from src.rag.store import KnowledgeBase
    kb = KnowledgeBase.build(backend=args.backend, limit=args.limit)
    log.info("政策库 chunks: %s | 统计 %s", len(kb.policy.chunks), kb.policy.stats)
    log.info("案例库 chunks: %s | 统计 %s", len(kb.case.chunks), kb.case.stats)
    log.info("检索后端: policy=%s, case=%s", kb.policy.backend, kb.case.backend)

    for q in ["研发费用加计扣除比例", "高新技术企业优惠", "固定资产一次性扣除"]:
        res = kb.search(q, "policy", top_k=2)
        log.info("检索[%s] -> %s 条", q, len(res))
        for r in res:
            log.info("   %.3f | %s | %s", r["score"], r.get("doc_no", "") or "-", r.get("title", "")[:44])


if __name__ == "__main__":
    main()
