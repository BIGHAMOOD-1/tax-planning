"""政策元数据抽取（Update 6.4 · 三层来源之第三层）。

对被分析引用过的政策，用 LLM 抽取候选元数据（生效/失效、适用主体、层级、条件），
写入 `config/policy_metadata.suggested.yaml`（**候选**，人工校验后再并入正式表）。
**绝不直接写政策库**。

用法：
    python scripts/extract_policy_metadata.py --dry           # 仅列出被引用的政策
    python scripts/extract_policy_metadata.py --limit 20      # 抽取前 20 条候选
    python scripts/extract_policy_metadata.py --apply         # 人工校验后，把候选并入正式表
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from src.common import ROOT, get_logger  # noqa: E402

log = get_logger()
OUTPUTS = ROOT / "outputs"
SUGGESTED = ROOT / "config" / "policy_metadata.suggested.yaml"
FORMAL = ROOT / "config" / "policy_metadata.yaml"


def cited_policies() -> dict[str, dict]:
    """收集所有分析产物中"被引用过"的政策（按文号去重）。"""
    out: dict[str, dict] = {}
    for fp in OUTPUTS.glob("*_*/plans/*.json"):
        try:
            d = json.loads(fp.read_text(encoding="utf-8"), strict=False)
        except Exception:  # noqa: BLE001
            continue
        for p in ((d.get("lineage") or {}).get("data_pool") or {}).get("policies") or []:
            doc_no = str(p.get("doc_no") or "").strip()
            if not doc_no:
                continue
            out.setdefault(doc_no, {"doc_no": doc_no, "title": p.get("title"),
                                    "date": p.get("date"), "text": p.get("text") or p.get("excerpt") or ""})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry", action="store_true", help="只列出被引用的政策，不调用 LLM")
    ap.add_argument("--apply", action="store_true", help="把 suggested 候选并入正式表（人工校验后）")
    args = ap.parse_args()

    if args.apply:
        if not SUGGESTED.exists():
            log.error("未找到 %s", SUGGESTED)
            sys.exit(1)
        sug = yaml.safe_load(SUGGESTED.read_text(encoding="utf-8")) or {}
        formal = yaml.safe_load(FORMAL.read_text(encoding="utf-8")) if FORMAL.exists() else {"policies": {}}
        formal.setdefault("policies", {})
        n = 0
        for doc_no, meta in (sug.get("policies") or {}).items():
            formal["policies"].setdefault(doc_no, meta)
            n += 1
        FORMAL.write_text(yaml.safe_dump(formal, allow_unicode=True, sort_keys=False), encoding="utf-8")
        log.info("已并入正式表 %s 条 -> %s", n, FORMAL)
        return

    pols = cited_policies()
    log.info("被引用的政策（去重）共 %s 条", len(pols))
    for doc_no, p in list(pols.items())[:args.limit]:
        log.info("  - %s %s（%s）", doc_no, p.get("title"), p.get("date"))
    if args.dry:
        return

    from src.evidence.policy_meta import SOURCE_LLM, extract_candidates
    out: dict = {}
    for doc_no, p in list(pols.items())[:args.limit]:
        try:
            cand = extract_candidates(p.get("text") or "", doc_no=doc_no)
        except Exception as exc:  # noqa: BLE001
            log.warning("抽取失败 %s：%s", doc_no, str(exc)[:120])
            continue
        if not cand:
            continue
        cand["title"] = p.get("title")
        cand.setdefault("source", SOURCE_LLM)
        out[doc_no] = cand
        log.info("  候选 %s -> %s", doc_no, {k: cand.get(k) for k in ("effective_from", "subject", "level")})
    SUGGESTED.write_text(yaml.safe_dump({"policies": out}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    log.info("候选已写入 %s（共 %s 条）。人工校验后运行 --apply 并入正式表。", SUGGESTED, len(out))


if __name__ == "__main__":
    main()
