"""冒烟测试：验证数据层、衍生层、RAG、Skill、端到端能用。

运行: python tests/test_smoke.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from src.common import CONFIG  # noqa: E402

TMP = Path(tempfile.gettempdir()) / "tax_tests"
TMP.mkdir(parents=True, exist_ok=True)

try:  # 标记为慢速冒烟测试（pytest 默认跳过，见 pytest.ini）
    import pytest
    pytestmark = pytest.mark.smoke
except Exception:  # noqa: BLE001
    pytestmark = None


def test_data_outputs():
    proc = Path(CONFIG["paths"]["data_processed"])
    for f in ["master.parquet", "dictionary.csv", "manifest.csv",
              "derived_catalog.csv", "pipeline_log.md"]:
        assert (proc / f).exists(), f"缺少 {f}"
    master = pd.read_parquet(proc / "master.parquet")
    assert not master.duplicated(["stock_code", "year"]).any(), "主表主键重复"
    assert master["year"].between(2020, 2024).all(), "年份越界"
    print(f"[OK] master {master.shape}")


def test_derived_and_profile():
    proc = Path(CONFIG["paths"]["data_processed"])
    d = pd.read_parquet(proc / "derived" / "derived_metrics.parquet")
    p = pd.read_parquet(proc / "features" / "profile_features.parquet")
    assert "rd_super_deduction_potential" in d.columns
    assert "asset_liability_ratio_industry_median" in d.columns
    assert "label_high_rd" in p.columns
    assert len(d) == len(p)
    print(f"[OK] derived {d.shape}, profile {p.shape}")


def test_rag():
    from src.rag.store import get_kb
    kb = get_kb()
    res = kb.search("研发费用加计扣除", "policy", top_k=3)
    assert res, "政策检索无结果"
    print(f"[OK] RAG hit {len(res)}: {res[0].get('doc_no') or res[0].get('title')}")


def test_skills():
    from src.skills.engine import get_skills
    skills = get_skills()
    assert "rnd_super_deduction" in skills
    assert len(skills) >= 3
    print(f"[OK] skills {list(skills)}")


def test_end_to_end():
    from src.pipeline.plan_pipeline import run_plan
    result = run_plan("300750", 2024, use_llm=False, save=False)
    assert result["plans"], "未产生方案"
    doc = result["plans"][0]
    assert doc["final_decision"] in {"推荐", "有条件推荐", "不推荐", "证据不足", "需要专业人员进一步确认"}
    print(f"[OK] plan {result['name']} -> {result['summary']}")


def test_evidence_pipeline():
    from src.evidence import review
    from src.evidence.conflict import detect
    from src.evidence.ingest import manual_fact
    from src.evidence.store import EvidenceStore

    db = TMP / "test_evidence.db"
    for suffix in ("", "-journal", "-wal"):
        try:
            Path(str(db) + suffix).unlink()
        except OSError:
            pass
    st = EvidenceStore(db)
    e1 = st.add(manual_fact("000001", 2024, "rd_spend_sum", 1.0e8, unit="元",
                            caliber="合并", source_type="csmar", source_ref="CSMAR"))
    e2 = st.add(manual_fact("000001", 2024, "rd_spend_sum", 1.6e8, unit="元",
                            caliber="合并", source_type="annual_report", source_ref="年报P1"))
    st.add(manual_fact("000001", 2024, "RD_PROJECT", "研发项目A", source_ref="年报P2"))
    assert e1 != e2, "evidence_id 应唯一"
    conf = detect(st)
    assert conf and conf[0]["status"] in {"conflict", "minor_conflict", "different_caliber"}
    review.act(st, e1, "a")
    assert st.confirmed("000001", 2024)
    print("[OK] evidence pipeline (ingest/conflict/review)")


def test_pdf_parser():
    import fitz
    from src.evidence.pdf_parser import extract_pdf_facts

    tmp = TMP / "smoke_report.pdf"
    doc = fitz.open()
    page = doc.new_page()
    lines = ["研发投入合计 1.85 亿元", "研发投入占营业收入比例 5.77%",
             "所得税费用 0.45 亿元", "营业收入 12.6 亿元"]
    y = 100
    for ln in lines:
        page.insert_text((72, y), ln, fontsize=12, fontname="china-s")
        y += 22
    doc.save(tmp)
    doc.close()

    facts = extract_pdf_facts(tmp, "000063", 2024, source_ref="smoke")
    by_type = {f.fact_type: f for f in facts}
    assert by_type["rd_spend_sum"].value_num == 1.85e8, "研发投入单位归一错误"
    assert by_type["is_income_tax"].value_num == 0.45e8
    assert all(abs(float(f.value_num or 0) - 5.77) > 1 for f in facts), "百分比噪声未被过滤"
    assert all(f.extraction_confidence > 0 and f.source_ref for f in facts), "候选缺少定位/置信度"
    assert all(f.source_type == "user_document" for f in facts), "文档来源应为 user_document"
    print(f"[OK] pdf parser {len(facts)} facts, noise filtered")


def test_source_precedence_and_resolution():
    from src.evidence.contract import (resolve_linked_field, source_precedence,
                                       upgrade_fact_type)
    from src.evidence.ingest import ingest_wide, manual_fact
    from src.evidence import review
    from src.evidence.resolution import resolve_profile
    from src.evidence.store import EvidenceStore

    # 采用优先级：直接输入 > 结构化 > 数据库 ≥ 文档提取
    assert source_precedence("manual") > source_precedence("user_structured")
    assert source_precedence("user_structured") > source_precedence("csmar")
    assert source_precedence("csmar") > source_precedence("user_document")

    import pandas as pd
    tmp = TMP
    db = tmp / "smoke_resolution.db"
    for suffix in ("", "-journal", "-wal"):
        try:
            Path(str(db) + suffix).unlink()
        except OSError:
            pass
    st = EvidenceStore(db)

    # C: unmapped -> upgrade
    e = st.add(manual_fact("600004", 2024, "RD_DEV_EXPENSE", 5.0e7, unit="元", source_ref="辅助账"))
    assert resolve_linked_field("RD_DEV_EXPENSE") is None
    upgrade_fact_type("RD_DEV_EXPENSE", "bs_development_expense", persist=False)
    assert resolve_linked_field("RD_DEV_EXPENSE") == "bs_development_expense"

    # manual 覆盖 + 留旧值
    st.add(manual_fact("600004", 2024, "rd_spend_sum", 1.62e8, unit="元",
                       caliber="合并", source_ref="企业内部台账"))
    prof, res = resolve_profile({"stock_code": "600004", "year": 2024, "rd_spend_sum": 1.45e8}, store=st)
    assert prof["rd_spend_sum"] == 1.62e8
    assert res["superseded"][0]["old_value"] == 1.45e8
    assert res["superseded"][0]["old_source"] == "csmar"

    # structured 默认不覆盖 -> 冲突；authoritative 才覆盖
    df = pd.DataFrame({"stock_code": ["600004"], "year": [2024], "is_revenue": [2.11e9]})
    st.add_many(ingest_wide(df, {"is_revenue": "is_revenue"}, unit="元", caliber="合并",
                            source_ref="内部.xlsx"))
    _, r1 = resolve_profile({"stock_code": "600004", "year": 2024, "is_revenue": 2.082e9}, store=st)
    assert r1["conflicts"] and not r1["superseded"], "结构化默认不应覆盖"
    st.add_many(ingest_wide(df, {"is_revenue": "is_revenue"}, unit="元", caliber="合并",
                            source_ref="内部.xlsx", import_mode="authoritative"))
    p2, r2 = resolve_profile({"stock_code": "600004", "year": 2024, "is_revenue": 2.082e9}, store=st)
    assert p2["is_revenue"] == 2.11e9 and r2["superseded"], "authoritative 应覆盖"
    print("[OK] source precedence + resolution (override/supersede/conflict)")


def test_document_rag_and_extraction():
    import pandas as pd
    from src.evidence.llm_extract import extract_document_facts, validate_quote
    from src.rag.chunking import chunk_document
    from src.rag.documents import list_documents, save_document
    from src.rag.store import KnowledgeBase, Retriever

    text = ("广州白云国际机场股份有限公司2024年度股东大会记录\n\n"
            "研发投入合计 53,223,001.54 元，研发人员数量 193 人。\n\n"
            "研发投入占营业收入比例 5.77%。\n")
    rec = save_document(text, "600004", doc_type="shareholder_meeting",
                        title="2024年度股东大会记录", document_date="2025-05-20", report_year=2024)
    assert list_documents("600004")
    chunks = chunk_document(text)
    assert any("研发投入合计" in c for c in chunks), "表格块应整体保留"

    # 引用校验（防幻觉）
    chunk = "研发投入合计 53,223,001.54 元，研发人员数量 193 人。"
    assert validate_quote("研发投入合计 53,223,001.54 元", chunk, 53223001.54) is True
    assert validate_quote("研发投入合计 999 元", chunk, 999) is False
    assert validate_quote("营业收入 100 元", chunk, 100) is False

    # 文档语料 + 元数据过滤 + 规则抽取（无 LLM）
    docs = pd.DataFrame([{
        "corpus": "document", "chunk_id": f"doc-{rec['document_id']}-0", "text": chunk,
        "stock_code": "600004", "report_year": 2024, "doc_type": "shareholder_meeting",
        "document_id": rec["document_id"], "document_date": "2025-05-20",
        "title": rec["title"], "channel": "shareholder_meeting", "date": "", "source": "用户文档",
    }])
    kb = KnowledgeBase(Retriever(pd.DataFrame()), Retriever(pd.DataFrame()),
                       Retriever(docs, backend="local").build())
    # 元数据过滤：别的公司检索不到
    assert kb.search("研发投入", corpus="document", filters={"stock_code": "000001"}) == []
    facts = extract_document_facts("600004", ["rd_spend_sum", "rd_person"], kb=kb,
                                   use_llm=False, report_year=2024)
    types = {f.fact_type for f in facts}
    assert "rd_spend_sum" in types and "rd_person" in types
    assert all(f.source_type == "user_document" and f.verification_status == "candidate" for f in facts)
    print("[OK] document RAG + quote validation + extraction")


def test_ingest_csv_excel():
    import pandas as pd
    from src.evidence.ingest import ingest_csv, ingest_excel

    tmp = TMP
    lp = tmp / "smoke_long.csv"
    pd.DataFrame({"stock_code": ["600004"], "year": [2024], "fact_type": ["rd_person"],
                  "value": ["193"], "unit": [""], "caliber": ["合并"]}).to_csv(
        lp, index=False, encoding="utf-8-sig")
    evs = ingest_csv(lp, mode="long", code_col="stock_code", year_col="year",
                     type_col="fact_type", value_col="value", unit_col="unit", caliber_col="caliber")
    assert len(evs) == 1 and evs[0].linked_field == "rd_person"
    assert evs[0].unit == "", "空单元格不应变成 'nan'"
    assert evs[0].source_type == "user_structured" and evs[0].verification_status == "confirmed"

    xp = tmp / "smoke_wide.xlsx"
    pd.DataFrame({"stock_code": ["600004"], "year": [2024], "rd_spend_sum": [53223001.54]}).to_excel(
        xp, index=False)
    evs2 = ingest_excel(xp, mode="wide", fact_map={"rd_spend_sum": "rd_spend_sum"},
                        unit="元", caliber="合并")
    assert len(evs2) == 1 and evs2[0].value_num == 53223001.54 and evs2[0].source_type == "user_structured"
    print("[OK] ingest csv/excel (long + wide)")


if __name__ == "__main__":
    test_data_outputs()
    test_derived_and_profile()
    test_rag()
    test_skills()
    test_end_to_end()
    test_evidence_pipeline()
    test_pdf_parser()
    test_source_precedence_and_resolution()
    test_document_rag_and_extraction()
    test_ingest_csv_excel()
    print("\n全部冒烟测试通过。")
