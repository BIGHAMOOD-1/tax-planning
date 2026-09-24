"""Evidence 评审 CLI（后端与界面解耦，未来 UI 复用同一状态机）。

用法：
  python scripts/review_evidence.py add --stock 000063 --year 2024 --type RD_PROJECT --value "研发项目A" --ref "2024年报P87"
  python scripts/review_evidence.py pending
  python scripts/review_evidence.py show EV202609170001
  python scripts/review_evidence.py act EV202609170001 a
  python scripts/review_evidence.py act EV202609170001 e --value 30000000
  python scripts/review_evidence.py conflicts
  python scripts/review_evidence.py solutions --status candidate
  python scripts/review_evidence.py promote SOL... --status accepted
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import get_logger  # noqa: E402
from src.evidence import review as review_mod  # noqa: E402
from src.evidence.conflict import detect  # noqa: E402
from src.evidence.ingest import manual_fact  # noqa: E402
from src.evidence.store import EvidenceStore  # noqa: E402

log = get_logger()


def cmd_add(store, a):
    """手动录入一条证据（默认 candidate，待人工确认）。"""
    ev = manual_fact(a.stock, a.year, a.type, a.value, unit=a.unit or "",
                     caliber=a.caliber or "", source_ref=a.ref or "用户直接输入",
                     raw_context=a.value, company_name=a.name or "",
                     report_year=a.year)
    store.add(ev)
    val = ev.value_num if ev.value_num is not None else ev.value_text
    log.info("已录入 %s：%s %s %s = %s %s（%s）",
             ev.evidence_id, ev.stock_code, ev.year, ev.fact_type, val, ev.unit,
             ev.verification_status)


def cmd_resolve(store, a):
    """按优先级规则把证据解析进画像，打印采用/覆盖/冲突明细。"""
    from src.pipeline.plan_pipeline import load_profile
    from src.evidence.resolution import resolve_profile
    p = load_profile(a.stock, a.year)
    # persist=True 时把解析结果写回；默认只预览
    _, res = resolve_profile(p, store=store, persist=a.persist)
    print(f"采用 {len(res['adopted'])} / 覆盖 {len(res['superseded'])} / 冲突 {len(res['conflicts'])}")
    for r in res["adopted"]:
        print(f"  [采用] {r['field']} = {r['value']}  <- {r['source_type']}({r['precedence']})")
    for r in res["superseded"]:
        print(f"  [覆盖] {r['field']}: {r['old_value']}({r['old_source']}) -> "
              f"{r['value']}({r['source_type']})  旧值已留档")
    for r in res["conflicts"]:
        print(f"  [冲突] {r['field']}: 画像={r['profile_value']} 证据={r['value']} "
              f"({r['source_type']}) 未覆盖")


def cmd_pending(store, a):
    """列出待评审证据。"""
    rows = review_mod.pending(store)
    if not rows:
        print("（无待评审证据）")
        return
    for r in rows:
        val = r["value_num"] if r.get("value_num") is not None else r.get("value_text")
        print(f"[{r['evidence_id']}] {r['verification_status']:<9} {r['fact_type']:<20} "
              f"{r['stock_code']} {r['report_year'] or r['year']} = {val} {r['unit'] or ''} "
              f"| {r['source_type']}/{r.get('doc_type') or '-'} {r['source_ref']}")


def cmd_show(store, a):
    """按 evidence_id 查看单条证据详情。"""
    r = store.get(a.evidence_id)
    print(r or "未找到")


def cmd_act(store, a):
    """对证据执行审核动作（如 a=采纳、e=修正值）。"""
    review_mod.act(store, a.evidence_id, a.action, new_value=a.value)


def cmd_conflicts(store, a):
    """运行多来源冲突检测并打印结果；mark=True 时把冲突项标记入库。"""
    rows = detect(store, stock_code=a.stock, year=a.year, mark=a.mark)
    if not rows:
        print("（无多来源冲突）")
        return
    for r in rows:
        print(f"{r['status']:<16} {r['fact_type']} {r['stock_code']} {r['year']} | {r['reason']}")
        for v in r["values"]:
            print(f"    - {v['source_type']}: {v['value']} {v['unit'] or ''} ({v['caliber']})")


def cmd_solutions(store, a):
    """列出方案库条目（可按企业与审核状态过滤）。"""
    rows = store.list_solutions(stock_code=a.stock, review_status=a.status)
    for r in rows:
        print(f"[{r['solution_id']}] {r['review_status']:<10} {r['stock_code']} {r['year']} "
              f"{r['direction']} | {r['final_decision']}")


def cmd_promote(store, a):
    """人工审定方案：更新审核状态并记录备注。"""
    from src.evidence.solution_kb import promote
    promote(store, a.solution_id, a.status, {"note": a.note or "", "reviewer": "human"})


def cmd_seed(store, a):
    """从画像表注入该企业/年度的 CSMAR 事实到证据库。"""
    import pandas as pd
    from src.common import CONFIG
    from src.evidence.ingest import csmar_facts_from_profile
    p = pd.read_parquet(Path(CONFIG["paths"]["features_dir"]) / "profile_features.parquet")
    p["stock_code"] = p["stock_code"].astype(str).str.zfill(6)
    row = p[(p["stock_code"] == a.stock.zfill(6)) & (p["year"] == a.year)]
    if row.empty:
        print("未找到该企业年份画像")
        return
    evs = csmar_facts_from_profile(row.iloc[0].to_dict())
    for e in evs:
        store.add(e)
    log.info("已注入 %s 条 CSMAR 事实（%s %s）", len(evs), a.stock, a.year)


def cmd_upgrade(store, a):
    """登记/查看事实类型到正式字段的映射（升级 fact_type）。"""
    from src.evidence.contract import FACT_TYPE_MAP, upgrade_fact_type
    if not a.field:
        print("当前已登记映射：", FACT_TYPE_MAP or "（空）")
        return
    info = upgrade_fact_type(a.fact_type, a.field)
    log.info("已升级 %s -> %s（累计 %s 条映射）",
             info["fact_type"], info["linked_field"], info["total_mapped"])


def cmd_doc(store, a):
    """保存一篇用户文档（文件或文本）到文档库。"""
    from src.rag.documents import save_document
    text = Path(a.file).read_text(encoding="utf-8", errors="ignore") if a.file else (a.text or "")
    rec = save_document(text, a.stock, doc_type=a.doc_type, title=a.title or "",
                        document_date=a.date or "", report_year=a.year)
    print(rec)


def cmd_extract(store, a):
    """从文档语料检索并抽取候选事实（默认规则兜底，--llm 启用模型抽取）。"""
    from src.evidence.llm_extract import extract_document_facts
    from src.rag.chunking import build_document_chunks
    from src.rag.store import KnowledgeBase, Retriever, get_kb
    llm = None
    if a.llm:
        from src.llm.client import get_llm
        llm = get_llm()
    try:
        base = get_kb()
        policy, case = base.policy, base.case
    except Exception:  # noqa: BLE001
        # 知识库不可用时用空检索器兜底，仍可对文档语料抽取
        import pandas as pd
        policy = case = Retriever(pd.DataFrame())
    docs = build_document_chunks()
    doc_r = Retriever(docs, backend="local").build() if not docs.empty else Retriever(docs)
    kb = KnowledgeBase(policy, case, doc_r)
    facts = extract_document_facts(a.stock, a.types.split(","), kb=kb, llm=llm,
                                   doc_type=a.doc_type, report_year=a.year, use_llm=bool(a.llm))
    if not facts:
        print("（未抽取到候选）")
        return
    for e in facts:
        print(f"{e.fact_type:<18} {e.value_num if e.value_num is not None else e.value_text} "
              f"{e.unit} | {e.source_ref} | {e.extraction_method} | conf={e.extraction_confidence}")
    if a.commit:
        for e in facts:
            store.add(e)
        log.info("已写入 %s 条候选（candidate），需人工确认", len(facts))


def main():
    """CLI 入口：解析子命令并分发到对应 cmd_* 处理器。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    # 各子命令仅做参数声明，逻辑在 cmd_* 中
    p = sub.add_parser("add"); p.add_argument("--stock", required=True); p.add_argument("--year", type=int, required=True)
    p.add_argument("--type", required=True); p.add_argument("--value", required=True)
    p.add_argument("--unit"); p.add_argument("--caliber"); p.add_argument("--ref"); p.add_argument("--name")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("pending"); p.set_defaults(func=cmd_pending)
    p = sub.add_parser("show"); p.add_argument("evidence_id"); p.set_defaults(func=cmd_show)
    p = sub.add_parser("act"); p.add_argument("evidence_id"); p.add_argument("action")
    p.add_argument("--value"); p.set_defaults(func=cmd_act)
    p = sub.add_parser("conflicts"); p.add_argument("--stock"); p.add_argument("--year", type=int)
    p.add_argument("--mark", action="store_true"); p.set_defaults(func=cmd_conflicts)
    p = sub.add_parser("solutions"); p.add_argument("--stock"); p.add_argument("--status")
    p.set_defaults(func=cmd_solutions)
    p = sub.add_parser("promote"); p.add_argument("solution_id"); p.add_argument("--status", required=True)
    p.add_argument("--note"); p.set_defaults(func=cmd_promote)
    p = sub.add_parser("seed"); p.add_argument("--stock", required=True); p.add_argument("--year", type=int, required=True)
    p.set_defaults(func=cmd_seed)
    p = sub.add_parser("upgrade"); p.add_argument("--fact-type", required=True, dest="fact_type")
    p.add_argument("--field", help="目标正式字段；省略则只列出当前映射"); p.set_defaults(func=cmd_upgrade)
    p = sub.add_parser("resolve"); p.add_argument("--stock", required=True)
    p.add_argument("--year", type=int, required=True); p.add_argument("--persist", action="store_true")
    p.set_defaults(func=cmd_resolve)
    p = sub.add_parser("doc"); p.add_argument("--stock", required=True); p.add_argument("--file")
    p.add_argument("--text"); p.add_argument("--doc-type", default="other", dest="doc_type")
    p.add_argument("--title"); p.add_argument("--date"); p.add_argument("--year", type=int)
    p.set_defaults(func=cmd_doc)
    p = sub.add_parser("extract"); p.add_argument("--stock", required=True)
    p.add_argument("--types", required=True, help="逗号分隔的 fact_type")
    p.add_argument("--doc-type", dest="doc_type"); p.add_argument("--year", type=int)
    p.add_argument("--llm", action="store_true", help="启用 LLM 抽取（默认规则兜底）")
    p.add_argument("--commit", action="store_true"); p.set_defaults(func=cmd_extract)

    a = ap.parse_args()
    store = EvidenceStore(a.db)
    a.func(store, a)


if __name__ == "__main__":
    main()
