"""端到端筹划流水线：画像 -> 方向识别 -> Skill -> 共用数据池 -> 红蓝多轮辩论 -> 计算 -> Green -> 报告。

流程要点：
    1. 加载画像并做「证据采用」解析，直接口径证据可把关键输入升级为 DIRECT；
    2. 跑确定性诊断，作为方向发现与 Gate 的 L1 信号；
    3. Discovery 三级分类（L0/L1/L2）并受深度分析配额约束；
    4. 对入选方向构建数据池、跑 Red/Blue 多轮辩论、计算、Green 结论；
    5. 落盘产物（plans/、report.md、result.json 等）。
不变量：
    - 同一「输入指纹」且非 force 时复用缓存，指纹剔除时间戳保证可复现；
    - 所有对外结论都必须可回溯到数据池编号/政策/计算过程。
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.agents import blue, green, opportunity, red
from src.common import CONFIG, get_logger, save_json
from src.evidence.lineage import build_plan_document, save_plan
from src.evidence.pool import build_pool, summarize_pool
from src.report.markdown import render_report
from src.skills.engine import evaluate_skill, get_skills, row_to_dict
from src.skills.schema import match_skill_for_direction, skill_applicable

log = get_logger()

PROFILE = Path(CONFIG["paths"]["features_dir"]) / "profile_features.parquet"
OUTPUTS = Path(CONFIG["paths"]["outputs"])

# 议题词典（topic_id）：用于辩论进展判定；未命中一律 other（不丢信息）
TOPIC_RULES = [
    (r"辅助账|辅助帐", "RD_LEDGER"),
    (r"归集|范围|费用构成|人员人工|直接投入|其他相关费用|设计试验", "RD_SCOPE"),
    (r"资本化|费用化", "RD_CAPITALIZE"),
    (r"立项|项目计划|研发项目", "RD_PROJECT"),
    (r"资格|高新|科技型中小|证书|有效期", "QUALIFICATION"),
    (r"关联方|债资比|独立交易|关联交易", "RELATED_INTEREST"),
    (r"同期同类|利率", "INTEREST_RATE"),
    (r"设备|房屋|建筑物|单价|500万|资产类别", "ASSET_ELIGIBILITY"),
    (r"一次性扣除|加速折旧|折旧年限", "ASSET_DEDUCTION"),
    (r"补助|不征税|专项资金|拨付|递延收益", "GOV_EVIDENCE"),
    (r"亏损|结转|弥补", "LOSS_CARRY"),
    (r"申报表|纳税调整|汇算|申报", "TAX_FILING"),
    (r"台账|明细表|辅助资料", "LEDGER"),
    (r"证据|凭证|资料|证明", "EVIDENCE_GENERAL"),
    (r"口径|计算|基数|比例", "CALIBER"),
]


def _topic_id(issue: str) -> str:
    """把议题文本映射到稳定 topic_id（用于辩论进展判定）；未命中返回 other。

    采用「首个命中规则」而非全部匹配，保证同一议题在每轮得到一致编号，
    否则「无进展」判定会因编号漂移而失效。
    """
    import re as _re
    s = str(issue)
    for rx, tid in TOPIC_RULES:
        if _re.search(rx, s):
            return tid
    return "other"


def _similar_text(a: str, b: str, threshold: float = 0.85) -> bool:
    """近似文本判定（用于议题去重 / 无进展检测）。"""
    import re as _re
    from difflib import SequenceMatcher
    na = _re.sub(r"\s+", "", str(a))[:200]
    nb = _re.sub(r"\s+", "", str(b))[:200]
    if not na or not nb:
        return False
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def _merge_adopted_evidence(pool, red_d: dict, debate: list[dict]) -> list[str]:
    """把 Red 采用的新证据（Blue 上一轮的 N* 编号）并入 pool.policies，供 Green/报告引用。

    去重键用 doc_no 或 title：同一政策可能由不同轮次以不同编号检索到，
    仅按编号去重会重复计入，故以「文号/标题」作为业务主键。
    """
    adopted = red_d.get("adopted_new_evidence") or []
    if not adopted or not debate:
        return []
    prev_found = {e.get("nid"): e
                  for e in ((debate[-1].get("blue") or {}).get("evidence_found") or [])
                  if e.get("nid")}
    existing = {(p.get("doc_no") or p.get("title")) for p in pool.policies}
    merged = []
    for nid in adopted:
        item = prev_found.get(str(nid))
        if not item:
            continue
        key = item.get("doc_no") or item.get("title")
        if not key or key in existing:
            continue
        pool.policies.append({
            "id": f"P{len(pool.policies) + 1}",
            "title": item.get("title"), "doc_no": item.get("doc_no"),
            "date": item.get("date"), "channel": item.get("channel"), "url": "",
            "excerpt": "（由 Blue 取证、Red 采用）", "adopted_from": nid,
        })
        existing.add(key)
        merged.append(nid)
    if merged:
        log.info("  [%s] 回流并入 pool 的新证据：%s", pool.direction, merged)
    return merged

# 多年趋势参考字段（分析年份之前各年作为参考）
# 这些字段用于趋势类诊断与 Gate 的同比信号，只加载历史列以避免整表读入
HISTORY_FIELDS = [
    "is_revenue", "is_total_profit", "is_net_profit", "is_income_tax",
    "rd_spend_sum", "rd_expense_ratio", "asset_liability_ratio", "etr",
    "nominal_tax_rate", "gov_subsidy_total", "bs_total_assets", "cf_tax_paid",
    "gross_margin", "finance_expense_ratio", "employee_compensation_base",
    "impairment_total",
]


def load_profile(stock_code: str, year: int | None = None) -> dict:
    """读取指定股票代码（可选年份）的画像行，返回字典。

    stock_code 统一 zfill(6) 对齐；未指定年份时取最新一年；
    找不到代码/年份直接抛 ValueError（调用方据此提示，不静默返回空）。
    """
    df = pd.read_parquet(PROFILE)
    df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)
    sub = df[df["stock_code"] == stock_code.zfill(6)]
    if sub.empty:
        raise ValueError(f"画像中未找到股票代码 {stock_code}")
    if year is not None:
        sub = sub[sub["year"] == year]
        if sub.empty:
            raise ValueError(f"未找到 {stock_code} 的 {year} 年数据")
    row = sub.sort_values("year").iloc[-1]
    return row_to_dict(row)


def load_history(stock_code: str, analysis_year: int) -> dict:
    """加载分析年份及之前各年的关键字段，作为趋势参考。"""
    df = pd.read_parquet(PROFILE)
    df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)
    sub = df[(df["stock_code"] == stock_code.zfill(6)) & (df["year"] <= analysis_year)]
    if sub.empty:
        return {}
    sub = sub.sort_values("year")
    fields = [f for f in HISTORY_FIELDS if f in sub.columns]
    hist: dict[str, dict] = {}
    for _, r in sub.iterrows():
        # 以年份为键、字段字典为值；只保留画像中实际存在的字段
        y = int(r["year"])
        hist[y] = {f: row_to_dict(r).get(f) for f in fields}
    return hist


def _run_dir(stock: str, year: int) -> Path:
    """单次运行的产物目录：outputs/<股票代码>_<年份>/。"""
    return OUTPUTS / f"{stock}_{year}"


def _load_cached_result(run_dir: Path) -> dict | None:
    """从已保存产物重建一个最小 result（供缓存复用返回）。

    容错：任一产物缺失/损坏都不应让缓存复用整体失败，故逐文件 try/except 读取，
    读不到就用空值兜底；最终仍失败则返回 None，由调用方转为正常重跑。
    """
    def _read(name):
        fp = run_dir / name
        if not fp.exists():
            return None
        try:
            return json.loads(fp.read_text(encoding="utf-8"), strict=False)
        except Exception:  # noqa: BLE001
            return None

    try:
        plans = [json.loads(p.read_text(encoding="utf-8"), strict=False)
                 for p in sorted((run_dir / "plans").glob("*.json"))]
        meta = _read("run_meta.json") or {}
        summary = {}
        rv = _read("result.json")
        if rv:
            summary = rv.get("direction_summary") or {}
        diags = _read("diagnostics.json")
        opps = _read("opportunities.json")
        return {"stock_code": meta.get("stock_code"), "year": meta.get("year"),
                "name": meta.get("name"), "plans": plans, "summary": summary,
                "opportunities": (opps.get("items") if isinstance(opps, dict) else opps) or [],
                "diagnostics": (diags.get("items") if isinstance(diags, dict) else diags) or [],
                "warnings": []}
    except Exception:  # noqa: BLE001
        return None


def run_plan(stock_code: str, year: int | None = None, use_llm: bool = True,
             top_k: int | None = None, rounds: int | None = None, save: bool = True,
             progress=None, force: bool = False) -> dict:
    """progress(stage_name, pct) 可选回调，用于 UI「运行中」进度展示。

    force=False 时：若已存在同一"输入指纹"的缓存结果，则直接复用（不重跑）。
    """
    import time
    t0 = time.perf_counter()
    stage_marks: list[dict] = []
    warnings: list[str] = []          # 非致命失败（部分成功）：产物缺失/降级

    def _p(name: str, pct: int):
        stage_marks.append({"stage": name, "pct": int(pct),
                            "seconds": round(time.perf_counter() - t0, 2)})
        if progress:
            try:
                progress(name, pct)
            except Exception:  # noqa: BLE001
                pass

    # 输入指纹（用于缓存复用判定）
    from src.versioning import build_fingerprint
    try:
        fp_obj = build_fingerprint()
        fp_obj.pop("generated_at", None)          # 时间戳不参与缓存判定
        fp_hash = hashlib.md5(json.dumps(
            {"v": fp_obj, "stock": str(stock_code), "year": year,
             "use_llm": use_llm, "top_k": top_k, "rounds": rounds},
            ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        fp_hash = None

    _p("数据加载 / 画像", 5)
    profile = load_profile(stock_code, year)
    year = int(profile["year"])

    # 缓存复用：同一输入指纹 + 未强制 → 直接返回已保存结果
    if save and not force and fp_hash:
        run_dir0 = _run_dir(profile["stock_code"], year)
        meta_fp = run_dir0 / "run_meta.json"
        # 需要 run_meta 与 result.json 同时存在才可复用（否则产物不完整）
        if meta_fp.exists() and (run_dir0 / "result.json").exists():
            try:
                prev = json.loads(meta_fp.read_text(encoding="utf-8"))
                if prev.get("input_fingerprint") == fp_hash:
                    log.info("命中缓存（输入未变），复用已有结果：%s %s", profile["stock_code"], year)
                    cached = _load_cached_result(run_dir0)
                    if cached is not None:
                        cached["_cached"] = True
                        return cached
            except Exception as exc:  # noqa: BLE001
                log.warning("缓存判定失败：%s", str(exc)[:120])

    # 证据采用（Evidence Resolution）：空值写入 / manual 允许覆盖并留旧值 / 其余记冲突
    from src.evidence.resolution import direct_evidence_fields, resolve_profile
    profile, ev_resolution = resolve_profile(profile)
    # 直接口径证据 → 关键输入 PROXY 升级为 DIRECT（可支撑 CONFIRMED）
    direct_fields = direct_evidence_fields(profile.get("stock_code"), year)

    # 历史序列用于趋势类诊断与 Gate 的同比信号（分析年份及之前各年）
    history = load_history(profile["stock_code"], year)

    # 诊断引擎（确定性）：先跑一遍，作为发现/触发/辩论素材
    from src.rules.diagnostics import run_diagnostics
    diag_dicts = [d.to_dict() for d in run_diagnostics(profile, history)]
    log.info("观察信号 %s 项：%s", len(diag_dicts),
             [f"{d['name']}({d['severity']})" for d in diag_dicts])
    _p("税务观察", 15)
    top_k = int(top_k or CONFIG["rag"].get("top_k", 8))
    case_top_k = int(CONFIG["rag"].get("case_top_k", 3))
    configured_rounds = int(CONFIG.get("debate", {}).get("rounds", 3))
    early_stop = bool(CONFIG.get("debate", {}).get("early_stop", True))
    rounds = int(rounds if rounds is not None else configured_rounds)
    if not use_llm:
        rounds = 1  # 规则模式无辩论意义

    log.info("开始筹划分析: %s %s %s | 轮次=%s top_k=%s",
             profile.get("stock_code"), year, profile.get("short_name"), rounds, top_k)

    opps, src = opportunity.identify(profile, use_llm=use_llm, diagnostics=diag_dicts)
    log.info("识别到 %s 个方向（来源=%s）: %s", len(opps), src,
             [f"{o.get('direction')}[{o.get('discovery_source')}]" for o in opps])
    _p("方向发现", 25)

    try:
        from src.rag.store import get_kb
        kb = get_kb()
    except Exception as exc:  # noqa: BLE001
        log.warning("知识库不可用，跳过检索: %s", exc)
        warnings.append("知识库不可用，政策/案例检索已跳过（结果可能缺少政策依据）")
        kb = None

    skills = get_skills()
    plans: list[dict] = []
    excluded: list[dict] = []
    watchlist: list[dict] = []
    candidates: list[dict] = []
    pool_summaries: list[dict] = []

    from src.skills.gate import evaluate_gate, load_thresholds
    thresholds = load_thresholds()
    max_deep = int(CONFIG.get("discovery", {}).get("max_deep_directions", 8))   # 深度分析配额上限

    # ── 第一遍：Discovery 三级分类（无 LLM 调用）──
    level2: list[dict] = []
    for opp in opps:
        direction = opp.get("direction", "")
        skill = match_skill_for_direction(direction, skills)
        applicable = skill_applicable(skill, year) if skill else True
        skill_d = evaluate_skill(skill, profile, kb=kb, top_k=top_k,
                                 direct_fields=direct_fields).to_dict() if skill else None
        gate = evaluate_gate(direction, profile, history, skill_d, thresholds,
                             diagnostics=diag_dicts)
        item = {"direction": direction, "gate": gate, "skill": skill,
                "skill_d": skill_d, "opportunity": opp, "applicable": applicable}
        log.info("  [Gate] %s -> %s / %s（存在%s 异常%s）%s", direction, gate.level_name,
                 gate.status, gate.existence_count, gate.anomaly_count,
                 "" if applicable else " [Skill 生效期不适用]")
        if not applicable:
            watchlist.append({
                "direction": direction, "gate": gate.to_dict(), "opportunity": opp,
                "note": (f"Skill `{skill.id}` 政策生效期不适用"
                         f"（effective_from={skill.effective_from} ~ effective_to={skill.effective_to}）"),
            })
        elif gate.level < 0:
            excluded.append({"direction": direction, "gate": gate.to_dict(), "opportunity": opp})
        elif gate.level == 0:
            candidates.append({"direction": direction, "gate": gate.to_dict(), "opportunity": opp})
        elif gate.level == 1:
            watchlist.append({"direction": direction, "gate": gate.to_dict(), "opportunity": opp})
        else:
            level2.append(item)

    # ── 深度分析配额：L2 按（异常信号数, 置信度）排序取前 N ──
    # 排序口径：先比核心异常数量（越多越值得查），再比 AI 置信度，保证配额分配可解释。
    level2.sort(key=lambda x: (-x["gate"].anomaly_count,
                               -float(x["opportunity"].get("confidence") or 0)))
    for extra in level2[max_deep:]:
        watchlist.append({
            "direction": extra["direction"], "gate": extra["gate"].to_dict(),
            "opportunity": extra["opportunity"],
            "note": f"L2 政策匹配但超出深度分析配额（前 {max_deep} 个）",
        })
    deep = level2[:max_deep]
    _p("Evidence Gate", 35)
    # ── 第二遍：对入选方向做多代理深度分析 ──
    # 每个方向：构建数据池 -> Red/Blue 多轮辩论 -> Green 结论 -> 落盘 plan
    for di, item in enumerate(deep, 1):
        direction = item["direction"]
        opp = item["opportunity"]
        skill = item["skill"]
        skill_d = item["skill_d"]
        gate = item["gate"]
        _p(f"分析 {direction}（{di}/{len(deep)}）", 35 + int(55 * (di - 1) / max(len(deep), 1)))

        if skill is None:
            plans.append({
                "direction": direction,
                "final_decision": "需要专业人员进一步确认",
                "decision_code": "MANUAL_REVIEW",
                "note": "暂无对应 Tax Skill，仅记录方向与依据",
                "gate": gate.to_dict(),
                "opportunity": opp,
                "estimated_tax_impact": None,
            })
            continue

        pool = build_pool(direction, skill_d, profile, kb=kb, top_k=top_k,
                          case_top_k=case_top_k, diagnostics=diag_dicts)
        pool_summaries.append(summarize_pool(pool))

        debate: list[dict] = []
        red_d = blue_d = None
        prev_issues: list[str] = []
        termination = None
        for r in range(1, rounds + 1):
            red_d = red.analyze(direction, pool, profile, use_llm=use_llm,
                                round_no=r, history=debate)
            # 政策引用校验（防张冠李戴/编造）：校验 Red 的逐字引用是否属于所引政策
            from src.evidence.citation import validate_citations
            red_d["_citation_check"] = validate_citations(red_d.get("citations"), pool.policies)
            # 回流：把 Red 采用的新证据（Blue 上一轮检索到的 N*）并入 pool，供 Green/报告引用
            adopted = _merge_adopted_evidence(pool, red_d, debate)
            blue_d = blue.review(pool, red_d, use_llm=use_llm, round_no=r,
                                 history=debate, kb=kb)
            debate.append({"round": r, "red": red_d, "blue": blue_d})
            issues_now = [str(x) for x in (blue_d.get("issues") or [])]
            new_issues = [i for i in issues_now if not any(_similar_text(i, p) for p in prev_issues)]
            no_new_evidence = bool(red_d.get("no_new_evidence"))
            log.info("  [%s] 第%s轮 Red=%s / Blue compliant=%s / 新议题%s / 采用新证据%s / 无新证据=%s",
                     direction, r, red_d.get("_source"), blue_d.get("compliant"),
                     len(new_issues), len(adopted), no_new_evidence)
            if (early_stop and blue_d.get("compliant") is True
                    and not blue_d.get("required_evidence")
                    and not blue_d.get("conditional")):
                # 收敛条件：合规审查认可、无待补证据、且非「有条件」——三者齐备才可提前结束
                termination = f"第{r}轮：Blue 认可且无待补证据，提前收敛"
                break
            if r > 1 and not new_issues and not adopted and no_new_evidence:
                # 无进展终止：既无新议题、Red 也未采用新证据、且声明无新证据 → 继续辩论无收益
                termination = (f"第{r}轮：无新增议题、Red 未采用新证据且声明无新证据，"
                               f"判定『无进展』提前终止（关键证据需向企业索取）")
                log.info("  [%s] %s", direction, termination)
                break
            prev_issues += issues_now

        green_d = green.decide(direction, pool, red_d, blue_d, history=debate, use_llm=use_llm)
        doc = build_plan_document(profile["stock_code"], year, direction, opp,
                                  pool, skill_d, red_d, blue_d, green_d, debate, profile)
        doc["gate"] = gate.to_dict()
        doc["debate_termination"] = termination
        if skill is not None:
            doc["skill"] = {"id": skill.id, "name": skill.name, "version": skill.version,
                            "effective_from": skill.effective_from,
                            "effective_to": skill.effective_to}
        # 合规税务意见（Advisor）：与 Red/Blue/Green 同源，独立产出
        try:
            from src.agents.advisor import build_opinion
            doc["opinion"] = build_opinion(doc, kb=kb, use_llm=use_llm)
        except Exception as exc:  # noqa: BLE001
            log.warning("意见生成失败（%s）：%s", direction, str(exc)[:140])
            warnings.append(f"方向「{direction}」的合规税务意见生成失败")
        plans.append(doc)
        log.info("  [%s] 结论=%s (%s) | 影响=%s", direction,
                 doc.get("final_decision"), doc.get("decision_code"), doc.get("estimated_tax_impact"))

    # 跨方向引用（共享字段）
    _add_cross_references(plans)

    # 方案/决策案例入库（最小单位 = 单个方向的方案）——仅正式保存时写入（dry-run 不污染方案库）
    if save:
        try:
            from src.evidence.solution_kb import save_from_plans
            from src.evidence.store import EvidenceStore
            sol_ids = save_from_plans(EvidenceStore(), plans, profile)
            log.info("方案库写入 %s 条（待人工审定）", len(sol_ids))
        except Exception as exc:  # noqa: BLE001
            log.warning("方案库写入失败：%s", str(exc)[:160])
            warnings.append("方案库写入失败（案例评价将不含本次方案）")

    if save:
        _p("结果落盘", 95)
        run_dir = _run_dir(profile["stock_code"], year)
        for p in plans:
            if "lineage" in p:
                p["_saved_to"] = save_plan(p, run_dir)
        log.info("方案已保存到 %s（%s 份）", run_dir, len([p for p in plans if "lineage" in p]))

    result = {
        "stock_code": profile["stock_code"],
        "year": year,
        "name": profile.get("short_name"),
        "industry": profile.get("industry_name"),
        "opportunity_source": src,
        "opportunities": opps,
        "plans": plans,
        "diagnostics": diag_dicts,
        "excluded": excluded,
        "watchlist": watchlist,
        "candidates": candidates,
        "pool_summaries": pool_summaries,
        "history": history,
        "evidence_resolution": ev_resolution,
        "summary": _summary(plans, opps, excluded, watchlist, candidates),
    }

    # 运行指标（Token / 耗时 / 阶段）
    try:
        from src.llm.client import get_llm
        usage = get_llm().usage_summary()
    except Exception:  # noqa: BLE001
        usage = {}
    result["metrics"] = {
        "total_seconds": round(time.perf_counter() - t0, 2),
        "stages": stage_marks,
        "llm": usage,
        "use_llm": use_llm,
    }
    result["_input_fingerprint"] = fp_hash
    result["warnings"] = warnings

    if save:
        _save_run_artifacts(result, profile, use_llm, top_k, rounds, early_stop, kb)
    _p("完成", 100)
    return result


def _add_cross_references(plans: list[dict]) -> None:
    """为每个方向附上与其他方向的交叉引用（仅保留**有信息量**的共享字段）。

    排除公共字段（每个方向都有的 is_revenue/bs_total_assets 等），
    并剔除出现在 >50% 方向的"非特异"字段，避免"跨方向引用 = 公共字段列表"。
    """
    from src.evidence.pool import COMMON_FIELDS
    field_map: dict[str, set[str]] = {}
    policy_map: dict[str, set[str]] = {}
    case_map: dict[str, set[str]] = {}
    for p in plans:
        pool = (p.get("lineage") or {}).get("data_pool") or {}
        fields = {f["field"] for f in pool.get("company_facts", [])}
        fields |= {f["field"] for f in pool.get("related_fields", [])}
        field_map[p.get("direction")] = fields
        policy_map[p.get("direction")] = {x.get("doc_no") or x.get("title")
                                          for x in pool.get("policies", [])}
        case_map[p.get("direction")] = {x.get("title") for x in pool.get("cases", [])}

    # 字段出现频次 -> 剔除公共/非特异字段
    freq: dict[str, int] = {}
    for fs in field_map.values():
        for f in fs:
            freq[f] = freq.get(f, 0) + 1
    n_dir = max(len(field_map), 1)
    # 出现在 >50% 方向上的字段视为「非特异」，连同 COMMON_FIELDS 一起从交叉引用中剔除，
    # 否则每个方向都会互相「共享」一堆公共字段，引用失去信息量。
    ubiquitous = set(COMMON_FIELDS) | {f for f, c in freq.items() if c > 0.5 * n_dir}

    for p in plans:
        if "lineage" not in p:
            continue
        d = p.get("direction")
        refs = []
        for q in plans:
            if q is p or "lineage" not in q:
                continue
            qd = q.get("direction")
            shared_fields = sorted((field_map.get(d, set()) & field_map.get(qd, set())) - ubiquitous)
            shared_policies = sorted(x for x in (policy_map.get(d, set()) & policy_map.get(qd, set())) if x)
            shared_cases = sorted(x for x in (case_map.get(d, set()) & case_map.get(qd, set())) if x)
            if shared_fields or shared_policies or shared_cases:
                refs.append({
                    "direction": qd,
                    "decision": q.get("final_decision"),
                    "estimated_tax_impact": q.get("estimated_tax_impact"),
                    "shared_fields": shared_fields,
                    "shared_policies": shared_policies[:5],
                    "shared_cases": shared_cases[:5],
                })
        p["lineage"]["cross_references"] = refs


def _save_run_artifacts(result, profile, use_llm, top_k, rounds, early_stop, kb) -> None:
    """落盘单次运行的全部产物：画像快照、run_meta、metrics、report.md、UI view。

    设计：各子步骤用 try/except 包裹并降级为 warning，保证「部分成功」也能出结果；
    report.md 会先经 relabel_roles 做角色脱敏（失败则保留原文）。
    """
    run_dir = _run_dir(result["stock_code"], result["year"])
    run_dir.mkdir(parents=True, exist_ok=True)

    # 企业画像快照
    save_json(profile, run_dir / "profile.json")

    # 运行元信息
    # rag_backend 兼容不同知识库实现，缺失时标为 unknown/unavailable，不影响主流程
    rag_backend = getattr(getattr(kb, "policy", None), "backend", "unknown") if kb else "unavailable"
    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stock_code": result["stock_code"],
        "year": result["year"],
        "name": result.get("name"),
        "llm_model": CONFIG.get("llm", {}).get("model"),
        "llm_source": "llm" if use_llm else "rule",   # 本次运行使用 LLM 还是规则模式
        "rag_backend": rag_backend,
        "hybrid": CONFIG.get("rag", {}).get("hybrid"),
        "top_k": top_k,
        "case_top_k": CONFIG.get("rag", {}).get("case_top_k"),
        "candidates": CONFIG.get("rag", {}).get("candidates"),
        "evidence_review_rounds": rounds,
        "early_stop": early_stop,
        # 方向漏斗快照：便于事后核对各层级数量与决策分布
        "direction_funnel": {
            "recon": result.get("summary", {}).get("recon_directions"),
            "analyzed": result.get("summary", {}).get("analyzed"),
            "excluded": result.get("summary", {}).get("excluded_no_basis"),
            "recommend": result.get("summary", {}).get("recommend"),
            "conditional": result.get("summary", {}).get("conditional"),
        },
        "pool_summaries": result.get("pool_summaries"),
        "input_fingerprint": result.get("_input_fingerprint"),
        "metrics": result.get("metrics"),
    }
    try:
        from src.versioning import build_fingerprint
        meta["version"] = build_fingerprint()
    except Exception as exc:  # noqa: BLE001
        log.warning("版本指纹生成失败：%s", str(exc)[:120])
    save_json(meta, run_dir / "run_meta.json")

    # 运行指标（单次 metrics.json + 全局 _metrics.jsonl）
    # 单次快照便于回溯本次运行；追加到全局 jsonl 便于跨运行汇总分析
    try:
        if result.get("metrics"):
            save_json(result["metrics"], run_dir / "metrics.json")
            agg = {"stock_code": result.get("stock_code"), "year": result.get("year"),
                   "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   **result["metrics"]}
            with open(OUTPUTS / "_metrics.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(agg, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.warning("运行指标写入失败：%s", str(exc)[:120])

    # Markdown 报告
    md = render_report(result, profile, meta)
    try:
        from src.report.labels import relabel_roles
        md = relabel_roles(md)
    except Exception:  # noqa: BLE001
        pass
    (run_dir / "report.md").write_text(md, encoding="utf-8")
    log.info("报告已保存: %s", run_dir / "report.md")

    # UI View Model（result.json / opportunities.json / diagnostics.json + index.json）
    try:
        from src.report.view_model import build_result_view
        build_result_view(result, profile, run_dir)
    except Exception as exc:  # noqa: BLE001
        log.warning("result.json 生成失败：%s", str(exc)[:160])
        result.setdefault("warnings", []).append("result.json 生成失败（UI 可能无法展示该企业结果）")


def _summary(plans: list[dict], opportunities: list[dict] | None = None,
             excluded: list[dict] | None = None, watchlist: list[dict] | None = None,
             candidates: list[dict] | None = None) -> dict:
    """汇总方向漏斗计数与影响金额合计。

    关键口径：决策分布以 decision_code 统计（而非文案），避免文案变更导致统计静默归零；
    确认影响与情景测算分别累加，二者语义不可混加。
    """
    counts: dict[str, int] = {}
    total_impact = 0.0
    total_scenario = 0.0
    code_counts: dict[str, int] = {}
    for p in plans:
        d = p.get("final_decision", "未知")
        counts[d] = counts.get(d, 0) + 1
        code = p.get("decision_code") or green.DECISION_CODES.get(d)
        if code:
            code_counts[code] = code_counts.get(code, 0) + 1
        for key, acc in (("estimated_tax_impact", "c"), ("scenario_tax_impact", "s")):
            try:
                v = float(p.get(key))
            except (TypeError, ValueError):
                continue
            if acc == "c":
                total_impact += v       # 确认影响合计
            else:
                total_scenario += v     # 情景测算合计（与确认影响分开累加）
    return {
        "recon_directions": len(opportunities or []),
        "level2_analyzed": len(plans),
        "level1_watchlist": len(watchlist or []),
        "level0_candidates": len(candidates or []),
        "excluded_no_basis": len(excluded or []),
        "analyzed": len(plans),
        # 以决策码为准（文案变更不会导致统计静默归零）
        "recommend": code_counts.get("RECOMMEND", 0),
        "conditional": code_counts.get("CONDITIONAL", 0),
        "insufficient": code_counts.get("INSUFFICIENT_EVIDENCE", 0),
        "manual_review": code_counts.get("MANUAL_REVIEW", 0),
        "not_recommend": code_counts.get("NOT_RECOMMEND", 0),
        "decision_counts": counts,
        "decision_code_counts": code_counts,
        "confirmed_tax_impact_total": round(total_impact, 2),
        "scenario_tax_impact_total": round(total_scenario, 2),
    }
