"""分层模块测试（Update 3.1）：确定性模块 + Green 四态。

运行: python tests/test_modules.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.gettempdir()) / "tax_tests"
TMP.mkdir(parents=True, exist_ok=True)

import pandas as pd  # noqa: E402

from src.common import CONFIG  # noqa: E402


def test_data_keys():
    proc = Path(CONFIG["paths"]["data_processed"])
    master = pd.read_parquet(proc / "master.parquet")
    assert not master.duplicated(["stock_code", "year"]).any(), "主表主键重复"
    assert master["year"].between(2020, 2024).all(), "年份越界"
    assert (proc / "lineage" / "field_lineage.json").exists(), "缺字段血缘"
    print(f"[OK] data keys {master.shape}")


def test_diagnostics_module():
    from src.pipeline.plan_pipeline import load_profile, load_history
    from src.rules.diagnostics import run_diagnostics
    p = load_profile("600004", 2024)
    h = load_history("600004", 2024)
    ds = run_diagnostics(p, h)
    assert isinstance(ds, list)
    for d in ds:
        assert d.severity in ("提示", "观察", "关注")
        assert hasattr(d, "gate") and hasattr(d, "deviation")
    # 存量-流量诊断（使用权资产 vs 折旧）应命中
    assert any(d.id == "rou_depreciation" for d in ds), "存量-流量诊断未命中"
    print(f"[OK] diagnostics {len(ds)} 项（含 rou_depreciation）")


def test_calculator_resolution():
    from src.pipeline.plan_pipeline import load_profile
    from src.rules.calculator import run_calculator
    p = load_profile("600004", 2024)
    # 关键输入缺失 → NOT_CALCULABLE
    row = dict(p)
    row["is_revenue"] = None
    r = run_calculator("income_tax_reconciliation", row,
                       key_inputs=["is_total_profit", "nominal_tax_rate", "is_revenue"])
    assert r["ok"] is False and r["impact_type"] == "NOT_CALCULABLE"
    # 含 PROXY 关键输入 → SCENARIO_IMPACT
    r2 = run_calculator("rnd_super_deduction", p, key_inputs=["rd_spend_sum", "nominal_tax_rate"])
    assert r2["ok"] is True and r2["impact_type"] == "SCENARIO_IMPACT"
    assert any(x["field"] == "rd_spend_sum" for x in r2["proxy_inputs"])
    # 零 / 极端不崩
    for mutate in (lambda d: d.update({k: 0.0 for k in ("rd_spend_sum", "nominal_tax_rate")}),
                   lambda d: d.update({"rd_spend_sum": 1e15})):
        d = dict(p); mutate(d)
        run_calculator("rnd_super_deduction", d, key_inputs=["rd_spend_sum", "nominal_tax_rate"])
    print("[OK] calculator resolution (NOT_CALCULABLE / SCENARIO / edges)")


def test_evidence_and_citation():
    from src.evidence.citation import validate_citations
    from src.evidence.ingest import manual_fact
    from src.evidence.store import EvidenceStore
    db = TMP / "mod_evidence.db"
    for s in ("", "-journal", "-wal"):
        try:
            Path(str(db) + s).unlink()
        except OSError:
            pass
    st = EvidenceStore(db)
    ev = st.add(manual_fact("600004", 2024, "rd_spend_sum", 1.62e8, unit="元"))
    assert st.get(ev)["verification_status"] == "confirmed"     # 直接输入自动确认
    # 引用校验
    pols = [{"id": "P1", "text": "按实际发生额的100%加计扣除"}]
    assert validate_citations([{"policy_id": "P1", "quote": "按实际发生额的100%加计扣除"}], pols)[0]["valid"]
    assert not validate_citations([{"policy_id": "P1", "quote": "编造的片段"}], pols)[0]["valid"]
    print("[OK] evidence + citation validation")


def test_fact_resolution_registry():
    from src.rules.fact_resolution import classify, resolution_of
    assert resolution_of("rd_spend_sum") == "PROXY"
    assert resolution_of("is_income_tax") == "DIRECT"
    cls = classify(["rd_spend_sum", "is_income_tax"], {"rd_spend_sum": 1.0, "is_income_tax": None})
    assert "rd_spend_sum" in cls["proxy"] and "is_income_tax" in cls["missing"]
    print("[OK] fact resolution registry")


def test_green_four_states():
    from src.agents import green
    from src.evidence.pool import DataPool

    def mk(**kw):
        p = DataPool(direction="测试")
        p.conditions = kw.get("conditions", {"passed": [{"field": "f", "desc": "d"}],
                                             "failed": [], "unknown": []})
        p.data_gaps = kw.get("data_gaps", [])
        p.amount_requires_evidence = kw.get("amount_requires_evidence", False)
        p.calculation = kw.get("calculation", {"ok": True, "results": {}, "caliber_verified": True})
        p.evidence_required = []
        p.external_evidence = []
        p.qualification_proxy_only = kw.get("qualification_proxy_only", False)
        return p

    d, _ = green.deterministic_decision(mk(conditions={"passed": [], "failed": [{"field": "x", "desc": "不满足"}], "unknown": []}), {})
    assert d == "不推荐", d
    d, _ = green.deterministic_decision(mk(data_gaps=["g"]), {})
    assert d == "证据不足", d
    d, _ = green.deterministic_decision(mk(conditions={"passed": [{"field": "f"}], "failed": [], "unknown": [{"field": "u"}]}), {})
    assert d == "证据不足", d
    d, _ = green.deterministic_decision(mk(), {})
    assert d == "推荐", d
    d, _ = green.deterministic_decision(mk(calculation=None), {})
    assert d == "需要专业人员进一步确认", d
    # 资格封顶
    d, _ = green.deterministic_decision(mk(qualification_proxy_only=True), {})
    assert d == "需要专业人员进一步确认", d
    print("[OK] green four states + qualification cap")


def test_calculation_type_invariant():
    from src.pipeline.plan_pipeline import load_profile
    from src.rules.calculator import (CALCULATORS, CALCULATION_TYPES,
                                      calculation_type_of, run_calculator)
    p = load_profile("600004", 2024)
    for cid in CALCULATORS:
        assert calculation_type_of(cid) in CALCULATION_TYPES, cid
    # 系统不变量：confirmed_tax_impact 存在 ⇒ calculation_type == INCREMENTAL_TAX_BENEFIT
    for cid in CALCULATORS:
        r = run_calculator(cid, p)
        if not isinstance(r, dict):
            continue
        assert r.get("calculation_type") in CALCULATION_TYPES, (cid, r.get("calculation_type"))
        if r.get("confirmed_tax_impact") is not None:
            assert r["calculation_type"] == "INCREMENTAL_TAX_BENEFIT", \
                (cid, r["calculation_type"], r["confirmed_tax_impact"])
        if r.get("calculation_type") in ("TAX_SHIELD", "NO_CALCULATION", "BASELINE_TAX"):
            assert r.get("scenario_tax_impact") is None, (cid, "税盾/无需计算不得计入情景收益")
    # 既有税盾（折旧税盾）不得被确认为新增税收收益，也不得计入情景收益
    r = run_calculator("depreciation_amortization", p)
    assert r["calculation_type"] == "TAX_SHIELD"
    assert r.get("confirmed_tax_impact") is None
    assert r.get("scenario_tax_impact") is None
    assert r.get("impact_type") == "TAX_SHIELD"
    print("[OK] calculation_type invariant (confirmed ⇒ INCREMENTAL)")


def test_policy_validity_gate():
    from src.agents import green
    from src.evidence.policy_validity import classify_policy, summarize
    from src.evidence.pool import DataPool
    assert classify_policy({"doc_no": "X", "date": "2023-01-01"}, "2024-12-31")["validity"] == "VALID"
    assert classify_policy({"title": "关于公布全文废止文件的公告"}, "2024-12-31")["validity"] == "EXPIRED"
    assert classify_policy({"effective_to": "2020-12-31"}, "2024-12-31")["validity"] == "EXPIRED"
    assert classify_policy({"effective_from": "2025-06-01"}, "2024-12-31")["validity"] == "NOT_YET_EFFECTIVE"
    assert classify_policy({}, "2024-12-31")["validity"] == "UNKNOWN"
    assert summarize([{"title": "无日期政策"}], "2024-12-31")["overall"] == "UNKNOWN"
    # 门禁：UNKNOWN 不得单独支撑 CONFIRMED
    p = DataPool(direction="测试")
    p.conditions = {"passed": [], "failed": [], "unknown": []}
    p.calculation = {"ok": True, "caliber_verified": True,
                     "results": {"confirmed_tax_impact": 100.0, "scenario_tax_impact": None}}
    p.policy_validity = {"overall": "UNKNOWN"}
    d, _ = green.deterministic_decision(p, {})
    assert d == "推荐", d
    plan = {"estimated_tax_impact": 100.0}
    d2, _ = green._apply_policy_validity_gate(p, d, [], plan)
    assert plan["estimated_tax_impact"] is None
    assert p.calculation["results"]["confirmed_tax_impact"] is None
    assert d2 == "有条件推荐", d2
    print("[OK] policy_validity gate (UNKNOWN ⇒ no CONFIRMED)")


def test_evidence_overlay_reanalysis():
    from src.evidence.ingest import manual_fact
    from src.evidence.resolution import resolve_profile
    from src.evidence.store import EvidenceStore
    from src.pipeline.plan_pipeline import load_profile
    db = TMP / "mod_overlay.db"
    for s in ("", "-journal", "-wal"):
        try:
            Path(str(db) + s).unlink()
        except OSError:
            pass
    st = EvidenceStore(db)
    prof = load_profile("600004", 2024)
    old = prof.get("rd_spend_sum")
    st.add(manual_fact("600004", 2024, "rd_spend_sum", (old or 0) + 1e7, unit="元"))
    prof2, res = resolve_profile(prof, store=st)
    assert any(s["field"] == "rd_spend_sum" for s in res["superseded"]), res
    assert prof2["rd_spend_sum"] != old
    # 直接口径证据 → 该字段进入 direct_fields（PROXY 可升级为 DIRECT）
    from src.evidence.resolution import direct_evidence_fields
    assert "rd_spend_sum" in direct_evidence_fields("600004", 2024, store=st)
    print("[OK] evidence overlay → profile (re-analysis input)")


def test_status_key_from_conclusion():
    from src.report.view_model import status_key
    assert status_key("RECOMMEND", "CONFIRMED_IMPACT") == "CONFIRMED"
    # 确认资格/合规（无金额）也算 CONFIRMED：结论已确认，不要求存在金额
    assert status_key("RECOMMEND", "NO_CALCULATION") == "CONFIRMED"
    assert status_key("RECOMMEND", "TAX_SHIELD") == "CONFIRMED"
    assert status_key("CONDITIONAL", "SCENARIO_IMPACT") == "SCENARIO"
    assert status_key("MANUAL_REVIEW", None) == "SCENARIO"
    assert status_key("INSUFFICIENT_EVIDENCE", None) == "DATA_GAP"
    assert status_key("NOT_RECOMMEND", "SCENARIO_IMPACT") == "NOT_RECOMMEND"
    assert status_key("", "NOT_CALCULABLE") == "DATA_GAP"
    print("[OK] status_key from conclusion + amount")


def test_rag_embedding_fingerprint():
    from src.rag.store import _fingerprint, _fingerprint_mismatch
    cur = _fingerprint()
    assert _fingerprint_mismatch(cur, cur, dim=1024) is None
    # 旧索引缺指纹 → 可采纳（不强制重建）
    assert _fingerprint_mismatch(None, cur) is None
    # 换模型 → 不一致（强制重建）
    assert _fingerprint_mismatch({**cur, "embedding_model": "other"}, cur, dim=1024) is not None
    # 换维度 → 不一致
    assert _fingerprint_mismatch({**cur, "dim": 768}, {**cur, "dim": 1024}, dim=1024) is not None
    print("[OK] rag embedding fingerprint")


def test_version_fingerprint():
    from src.versioning import build_fingerprint
    fp = build_fingerprint()
    assert fp["skills"]["count"] >= 1 and fp["skills"]["hash"]
    assert {"data", "models", "config"} <= set(fp.keys())
    assert fp["models"].get("embedding") is not None
    print("[OK] version fingerprint")


def test_rerank_backoff_cache():
    from src.common import CONFIG
    from src.llm.client import LLMClient
    cfg = CONFIG.setdefault("rag", {}).setdefault("rerank", {})
    old = dict(cfg)
    cfg.update({"retries": 3, "backoff": 1.0, "cooldown": 1, "cache_size": 8})
    try:
        c = LLMClient()
        calls = {"n": 0}

        def fake_rerank(q, docs, top_n=5):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("Rerank 调用失败 429: TPM limit reached")
            return [{"index": 0, "relevance_score": 0.9}]

        c.rerank = fake_rerank  # type: ignore[assignment]
        r1 = c.try_rerank("q", ["a", "b"], top_n=1)
        assert r1 and r1[0]["relevance_score"] == 0.9
        assert calls["n"] == 2, calls  # 一次限流重试后成功
        r2 = c.try_rerank("q", ["a", "b"], top_n=1)
        assert r2 == r1 and calls["n"] == 2  # 命中缓存，不再调用
    finally:
        cfg.clear()
        cfg.update(old)
    print("[OK] rerank backoff + cache")


def test_policy_metadata_governance():
    from src.evidence.policy_meta import PENDING, SOURCE_RULE, VERIFIED, build_metadata
    m = build_metadata({"doc_no": "财税〔2018〕99号", "title": "T", "date": "2018-09-01",
                        "tax_type": "税收政策-企业所得税", "effect_level": "规范性文件",
                        "url": "/zcfgk/x"}, "2024-12-31")
    assert m["source"] == SOURCE_RULE and m["verification"] == VERIFIED
    assert m["level"] == "规范性文件" and m["tax_type"] == ["税收政策-企业所得税"]
    assert m["status"] == "VALID" and m["citation_url"]
    m2 = build_metadata({"title": "无文号无日期"}, "2024-12-31")
    assert m2["verification"] == PENDING and m2["metadata_confidence"] == "LOW"
    print("[OK] policy metadata governance (source/confidence/verification)")


def test_job_persistence():
    from src.api.services import jobs as J
    job = J.Job("600004", 2024, False)
    job.status = "RUNNING"
    job.started_at = "10:00:00"
    job.progress("阶段A", 10)
    assert J._job_file(job.id).exists()
    # 新管理器（模拟进程重启）→ 加载并标记中断
    m = J.JobManager()
    j2 = m.get(job.id)
    assert j2 is not None and j2.status == "FAILED", j2 and j2.status
    assert "中断" in (j2.error or "")
    try:
        J._job_file(job.id).unlink()
    except OSError:
        pass
    print("[OK] job persistence + restart recovery")


def test_pool_attaches_policy_meta():
    from src.evidence.pool import build_pool
    sr = {"policies": [{"doc_no": "财税〔2015〕119号", "title": "T", "date": "2015-11-02",
                        "tax_type": "税收政策-企业所得税", "effect_level": "规范性文件",
                        "url": "/zcfgk/x"}]}
    pool = build_pool("研发筹划", sr, {"stock_code": "600004", "year": 2024})
    assert pool.policies and pool.policies[0]["meta"]["source"] == "rule"
    assert pool.policies[0]["meta"]["verification"] == "VERIFIED"
    assert pool.policy_validity.get("overall") == "VALID"
    print("[OK] pool attaches policy meta + validity")


def test_export_chain_markdown():
    from src.report.export import render_chain_markdown
    doc = {
        "stock_code": "600004", "year": 2024, "direction": "研发筹划",
        "final_decision": "有条件推荐", "decision_code": "CONDITIONAL",
        "estimated_tax_impact": None, "scenario_tax_impact": 12345678.0,
        "policy_validity": {"overall": "VALID"},
        "skill": {"id": "rnd_super_deduction", "name": "研发加计扣除"},
        "lineage": {
            "calculation": {"calculator": "rnd_super_deduction",
                            "calculation_type": "INCREMENTAL_TAX_BENEFIT",
                            "impact_type": "SCENARIO_IMPACT",
                            "layers": [{"layer": "可加计扣除额", "value": 1e7, "note": "估算"}]},
            "data_pool": {
                "company_facts": [{"id": "F1", "field": "rd_spend_sum", "value": 1.6e8,
                                   "source_text": "CSMAR"}],
                "diagnostics": [{"severity": "关注", "name": "研发强度"}],
                "policies": [{"id": "P1", "doc_no": "财税〔2015〕119号", "title": "通知",
                              "date": "2015-11-02", "url": "http://x",
                              "meta": {"source": "rule", "metadata_confidence": "HIGH",
                                       "verification": "VERIFIED"}}],
                "conditions": {"passed": [{"desc": "研发活动真实"}], "failed": [], "unknown": []},
            },
            "debate": [{"round": 1, "red": {"position": "可加计"}, "blue": {"position": "需辅助账"}}],
            "required_facts": [{"present": True}], "evidence_required": ["研发辅助账"],
        },
    }
    md = render_chain_markdown(doc)
    for k in ("① 企业事实", "② 诊断信号", "③ 税务方向", "④ 政策依据", "⑤ 条件核验",
              "⑥ 税额计算", "⑦ 最终结论", "旁支 · 审查说明", "旁支 · Evidence"):
        assert k in md, k
    assert "财税〔2015〕119号" in md
    print("[OK] export chain markdown")


def test_evidence_upgrades_resolution_to_confirmed():
    from src.rules.calculator import run_calculator
    row = {"rd_spend_sum": 1e8, "rd_expenses": 1e8, "nominal_tax_rate": 25.0,
           "is_asset_impairment": 1e7, "is_credit_impairment": 5e6, "is_revenue": 1e10}
    keys = ["rd_spend_sum", "nominal_tax_rate"]
    # 无直接证据 → 会计口径为代理 → SCENARIO
    r0 = run_calculator("rnd_super_deduction", row, key_inputs=keys)
    assert r0["impact_type"] == "SCENARIO_IMPACT" and r0.get("confirmed_tax_impact") is None
    # 直接口径证据 → PROXY 升级为 DIRECT → CONFIRMED
    r1 = run_calculator("rnd_super_deduction", row, key_inputs=keys,
                        direct_fields={"rd_spend_sum"})
    assert r1["calculation_type"] == "INCREMENTAL_TAX_BENEFIT"
    assert r1["impact_type"] == "CONFIRMED_IMPACT" and r1.get("confirmed_tax_impact") is not None
    # 资产减值：会计计提为代理，需直接证据（可税前扣除的资产损失）
    akeys = ["is_asset_impairment", "is_credit_impairment", "nominal_tax_rate"]
    a0 = run_calculator("asset_impairment", row, key_inputs=akeys)
    assert a0["impact_type"] == "SCENARIO_IMPACT"
    # 登记层保守：asset_impairment 已移出 VERIFIABLE → 即使补直接证据也不 CONFIRMED（P0-E/B）
    a1 = run_calculator("asset_impairment", row, key_inputs=akeys,
                        direct_fields={"is_asset_impairment", "is_credit_impairment"})
    assert a1["impact_type"] == "SCENARIO_IMPACT" and a1.get("confirmed_tax_impact") is None
    print("[OK] evidence upgrades resolution → CONFIRMED (rnd / asset_impairment conservative)")


def test_per_item_diagnostic_thresholds():
    from src.rules.diagnostics import _Builder
    cfg = {"category_thresholds": {"estimate": {"提示": 0.1, "观察": 0.2, "关注": 0.3}},
           "items": {"x": {"category": "estimate",
                           "thresholds": {"提示": 0.5, "观察": 0.6, "关注": 0.7}}}}
    b = _Builder(cfg)
    b.add("x", "n", "d", 1.0, 1.4, 0.55, "s", [])
    assert b.out and b.out[0].severity == "提示", b.out  # 逐项阈值覆盖类别阈值
    b2 = _Builder(cfg)
    b2.add("y", "n", "d", 1.0, 1.4, 0.55, "s", [])       # 未登记 → 用类别阈值
    assert b2.out[0].severity == "关注"
    b3 = _Builder(cfg)
    b3.add("x", "n", "d", 1.0, 1.4, 0.4, "s", [])        # 低于逐项提示 → 不产出
    assert not b3.out
    print("[OK] per-item diagnostic thresholds (override category)")


def test_backfill_proxy_and_coverage():
    import pandas as pd
    from src.data.backfill import apply_backfill
    from src.data.coverage import coverage_ledger
    df = pd.DataFrame({
        "year": [2024, 2024, 2024],
        "is_hightech": [None, None, None],
        "nominal_tax_rate": [15.0, 25.0, 25.0],
        "rd_expense_ratio": [0.01, 0.05, 0.01],
        "rd_person_ratio_cross": [0.02, 0.12, 0.02],
        "subsidiary_count": [0.0, 1.0, None],
        "subsidiary_overseas_count": [None, None, None],
        "employee_compensation_base": [None, 100.0, None],
        "bs_payroll_payable": [None, 100.0, 50.0],
    })
    out, _rep = apply_backfill(df)
    assert "hightech_signal_proxy" in out.columns
    assert int(out.loc[0, "hightech_signal_proxy"]) == 1     # 名义税率<=15%
    assert int(out.loc[1, "hightech_signal_proxy"]) == 1     # 研发指标接近门槛
    assert int(out.loc[2, "hightech_signal_proxy"]) == 0
    # 资格事实 is_hightech 绝不被回填
    assert out["is_hightech"].isna().all()
    # 同义多源合并：职工薪酬缺口由应付职工薪酬补齐
    assert out.loc[2, "employee_compensation_base"] == 50.0
    led = coverage_ledger(out)
    assert any(r["field"] == "is_hightech" and r["low"] for r in led)
    print("[OK] backfill proxy + coverage ledger")


def test_conflict_resolution():
    from src.evidence.contract import Evidence
    from src.evidence.resolution import resolve_profile
    from src.evidence.review import resolve_conflict
    from src.evidence.store import EvidenceStore
    from src.pipeline.plan_pipeline import load_profile
    db = TMP / "mod_conflict.db"
    for s in ("", "-journal", "-wal"):
        try:
            Path(str(db) + s).unlink()
        except OSError:
            pass
    st = EvidenceStore(db)
    prof = load_profile("600004", 2024)
    old = prof.get("rd_spend_sum") or 0.0
    # user_structured（补充）与画像不一致 → 冲突（不覆盖）
    ev = Evidence.from_row({"evidence_id": "", "stock_code": "600004", "year": 2024,
                            "fact_type": "rd_spend_sum", "value_num": old + 5e7, "unit": "元",
                            "source_type": "user_structured", "import_mode": "supplement",
                            "verification_status": "confirmed"})
    eid = st.add(ev)
    _p, res = resolve_profile(prof, store=st)
    assert any(c["evidence_id"] == eid for c in res["conflicts"]), res
    # 以证据为准 → authoritative + confirmed → 下次采用时覆盖画像
    assert resolve_conflict(st, eid, "evidence") == "evidence"
    _p3, res3 = resolve_profile(prof, store=st)
    assert any(s["field"] == "rd_spend_sum" for s in res3["superseded"]), res3
    print("[OK] conflict resolution (evidence vs profile)")


def test_advisor_opinion_template():
    from src.agents.advisor import build_opinion
    doc = {
        "direction": "研发筹划", "final_decision": "有条件推荐", "decision_code": "CONDITIONAL",
        "lineage": {
            "data_pool": {
                "conditions": {"passed": [{"desc": "研发活动真实"}], "failed": [],
                               "unknown": [{"desc": "研发辅助账"}]},
                "policies": [{"id": "P1", "doc_no": "财税〔2015〕119号", "title": "通知", "url": "http://x"}],
                "risks": ["归集不规范"],
            },
            "ai_process": {"green": {"reasons": ["条件满足但需补证据"]},
                           "blue": {"risks": ["辅助账缺失"], "issues": ["需辅助账"]}},
            "debate": [{"round": 1, "red": {"position": "可加计"}, "blue": {"position": "需辅助账"}}],
            "calculation": {"calculation_type": "INCREMENTAL_TAX_BENEFIT",
                            "results": {"scenario_tax_impact": 1e7}},
            "evidence_required": ["研发辅助账"],
        },
    }
    op = build_opinion(doc, kb=None, use_llm=False)
    assert op["_source"] == "rule"
    assert op["decision"] == "有条件推荐"            # 沿用 Green 结论
    assert op["steps"] and op["materials"]
    assert op["references"] and op["refs"]
    assert op["disclaimer"]
    print("[OK] advisor opinion (template fallback, grounded in pool/debate)")


def test_per_skill_resolution():
    from src.rules.fact_resolution import resolution_of
    # 全局：利润总额是"可弥补亏损"的代理
    assert resolution_of("is_total_profit") == "PROXY"
    # 技能语境：税率优惠/税会差异以利润总额为基数 → 直接口径
    assert resolution_of("is_total_profit", "hightech_rate_benefit") == "DIRECT"
    assert resolution_of("is_total_profit", "income_tax_reconciliation") == "DIRECT"
    # 亏损弥补语境仍为代理
    assert resolution_of("is_total_profit", "loss_carryforward") == "PROXY"
    print("[OK] per-skill resolution (by_skill override)")


def test_semantic_registry_consistency():
    """登记体检：registry ↔ calculator ↔ 语义约束 一致（P0-C）。"""
    from src.rules.calculator import CALCULATORS, calculation_type_of
    from src.rules.semantics import load_semantics, semantics_of
    assert load_semantics(), "calculator_semantics.yaml 未加载"
    for cid in CALCULATORS:
        s = semantics_of(cid)
        assert s, f"缺少语义登记：{cid}"
        assert s.get("calculation_type") == calculation_type_of(cid), \
            (cid, s.get("calculation_type"), calculation_type_of(cid))
        if s["calculation_type"] == "INCREMENTAL_TAX_BENEFIT":
            assert s.get("impact_direction") == "tax_reduce", cid
            for f in ("primary_output", "economic_meaning", "baseline", "action", "formula_role"):
                assert s.get(f), (cid, f)
    print("[OK] semantic registry consistency (registry ↔ calculator)")


def test_confirmed_amount_invariant():
    """硬约束：confirmed_tax_impact 仅 CONFIRMED ∧ tax_reduce，且恒 ≥0（P0-C）。"""
    from src.pipeline.plan_pipeline import load_profile
    from src.rules.calculator import CALCULATORS, run_calculator
    p = load_profile("600004", 2024)
    for cid in CALCULATORS:
        r = run_calculator(cid, p)
        if not isinstance(r, dict):
            continue
        assert r.get("direction") in ("tax_reduce", "tax_increase", "none"), (cid, r.get("direction"))
        ct = r.get("confirmed_tax_impact")
        if ct is not None:
            assert r["calculation_type"] == "INCREMENTAL_TAX_BENEFIT", (cid, r["calculation_type"])
            assert r.get("direction") == "tax_reduce", (cid, r.get("direction"))
            assert ct >= 0, (cid, ct)
    print("[OK] confirmed amount invariant (CONFIRMED ∧ tax_reduce ∧ ≥0)")


if __name__ == "__main__":
    test_data_keys()
    test_diagnostics_module()
    test_calculator_resolution()
    test_evidence_and_citation()
    test_fact_resolution_registry()
    test_green_four_states()
    test_calculation_type_invariant()
    test_policy_validity_gate()
    test_evidence_overlay_reanalysis()
    test_status_key_from_conclusion()
    test_rag_embedding_fingerprint()
    test_version_fingerprint()
    test_rerank_backoff_cache()
    test_policy_metadata_governance()
    test_job_persistence()
    test_pool_attaches_policy_meta()
    test_export_chain_markdown()
    test_evidence_upgrades_resolution_to_confirmed()
    test_per_item_diagnostic_thresholds()
    test_backfill_proxy_and_coverage()
    test_conflict_resolution()
    test_advisor_opinion_template()
    test_per_skill_resolution()
    test_semantic_registry_consistency()
    test_confirmed_amount_invariant()
    print("\n分层模块测试全部通过。")
