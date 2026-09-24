"""UI View Model 构建（Update 4 · Phase 0）。

产出（run_dir 下）：
- result.json        轻索引（UI 入口）
- opportunities.json 侦察方向
- diagnostics.json   诊断信号
- plans/<dir>.json   完整 plan（由 save_plan 写入）

以及 outputs/index.json（已分析清单）。

设计：View Model 是「UI 只读快照」，把所有展示所需字段预先算好（状态码、金额、
完整度、简要建议），UI 端不做业务判断，保证展示与后端口径一致。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.common import get_logger, save_json

log = get_logger()

# UI 企业概览展示的字段白名单（按展示顺序）
# 只暴露这些画像字段给前端，避免把内部/敏感字段一并输出
ENTERPRISE_FIELDS = [
    "short_name", "industry_name", "province", "city", "equity_nature", "size_group",
    "is_revenue", "is_net_profit", "is_total_profit", "bs_total_assets",
    "rd_spend_sum", "rd_expense_ratio", "rd_person", "nominal_tax_rate", "etr",
    "gross_margin", "net_margin", "asset_liability_ratio", "interest_debt_ratio",
    "revenue_growth", "fixed_asset_ratio", "gov_subsidy_total", "is_income_tax",
    "cf_tax_paid", "employee_compensation_base", "bs_accounts_receivable", "is_hightech",
]


def _slug(text: str) -> str:
    """把方向名转为安全文件名片段：仅保留字母数字与 -_，截断 20 字符。"""
    return "".join(ch for ch in str(text) if ch.isalnum() or ch in "-_")[:20] or "plan"


def status_key(decision_code: str, impact_type: str | None) -> str:
    """结论状态（四态）：CONFIRMED / SCENARIO / DATA_GAP / NOT_RECOMMEND。

    由「结论 + 金额」共同推导（Update 5.0 收尾）：
      - NOT_RECOMMEND          → 不推荐
      - INSUFFICIENT_EVIDENCE / NOT_CALCULABLE → 证据不足
      - RECOMMEND / CONFIRMED_IMPACT           → 已确认
        （**确认资格 / 合规 / 政策适用 / 风险**等无金额的确认性结论也算 CONFIRMED）
      - 其余（CONDITIONAL / MANUAL_REVIEW / 情景 / 税盾 / 无需计算）→ 情景/待确认
    """
    dc = str(decision_code or "")
    it = str(impact_type or "")
    if dc == "NOT_RECOMMEND":
        # 明确不推荐：优先级最高，即使有金额也不再展示为可执行
        return "NOT_RECOMMEND"
    if dc == "INSUFFICIENT_EVIDENCE" or it == "NOT_CALCULABLE":
        # 证据不足 / 不可计算 → 数据缺口态
        return "DATA_GAP"
    if dc == "RECOMMEND" or it == "CONFIRMED_IMPACT":
        # 推荐或已确认影响 → 已确认态（无金额的确认性结论同样归此）
        return "CONFIRMED"
    return "SCENARIO"   # 其余（有条件/待人工/情景/税盾/无需计算）


def _fact_stats(required_facts: list[dict]) -> dict:
    """统计必需事实的完整度。

    data_completeness = 已具备事实占比；evidence_completeness = 直接口径(DIRECT)占比。
    missing 含「未提供」与「证据不足(INSUFFICIENT)」两类，避免把不足当已具备。
    """
    total = len(required_facts)
    present = sum(1 for f in required_facts if f.get("present"))
    direct = sum(1 for f in required_facts
                 if f.get("present") and f.get("resolution") == "DIRECT")
    proxy = sum(1 for f in required_facts if f.get("resolution") == "PROXY")
    missing = sum(1 for f in required_facts
                  if not f.get("present") or f.get("resolution") == "INSUFFICIENT")
    return {
        "total": total,
        "present": present,   # 已提供（含代理口径）
        "direct": direct,     # 直接口径证据（可支撑确认）
        "proxy": proxy,       # 代理/近似口径
        "missing": missing,   # 缺失或证据不足
        "data_completeness": round(present / total, 3) if total else None,
        "evidence_completeness": round(direct / total, 3) if total else None,
    }


def _plan_entry(p: dict, discovery: dict) -> dict:
    """把单个 plan 压缩为 UI 列表项（含状态、金额、事实完整度、详情引用）。

    evidence_gap 为真表示存在证据缺口（数据缺口/条件未知/不可计算），
    UI 据此提示「需补证据」而非直接展示金额。
    """
    # 只从 lineage 读取证据链信息；缺失时以空结构兜底，保证列表项字段齐全
    lin = p.get("lineage") or {}
    calc = lin.get("calculation") or {}
    rfacts = lin.get("required_facts") or []
    stats = _fact_stats(rfacts)
    impact = calc.get("impact_type")
    dc = p.get("decision_code")
    evidence_gap = bool(lin.get("data_gaps") or
                        (lin.get("conditions") or {}).get("unknown") or
                        impact == "NOT_CALCULABLE")
    return {
        "direction": p.get("direction"),
        "skill_id": (p.get("skill") or {}).get("id"),
        "decision": p.get("final_decision"),
        "decision_code": dc,
        "status_key": status_key(dc, impact),   # 四态：CONFIRMED/SCENARIO/DATA_GAP/NOT_RECOMMEND
        "impact_type": impact,
        "calculation_type": calc.get("calculation_type"),
        "policy_validity": (p.get("policy_validity") or {}).get("overall"),
        "evidence_gap": evidence_gap,
        "confirmed_tax_impact": p.get("estimated_tax_impact"),
        "scenario_tax_impact": p.get("scenario_tax_impact"),
        "amount_is_estimate": p.get("amount_is_estimate"),
        "discovery_source": discovery.get(p.get("direction")),
        "fact_stats": stats,
        "detail_ref": f"plans/{_slug(p.get('direction'))}.json",   # 详情文件相对路径
    }


def _brief(plan_index: list[dict], diagnostics: list[dict]) -> str:
    """一句话简要建议（确定性拼装，不做 LLM 判断）。优先 推荐 > 有条件推荐。

    结构：先取最高严重度诊断，再取排序最靠前的方案；金额标注来源类型
    （确认影响 / 税盾（非筹划收益） / 情景测算），避免把税盾当收益展示。
    """
    rank = {"关注": 3, "观察": 2, "提示": 1}
    parts: list[str] = []
    ds = sorted(diagnostics, key=lambda d: -rank.get(d.get("severity"), 0))   # 取最高严重度信号
    if ds:
        parts.append(f"{ds[0].get('severity')}：{ds[0].get('name')}")
    order = {"RECOMMEND": 0, "CONDITIONAL": 1}
    # 优先推荐 > 有条件推荐，再按影响金额降序；确认影响优先于情景金额
    pref = sorted(plan_index, key=lambda p: (
        order.get(p.get("decision_code"), 2),
        -(p.get("confirmed_tax_impact") or p.get("scenario_tax_impact") or 0)))
    if pref:
        p = pref[0]
        amt = p.get("confirmed_tax_impact") or p.get("scenario_tax_impact")
        seg = f"{p['direction']}：{p.get('decision')}"
        if amt:
            if p.get("confirmed_tax_impact"):
                tag = "确认影响"
            elif p.get("calculation_type") == "TAX_SHIELD":
                tag = "税盾（非筹划收益）"
            else:
                tag = "情景测算"
            seg += f"（{tag} {amt/1e4:,.2f} 万元）"
        parts.append(seg)
    return "；".join(parts)


def build_result_view(result: dict, profile: dict, run_dir: str | Path) -> dict:
    """构建并落盘 UI View Model（result.json / opportunities.json / diagnostics.json）并更新索引。

    参数：result 运行结果；profile 画像；run_dir 本次运行目录。
    返回：view 字典（同时写入 run_dir/result.json）。
    """
    run_dir = Path(run_dir)
    discovery = {o.get("direction"): o.get("discovery_source")
                 for o in (result.get("opportunities") or [])}

    # 只保留含 lineage 的正式方案（无 lineage 的为降级占位，不进入 UI 列表）
    plans = [p for p in (result.get("plans") or []) if "lineage" in p]
    plan_index = [_plan_entry(p, discovery) for p in plans]

    # 汇总统计
    diags = result.get("diagnostics") or []
    diag_counts = {"关注": 0, "观察": 0, "提示": 0}
    for d in diags:
        sev = d.get("severity")
        if sev in diag_counts:  # 仅为可识别的三档计数，未知严重度不纳入
            diag_counts[sev] += 1

    s = result.get("summary") or {}
    direction_summary = {
        # 方向漏斗：侦察 → 分析 → 待观察 → 候选 → 排除，以及决策分布
        "recon": s.get("recon_directions", 0),
        "analyzed": s.get("analyzed", len(plans)),
        "watchlist": s.get("level1_watchlist", len(result.get("watchlist") or [])),
        "candidates": s.get("level0_candidates", len(result.get("candidates") or [])),
        "excluded": s.get("excluded_no_basis", len(result.get("excluded") or [])),
        "recommend": s.get("recommend", 0),
        "conditional": s.get("conditional", 0),
        "insufficient": s.get("insufficient", 0),
        "manual_review": s.get("manual_review", 0),
        "not_recommend": s.get("not_recommend", 0),
        "scenario_tax_impact_total": s.get("scenario_tax_impact_total", 0.0),
        "confirmed_tax_impact_total": s.get("confirmed_tax_impact_total", 0.0),
    }

    # 证据/完整度聚合
    all_facts = [f for p in plans for f in ((p.get("lineage") or {}).get("required_facts") or [])]
    agg = _fact_stats(all_facts)
    evidence_summary = {
        "confirmed": agg["direct"], "proxy": agg["proxy"], "missing": agg["missing"],
        "total": agg["total"],
    }
    completeness = {
        "data": agg["data_completeness"],
        "evidence": agg["evidence_completeness"],
        "by_direction": {e["direction"]: e["fact_stats"] for e in plan_index},
    }

    view = {
        "analysis_meta": {  # 本次分析的标识与时间
            "stock_code": result.get("stock_code"),
            "year": result.get("year"),
            "name": result.get("name"),
            "industry": result.get("industry"),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "opportunity_source": result.get("opportunity_source"),
        },
        "brief": _brief(plan_index, diags),  # 一句话简要建议（确定性拼装）
        "enterprise_summary": {k: profile.get(k) for k in ENTERPRISE_FIELDS},  # 企业关键指标
        "trend": result.get("history") or {},  # 历年趋势序列
        "diagnostics_summary": {**diag_counts, "items": diags},  # 诊断计数 + 明细
        "direction_summary": direction_summary,  # 方向漏斗与决策计数
        "plan_index": plan_index,  # 各方向列表项
        "evidence_summary": evidence_summary,  # 证据/事实完整度
        "completeness": completeness,  # 完整度（总体 + 分方向）
        "artifact_refs": {  # 关联产物文件名（供 UI 二次加载）
            "opportunities": "opportunities.json",
            "diagnostics": "diagnostics.json",
            "plans": "plans/",
        },
    }

    # 落盘三件套：result.json（轻索引）、opportunities.json、diagnostics.json
    save_json(view, run_dir / "result.json")
    save_json({"opportunities": result.get("opportunities") or [],
               "watchlist": result.get("watchlist") or [],
               "candidates": result.get("candidates") or [],
               "excluded": result.get("excluded") or []},
              run_dir / "opportunities.json")
    save_json({"items": diags}, run_dir / "diagnostics.json")
    update_index(run_dir.parent, view)
    log.info("result.json 已生成：%s", run_dir / "result.json")
    return view


def update_index(outputs_dir: Path, view: dict) -> None:
    """把本次分析条目合并进 outputs/index.json（同 股票代码+年份 覆盖，按代码/年排序）。"""
    outputs_dir = Path(outputs_dir)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    idx_path = outputs_dir / "index.json"
    entries = []
    if idx_path.exists():
        try:
            import json
            entries = json.loads(idx_path.read_text(encoding="utf-8")).get("analyses", [])
        except Exception:  # noqa: BLE001
            entries = []
    meta = view["analysis_meta"]
    ds = view["direction_summary"]
    # 索引条目：仅保留列表页需要的轻量字段，详情按 result_ref 二次加载
    entry = {
        "stock_code": meta["stock_code"], "year": meta["year"], "name": meta.get("name"),
        "industry": meta.get("industry"), "generated_at": meta["generated_at"],
        "analyzed": ds["analyzed"], "conditional": ds["conditional"],
        "insufficient": ds["insufficient"], "recommend": ds["recommend"],
        "brief": view.get("brief", ""),
        "result_ref": f"{meta['stock_code']}_{meta['year']}/result.json",
    }
    entries = [e for e in entries
               if not (e.get("stock_code") == entry["stock_code"] and e.get("year") == entry["year"])]
    # 先按 股票代码+年份 去重（覆盖旧条目），再统一排序，保证索引稳定
    entries.append(entry)
    entries.sort(key=lambda e: (e.get("stock_code", ""), e.get("year", 0)))
    save_json({"analyses": entries}, idx_path)
