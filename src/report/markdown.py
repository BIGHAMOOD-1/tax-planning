"""生成人工可读的 Markdown 分析报告。

结构：运行信息 -> 观察信号 -> 企业画像/趋势 -> 侦察方向 -> 逐方向证据审查
      -> 汇总/取证清单 -> 免责声明 -> 附录（待观察/候选/已排除）。
约定：金额统一按 亿/万/元 自适应显示；缺失值显示为「—」；结论与情景测算分列，
      不把情景估算混入「确认影响」。
"""
from __future__ import annotations

from datetime import datetime

from src.evidence.sources import lookup
from src.report.notice import AI_NOTICE, AI_NOTICE_SHORT

# 结论展示顺序（从强到弱）；用于需要按结论排序的场景
DECISION_ORDER = ["推荐", "需要专业人员进一步确认", "证据不足", "不推荐"]


def _fmt_money(v) -> str:
    """金额自适应格式化：≥1亿显示亿元、≥1万显示万元、否则元；不可转显示「—」。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(f) >= 1e8:
        return f"{f/1e8:,.2f} 亿元"
    if abs(f) >= 1e4:
        return f"{f/1e4:,.2f} 万元"
    return f"{f:,.2f} 元"


def _fmt(v) -> str:
    """通用格式化：float 去尾零，None 显示「—」，其余 str。"""
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return str(v)


def _fmt_layer(name: str, value, kind: str | None = None) -> str:
    """按分层类型显示：amount 金额 / ratio(0~1) 百分比 / percent(已是百分数) / text。"""
    if value is None:
        return "—"
    if kind == "text" or isinstance(value, str):
        return str(value)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return _fmt(value)
    if kind == "percent":
        return f"{f:.2f}%"          # 已是百分数（如名义税率），不再 ×100
    if kind == "ratio":
        return f"{f*100:.2f}%"      # 0~1 比率，×100 转百分比
    if kind == "amount":
        return _fmt_money(f)        # 金额按亿/万/元自适应
    # 无 kind 时的兜底启发式：仅"比率词 + 量级像比率"才按百分比
    if any(k in str(name) for k in ("比例", "税率", "占比", "比率")) and abs(f) <= 1.5:
        return f"{f*100:.2f}%"
    return _fmt_money(f)


def _url(u) -> str:
    """规范化政策链接：相对路径补官网前缀，其余原样返回。"""
    if not u:
        return ""
    u = str(u)
    if u.startswith("http"):
        return u
    if u.startswith("/"):
        # 税务总局法规库相对路径 → 补全官网域名
        return "https://fgk.chinatax.gov.cn" + u
    return u


def _why_no_confirmed(p: dict) -> list[str]:
    """解释某方向为何没有『确认税务影响』。

    归因顺序与计算语义一致：数据缺口 → 条件未确认 → 口径未验证 → 无计算依据，
    覆盖不到时给出兜底说明，避免出现空列表让报告留白。
    """
    lin = p.get("lineage") or {}
    calc = lin.get("calculation") or {}
    reasons: list[str] = []
    gaps = lin.get("data_gaps") or []
    if gaps:
        reasons.append(f"关键数据缺失（{'、'.join(gaps)}）")
    unknown = (lin.get("conditions") or {}).get("unknown") or []
    if unknown:
        reasons.append(f"适用条件未确认（{'、'.join(c.get('field', '') for c in unknown)}）")
    if calc.get("amount_is_estimate") and not calc.get("caliber_verified"):
        reasons.append("金额为估算、口径未经验证")
    if not calc.get("calculator"):
        reasons.append("无确定性计算依据（需专业判断）")
    if not reasons:
        reasons.append("尚未形成确定性计算")
    return reasons


def _no_deep_reason(item: dict) -> str:
    """未进入深度分析的原因：优先用显式 note，其次拼 Gate 的 reasons。"""
    note = item.get("note")
    if note:
        return note
    g = item.get("gate") or {}
    reasons = [r for r in (g.get("reasons") or []) if r]
    return "；".join(reasons) if reasons else "未进入深度分析"


PROFILE_FIELDS = [
    # (画像字段, 中文标签)；金额类与比率类在渲染时分别走不同格式化分支
    ("short_name", "证券简称"), ("industry_name", "行业"), ("province", "省份"), ("city", "城市"),
    ("equity_nature", "股权性质"), ("listing_state", "上市状态"), ("size_group", "规模分组"),
    ("is_revenue", "营业收入"), ("is_net_profit", "净利润"), ("bs_total_assets", "资产总计"),
    ("asset_liability_ratio", "资产负债率"), ("gross_margin", "毛利率"), ("net_margin", "净利率"),
    ("rd_spend_sum", "研发投入"), ("rd_expense_ratio", "研发费用率"), ("rd_person", "研发人员"),
    ("fixed_asset_ratio", "固定资产占比"), ("interest_debt_ratio", "有息负债率"),
    ("etr", "实际税率ETR"), ("nominal_tax_rate", "名义税率"),
    ("revenue_growth", "营收增长率"), ("gov_subsidy_total", "政府补助"),
    ("subsidiary_count", "子公司数"), ("subsidiary_overseas_count", "海外子公司数"),
]

LABEL_FIELDS = [
    # (画像标签字段, 中文标签)；值为 1 时在「企业标签」一行展示
    ("label_high_rd", "高研发"), ("label_heavy_asset", "重资产"), ("label_high_leverage", "高杠杆"),
    ("label_has_gov_subsidy", "有政府补助"), ("label_high_interest", "高利息负担"),
    ("label_declining_revenue", "收入下滑"), ("label_hightech", "高新技术资质"),
    ("label_overseas_group", "有港澳台子公司"), ("label_related_party_deals", "有关联交易"),
]


def render_report(result: dict, profile: dict, run_meta: dict) -> str:
    """把一次运行的完整结果渲染为 Markdown 报告文本。

    参数：result 运行结果（plans/opportunities/diagnostics/summary 等）；
          profile 企业画像；run_meta 运行元信息（时间/模型/检索配置）。
    返回：Markdown 字符串（含 AI 声明与免责声明）。

    章节顺序：
        一、运行信息 + 一·补充 观察信号；二、企业画像 + 二·补充 近5年趋势；
        三、侦察到的方向 + 三·补充 方向筛选漏斗；四、逐方向分析；
        五、汇总 + 为什么确认影响为 0；六、取证清单；七、免责声明；
        附录一/二/三：待观察 / 候选 / 已排除。
    """
    lines: list[str] = []
    name = result.get("name") or ""
    stock = result.get("stock_code")
    year = result.get("year")

    lines.append(f"# {name}（{stock}）{year} 年 税务筹划分析报告")
    lines.append("")
    lines.append(AI_NOTICE_SHORT)
    lines.append("")
    lines.append("> 本报告由智能税务筹划系统自动生成，所有结论均可回溯至企业数据、政策依据与计算过程。")
    lines.append("")

    # 一、运行信息
    # 交代「谁生成的、用什么模型/检索配置、方向漏斗各层级数量」，为结论提供运行上下文
    lines.append("## 一、运行信息")
    lines.append("")
    lines.append(f"- 生成时间：{run_meta.get('generated_at')}")
    lines.append(f"- 对话模型：{run_meta.get('llm_model')}（来源：{run_meta.get('llm_source')}）")
    lines.append(f"- 检索后端：{run_meta.get('rag_backend')}，Hybrid={run_meta.get('hybrid')}，"
                 f"Top-K={run_meta.get('top_k')}，候选={run_meta.get('candidates')}")
    lines.append("- 证据审查：多代理对抗式验证（机会分析 → 合规审查 → "
                 "Evidence Gate 证据门槛 → Calculator 计算 → Green 结论）")
    s = result.get("summary", {})
    lines.append(f"- 方向漏斗：侦察 **{s.get('recon_directions', 0)}** → "
                 f"L0 事实存在 **{s.get('level0_candidates', 0)}** → "
                 f"L1 异常信号 **{s.get('level1_watchlist', 0)}** → "
                 f"L2 政策匹配 **{s.get('level2_analyzed', 0)}**（进入深度分析）；"
                 f"排除 **{s.get('excluded_no_basis', 0)}**")
    lines.append("")

    # 一·补充：观察信号（诊断，按类型分组）
    # 强调这是「事实性观察」，只指出值得查，不代表已存在问题，避免读者误读为结论
    diags = result.get("diagnostics") or []
    lines.append("## 一·补充：观察信号（跨字段一致性/基准/时点/趋势）")
    lines.append("")
    if not diags:
        lines.append("本次分析未发现显著信号。")
        lines.append("")
    else:
        lines.append("> 以下为**事实性观察**（提示/观察/关注），仅指出『值得查』，"
                     "不代表已存在问题；成因由后续审查。")
        lines.append("")
        groups: dict[str, list] = {}
        for d in diags:
            # 按类别中文名分组（勾稽/推算/确认/趋势），便于读者按诊断性质阅读
            groups.setdefault(d.get("category_cn") or d.get("category"), []).append(d)
        for cat, items in groups.items():
            lines.append(f"**{cat}**")
            lines.append("")
            lines.append("| 严重度 | 观察 | 关联方向 | 实际 | 预期 | 偏差 |")
            lines.append("|---|---|---|---|---|---|")
            for d in items:
                lines.append(f"| {d.get('severity')} | {d.get('name')} | {d.get('direction')} | "
                             f"{_fmt_money(d.get('actual'))} | {_fmt_money(d.get('expected'))} | "
                             f"{(d.get('deviation') or 0)*100:.1f}% |")
            lines.append("")

    # 二、企业画像
    # 关键财务/税务指标快照 + 企业标签，作为后续方向分析的事实底座
    lines.append("## 二、企业画像")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|---|---|")
    for key, cn in PROFILE_FIELDS:
        if key not in profile:
            continue
        v = profile.get(key)
        if key in ("is_revenue", "is_net_profit", "bs_total_assets", "rd_spend_sum", "gov_subsidy_total"):
            v = _fmt_money(v)   # 金额类：亿/万自适应
        elif key in ("asset_liability_ratio", "gross_margin", "net_margin", "rd_expense_ratio",
                     "fixed_asset_ratio", "interest_debt_ratio", "etr", "revenue_growth"):
            v = "—" if v is None else f"{float(v)*100:.2f}%"   # 比率类：小数转百分比
        lines.append(f"| {cn} | {_fmt(v)} |")
    lines.append("")
    labels = [cn for key, cn in LABEL_FIELDS if profile.get(key) == 1]
    # 企业标签：把画像布尔标签拼成一行，帮助快速判断企业类型
    lines.append(f"**企业标签**：{'、'.join(labels) if labels else '（无）'}")
    lines.append("")

    # 二·补充：近5年趋势（分析年份之前作为参考）
    # 仅当历史存在时渲染；趋势用于识别同比突变等时间维度的观察信号
    history = result.get("history") or {}
    if history:
        _render_history(lines, history, year)

    # 三、识别到的方向（侦察结果）
    # 明确「侦察 ≠ 方案」，防止把 AI 猜测的方向当成最终可执行建议
    lines.append("## 三、侦察到的筹划方向")
    lines.append("")
    lines.append("> 这是**侦察结果**（AI 认为值得调查的方向），不等于最终方案。"
                 "经 Evidence Gate 后进入深度分析，最终可执行方案见第五部分。")
    lines.append("")
    lines.append("| 方向 | 置信度 | 判断理由 |")
    lines.append("|---|---|---|")
    for o in result.get("opportunities", []):
        lines.append(f"| {o.get('direction')} | {_fmt(o.get('confidence'))} | {o.get('rationale','')} |")
    lines.append("")

    # 三·补充：Discovery 三级 + Evidence Gate 漏斗
    # 把「进入深度分析 / 待观察 / 候选 / 已排除」四类方向合并展示，读者可核对筛选口径
    plans = result.get("plans", [])
    excluded = result.get("excluded", [])
    watchlist = result.get("watchlist", [])
    candidates = result.get("candidates", [])
    if plans or excluded or watchlist or candidates:
        lines.append("### 方向筛选（Discovery 三级 + Evidence Gate）")
        lines.append("")
        # 先列进入深度分析的方案，再列待观察/候选/排除，层级从高到低
        lines.append("| 方向 | 级别 | 证据门槛 | 命中信号(异常) | 说明 |")
        lines.append("|---|---|---|---|---|")
        for p in plans:
            g = p.get("gate") or {}
            lines.append(f"| {p.get('direction')} | {g.get('level_name', '-')} | {g.get('status', '-')} | "
                         f"{g.get('anomaly_count', '-')} | {'；'.join(g.get('reasons', [])[:1])} |")
        for w in watchlist:
            g = w.get("gate") or {}
            lines.append(f"| {w.get('direction')} | {g.get('level_name', 'L1')} | WATCH | "
                         f"{g.get('anomaly_count', '-')} | {'；'.join(g.get('reasons', [])[:1])} |")
        for c in candidates:
            g = c.get("gate") or {}
            lines.append(f"| {c.get('direction')} | {g.get('level_name', 'L0')} | CANDIDATE | 0 | "
                         f"仅事实存在，未命中异常信号 |")
        for e in excluded:
            g = e.get("gate") or {}
            lines.append(f"| {e.get('direction')} | 排除 | NO_BASIS | 0 | "
                         f"{'；'.join(g.get('reasons', [])[:1])} |")
        lines.append("")

    # 四、逐方向（多代理证据审查）
    # 每个方向独立成章，包含触发依据、结论、条件、辩论过程、计算与数据池
    lines.append("## 四、逐方向分析（多代理证据审查）")
    lines.append("")
    for p in result.get("plans", []):
        _render_direction(lines, p)

    # 五、汇总
    # 确认影响与情景测算分列展示，强调后者为假设性估算、不代表可实现收益
    lines.append("## 五、汇总")
    lines.append("")
    # 表头明确区分「确认税务影响」与「情景测算(非结论)」两列
    lines.append("| 方向 | 结论 | 确认税务影响 | 情景测算(非结论) |")
    lines.append("|---|---|---|---|")
    for p in result.get("plans", []):
        # 确认影响与情景测算分两列，避免读者把假设性估算当成可落地收益
        lines.append(f"| {p.get('direction')} | {p.get('final_decision')} "
                     f"({p.get('decision_code') or '-'}) | {_fmt_money(p.get('estimated_tax_impact'))} | "
                     f"{_fmt_money(p.get('scenario_tax_impact'))} |")
    lines.append("")
    s = result.get("summary", {})
    lines.append(f"**合计确认税务影响**：{_fmt_money(s.get('confirmed_tax_impact_total'))}"
                 f"　|　**合计情景测算（非结论）**：{_fmt_money(s.get('scenario_tax_impact_total'))}")
    lines.append("")

    # 为什么确认影响为 0（按原因归类）
    zero_plans = [p for p in result.get("plans", []) if p.get("estimated_tax_impact") is None]
    if zero_plans:
        # 把「无确认影响」的原因归入四类桶，避免读者误以为系统漏算
        buckets = {"口径未验证": [], "关键数据缺失": [], "适用条件未确认": [], "无确定性计算依据": []}
        for p in zero_plans:
            lin = p.get("lineage") or {}
            calc = lin.get("calculation") or {}
            d = p.get("direction")
            if lin.get("data_gaps"):
                buckets["关键数据缺失"].append(d)
            if (lin.get("conditions") or {}).get("unknown"):
                buckets["适用条件未确认"].append(d)
            if calc.get("amount_is_estimate") and not calc.get("caliber_verified"):
                buckets["口径未验证"].append(d)
            if not calc.get("calculator"):
                buckets["无确定性计算依据"].append(d)
        lines.append("**为什么确认影响为 0**")
        lines.append("")
        for k, v in buckets.items():
            if v:
                lines.append(f"- {k}：{'、'.join(v)}")
        lines.append("- → 只有补齐上述证据、验证口径后，才能产生确认影响；"
                     "表中「情景测算」为假设性估算，不代表可实现收益。")
        lines.append("")

    # 六、取证清单
    # 把「系统内数据缺口」与「需企业提供的材料」分开列示，明确下一步行动项
    lines.append("## 六、下一步取证清单")
    lines.append("")
    lines.append("> 这些材料补齐后，系统可继续完成确定性计算并给出最终结论。")
    lines.append("")
    any_item = False
    for p in result.get("plans", []):
        lin = p.get("lineage", {})
        gaps = lin.get("data_gaps", [])
        ev = lin.get("evidence_required", [])
        if not gaps and not ev:
            continue
        any_item = True
        blocks = "金额" if p.get("amount_is_estimate") else "资格/口径"   # 阻塞类型：金额估算 vs 资格口径
        lines.append(f"### {p.get('direction')}（结论：{p.get('final_decision')}；阻塞：{blocks}）")
        if gaps:
            lines.append(f"- 数据缺口（系统内缺失）：{'、'.join(gaps)}")
        if ev:
            lines.append("- 需企业提供的材料：")
            for e in ev:
                lines.append(f"  - [ ] {e}")
        lines.append("")
    if not any_item:
        lines.append("（本企业各方向均无待补证据）")
        lines.append("")

    lines.append("## 七、免责声明")
    # 免责声明：强调自动化分析不构成税务意见，估算金额须专业复核
    lines.append("")
    lines.append(f"**{AI_NOTICE}**")
    lines.append("")
    lines.append("本报告为基于公开结构化数据与公开政策的自动化分析结果，不构成税务意见或申报依据。"
                 "标注为「估算」的金额均未经税法口径调整与证据验证，实际适用须由专业税务人员复核确认。")
    lines.append("")

    # 附录：待观察 / 候选 / 已排除
    # 三级附录保证「被过滤掉的方向」也可被审阅，避免筛选过程成为黑盒
    watchlist = result.get("watchlist", [])
    candidates = result.get("candidates", [])
    if watchlist:
        lines.append("## 附录一：待观察方向（命中信号但未进入深度分析）")
        lines.append("")
        lines.append("| 方向 | 命中异常信号 | 未进深度分析原因 |")
        lines.append("|---|---|---|")
        for w in watchlist:
            g = w.get("gate") or {}
            sigs = "；".join(s["name"] for s in (g.get("signals") or []) if s.get("passed"))
            lines.append(f"| {w.get('direction')} | {sigs or '—'} | {_no_deep_reason(w)} |")
        lines.append("")
    if candidates:
        lines.append("## 附录二：候选方向（L0 仅事实存在）")
        lines.append("")
        lines.append("以下方向仅『事实存在』，未命中异常信号，未进入分析：")
        lines.append("")
        lines.append("| 方向 | 未进深度分析原因 |")
        lines.append("|---|---|")
        for c in candidates:
            lines.append(f"| {c.get('direction')} | {_no_deep_reason(c)} |")
        lines.append("")
    if excluded:
        lines.append("## 附录三：已排除方向（无事实基础）")
        lines.append("")
        lines.append("以下方向连『事实存在』都不满足，予以排除：")
        lines.append("")
        lines.append("| 方向 | 排除理由 |")
        lines.append("|---|---|")
        for e in excluded:
            g = e.get("gate") or {}
            lines.append(f"| {e.get('direction')} | {'；'.join(g.get('reasons', ['未满足事实存在']))} |")
        lines.append("")
    return "\n".join(lines)


def _render_history(lines: list[str], history: dict, analysis_year: int) -> None:
    """渲染近 N 年趋势表；金额按 _fmt_money、比率按百分比、名义税率按百分数原值显示。

    kind 区分：money（元）/ pct（小数转百分比）/ pctnum（已是百分数，如名义税率）。
    """
    years = sorted(int(y) for y in history.keys())
    if not years:
        return
    fields = [
        # (字段, 中文名, 类型)：类型决定格式化方式（money/pct/pctnum）
        ("is_revenue", "营业收入", "money"),
        ("is_total_profit", "利润总额", "money"),
        ("is_net_profit", "净利润", "money"),
        ("rd_spend_sum", "研发投入", "money"),
        ("rd_expense_ratio", "研发费用率", "pct"),
        ("asset_liability_ratio", "资产负债率", "pct"),
        ("etr", "实际税率", "pct"),
        ("nominal_tax_rate", "名义税率", "pctnum"),
        ("is_income_tax", "所得税费用", "money"),
        ("cf_tax_paid", "支付的各项税费", "money"),
        ("gov_subsidy_total", "政府补助", "money"),
    ]
    lines.append(f"**近 {len(years)} 年趋势（{years[0]}–{years[-1]}，{analysis_year} 为分析年份，其余为参考）**")
    lines.append("")
    header = "| 项目 | " + " | ".join(f"{y}{'（分析）' if y == analysis_year else ''}" for y in years) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(years) + 1))
    for key, cn, kind in fields:
        if not any(history.get(y, {}).get(key) is not None for y in years):
            continue   # 全为空的字段不渲染，避免整行「—」
        cells = []
        for y in years:
            v = history.get(y, {}).get(key)
            if v is None:
                cells.append("—")
            elif kind == "money":
                cells.append(_fmt_money(v))
            elif kind == "pct":
                cells.append(f"{float(v)*100:.2f}%")
            elif kind == "pctnum":
                cells.append(f"{float(v):.2f}%")
            else:
                cells.append(_fmt(v))
        lines.append(f"| {cn} | " + " | ".join(cells) + " |")
    lines.append("")


def _render_direction(lines: list[str], p: dict) -> None:
    """渲染单个方向的完整章节：触发观察 -> 结论 -> 条件 -> 审查过程 -> 计算 -> 数据池。

    输出顺序刻意「先讲为何被选中，再讲辩论与计算」，便于人工按证据链复核。
    """
    lin = p.get("lineage", {})
    g = p.get("gate") or {}
    lines.append(f"### {p.get('direction')}")
    lines.append("")

    # 触发与观察（诊断优先：先讲本方向为何被选中，再讲辩论）
    # core_sigs 只取核心异常，排除「观察信号：」前缀的诊断项（已在上一节展示）
    pd_diags = (lin.get("data_pool") or {}).get("diagnostics") or []
    # 核心触发信号 = 本方向核心异常，且排除诊断注入的「观察信号：」条目（避免重复）
    core_sigs = [s for s in (g.get("signals") or [])
                 if s.get("level") == "anomaly" and s.get("passed") and s.get("core", True)
                 and not str(s.get("name", "")).startswith("观察信号：")]
    if pd_diags or core_sigs:
        lines.append("**触发与观察（本方向为何被选中）**")
        lines.append("")
        for i, d in enumerate(pd_diags):
            role = "核心触发信号" if i == 0 else "关联观察信号"
            lines.append(f"- [{d.get('severity')}·{role}] {d.get('name')}："
                         f"实际 {_fmt_money(d.get('actual'))} / 预期 {_fmt_money(d.get('expected'))}"
                         f"（偏差 {(d.get('deviation') or 0)*100:.1f}%）")
            if d.get("statement"):
                lines.append(f"  - 事实：{d.get('statement')}")
        for s in core_sigs:
            lines.append(f"- [核心异常] {s.get('name')}：实际 {_fmt(s.get('value'))}"
                         f"（阈值 {_fmt(s.get('threshold'))}）")
        lines.append("")

    lines.append(f"- **结论**：{p.get('final_decision')}（{p.get('decision_code') or '-'}）")
    lines.append(f"- **确认税务影响**：{_fmt_money(p.get('estimated_tax_impact'))}")
    if p.get("estimated_tax_impact") is None:
        lines.append(f"- **为何无确认影响**：{'；'.join(_why_no_confirmed(p))}")
    if p.get("scenario_tax_impact") is not None:
        lines.append(f"- **情景测算（非结论）**：{_fmt_money(p.get('scenario_tax_impact'))}"
                     f"　（假设性估算，不代表可实现收益）")
    if p.get("debate_termination"):
        lines.append(f"- **审查终止**：{p.get('debate_termination')}")
    sk = p.get("skill")
    if sk:
        lines.append(f"- **Skill**：`{sk.get('id')}` v{sk.get('version')}"
                     f"（生效 {sk.get('effective_from') or '—'} ~ {sk.get('effective_to') or '—'}）")
    if g:
        lines.append(f"- **发现级别**：{g.get('level_name', '-')}；证据门槛 {g.get('status')}"
                     f"（存在 {g.get('existence_count', 0)} / 异常 {g.get('anomaly_count', 0)}）")
        for sig in (g.get("signals") or []):
            mark = "✅" if sig.get("passed") else "✗"
            lines.append(f"  - {mark} [{sig.get('level')}] {sig.get('name')}：实际 {_fmt(sig.get('value'))}"
                         f"（阈值 {_fmt(sig.get('threshold'))}）")
    lines.append("")

    # 资格条件
    # passed/failed/unknown 三分：unknown 表示数据缺失无法判断，须与「不满足」区分
    cond = lin.get("conditions", {})
    if cond:
        lines.append("**适用条件**")
        lines.append("")
        lines.append("| 阶段 | 条件 | 期望 | 实际 | 结果 |")
        lines.append("|---|---|---|---|---|")
        for c in cond.get("passed", []):
            lines.append(f"| {c.get('stage','')} | {c.get('desc') or c.get('field')} | "
                         f"{c.get('op')} {_fmt(c.get('expect'))} | {_fmt(c.get('actual'))} | ✅ 通过 |")
        for c in cond.get("failed", []):
            lines.append(f"| {c.get('stage','')} | {c.get('desc') or c.get('field')} | "
                         f"{c.get('op')} {_fmt(c.get('expect'))} | {_fmt(c.get('actual'))} | ❌ 不满足 |")
        for c in cond.get("unknown", []):
            lines.append(f"| {c.get('stage','')} | {c.get('desc') or c.get('field')} | "
                         f"{c.get('op')} {_fmt(c.get('expect'))} | 数据缺失 | ⚠ 无法判断 |")
        lines.append("")

    # 输入合理性校验
    # 展示数据池构建阶段发现的字段口径异常（如折旧/存量严重不匹配），提示人工核实
    warns = (lin.get("data_pool") or {}).get("warnings", [])
    if warns:
        lines.append("**输入合理性提示**")
        lines.append("")
        for w in warns:
            icon = {"warning": "⚠", "info": "ℹ"}.get(w.get("level"), "•")
            lines.append(f"- {icon} {w.get('message')}（`{w.get('field')}`）")
        lines.append("")

    # 多代理证据审查过程
    # 逐轮展示 Red 主张/回应与 Blue 质疑/取证，形成可审计的对抗式验证轨迹
    debate = lin.get("debate", [])
    if debate:
        lines.append("**证据审查过程**（机会分析 → 合规审查）")
        lines.append("")
        for h in debate:
            lines.append(f"- **第 {h.get('round')} 轮**")
            red = h.get("red") or {}
            blue = h.get("blue") or {}
            lines.append(f"  - 🔴 机会分析：{red.get('position') or red.get('benefit_summary') or '—'}")
            for pt in (red.get("supporting_points") or [])[:4]:
                lines.append(f"    - {pt}")
            for r in (red.get("responses_to_blue") or []):
                lines.append(f"    - 回应：{r.get('issue')} → {r.get('response')}")
            lines.append(f"  - 🔵 合规审查：{blue.get('position') or blue.get('reasoning') or '—'}"
                         f"（compliant={blue.get('compliant')}，conditional={blue.get('conditional')}）")
            for it in (blue.get("issues") or [])[:4]:
                lines.append(f"    - 质疑：{it}")
            for ef in (blue.get("evidence_found") or [])[:3]:
                lines.append(f"    - 🔎 合规审查取证：{ef.get('title')}（{ef.get('channel')} {ef.get('date')}）")
        lines.append("")

    # 政策引用校验（防张冠李戴/编造）
    # 跨轮次去重（policy_id + 引用片段）后统一展示，避免同一引用重复罗列
    checks: list[dict] = []
    seen_chk = set()
    for h in debate:
        for c in ((h.get("red") or {}).get("_citation_check") or []):
            key = (c.get("policy_id"), c.get("quote"))
            if key in seen_chk:
                continue
            seen_chk.add(key)
            checks.append(c)
    for c in ((lin.get("ai_process") or {}).get("red") or {}).get("_citation_check") or []:
        key = (c.get("policy_id"), c.get("quote"))
        if key not in seen_chk:
            seen_chk.add(key)
            checks.append(c)
    if checks:
        n_bad = sum(1 for c in checks if not c.get("valid"))
        lines.append(f"**政策引用校验**（共 {len(checks)} 条，未通过 {n_bad} 条）")
        lines.append("")
        for c in checks:
            mark = "✅" if c.get("valid") else "⚠"
            quote = str(c.get("quote") or "")
            lines.append(f"- {mark} [{c.get('policy_id')}] {c.get('doc_no') or c.get('title') or ''}："
                         f"{c.get('reason')}")
            if quote:
                lines.append(f"  - 引用片段：{quote[:160]}")
        lines.append("")

    # 计算
    # 分层展示计算过程（金额/比率按 kind 正确显示），并附计算假设供复核
    calc = lin.get("calculation") or {}
    layers = calc.get("layers", [])
    if layers:
        lines.append("**计算过程（分层）**")
        lines.append("")
        for layer in layers:
            note = f"　（{layer.get('note')}）" if layer.get("note") else ""
            lines.append(f"- {layer.get('layer')}："
                         f"{_fmt_layer(layer.get('layer'), layer.get('value'), layer.get('kind'))}{note}")
        if calc.get("assumptions"):
            lines.append("")
            lines.append("**计算假设**")
            for a in calc["assumptions"]:
                lines.append(f"- {a}")
        lines.append("")

    # 数据池
    # 数据池是「所见即所证」的核心：逐条列出本次实际调用的字段/政策/案例及编号
    pool = lin.get("data_pool") or {}
    if pool:
        lines.append("**本次调用的数据（共用数据池）**")
        lines.append("")
        if pool.get("company_facts"):
            lines.append("企业事实：")
            for f in pool["company_facts"]:
                src = f.get("source_text") or ""
                lines.append(f"- [{f['id']}] {f['field']} = {_fmt(f.get('value'))}"
                             + (f"　来源：{src}" if src else ""))
        if pool.get("policies"):
            lines.append("")
            lines.append("政策依据：")
            for pol in pool["policies"]:
                lines.append(f"- [{pol['id']}] {pol.get('doc_no') or '（无文号）'} "
                             f"{pol.get('title','')}（{pol.get('date','')}）")
                ex = str(pol.get("excerpt") or "").strip()
                if ex:
                    lines.append(f"  - 原文片段：{ex[:200]}")
                if pol.get("url"):
                    lines.append(f"  - {_url(pol['url'])}")
        if pool.get("cases"):
            lines.append("")
            lines.append("参考案例：")
            for c in pool["cases"]:
                lines.append(f"- [{c['id']}] {c.get('title','')}（{c.get('channel','')} {c.get('date','')}）")
        if pool.get("related_fields"):
            lines.append("")
            lines.append("相关但未直接使用的数据（供人工参考）：")
            for f in pool["related_fields"]:
                src = f.get("source_text") or ""
                lines.append(f"- [{f['id']}] {f['field']} = {_fmt(f.get('value'))}"
                             + (f"　来源：{src}" if src else ""))
        lines.append("")

    # 跨方向引用
    # 只展示有信息量的共享字段/政策/案例，公共字段已在 _add_cross_references 中剔除
    cross = lin.get("cross_references", [])
    if cross:
        lines.append("**跨方向引用**")
        lines.append("")
        for c in cross:
            parts = []
            if c.get("shared_fields"):
                parts.append(f"共享字段：{'、'.join(c['shared_fields'])}")
            if c.get("shared_policies"):
                parts.append(f"共享政策：{'、'.join(c['shared_policies'])}")
            if c.get("shared_cases"):
                parts.append(f"共享案例：{len(c['shared_cases'])} 篇")
            lines.append(f"- 与「{c['direction']}」（结论：{c.get('decision')}）—— " + "；".join(parts))
        lines.append("")

    # AI 使用痕迹
    # 汇总 Red/Blue 实际引用的数据池编号，便于核对「结论是否真的用了这些证据」
    red = lin.get("ai_process", {}).get("red") or {}
    blue = lin.get("ai_process", {}).get("blue") or {}
    used = list(dict.fromkeys((red.get("used_pool_ids") or []) + (blue.get("used_pool_ids") or [])))
    if used:
        lines.append(f"**审查引用的数据编号**：{'、'.join(used)}")
        lines.append("")

    # Green 理由
    # Green 的结论理由逐条列出，作为「为什么是此决策码」的可解释依据
    green = lin.get("ai_process", {}).get("green") or {}
    if green.get("reasons"):
        lines.append("**Green 结论理由**")
        for r in green["reasons"]:
            lines.append(f"- {r}")
        lines.append("")

    # 待专业确认（MANUAL_REVIEW 可执行说明）
    # 区分「需人工确认」与「证据不足」：前者是方向可行但依赖专业判断，不是缺数据
    mr = lin.get("manual_review")
    if mr:
        lines.append("**待专业确认（MANUAL_REVIEW 说明）**")
        lines.append("")
        lines.append(f"- 为何需人工确认：{mr.get('why_manual_review')}")
        lines.append(f"- 为何不是『证据不足』：{mr.get('not_insufficient_reason')}")
        for x in (mr.get("confirm_items") or []):
            lines.append(f"- [ ] 待确认：{x}")
        for x in (mr.get("possible_conclusions") or []):
            lines.append(f"- 结论走向：{x}")
        lines.append("")

    # 合规税务意见（Advisor · 面向行动）
    # 与 Red/Blue/Green 同源、独立产出，给出可落地的条件/路径/资料/红线
    op = p.get("opinion") or {}
    if op:
        lines.append("**合规税务意见（面向行动）**")
        lines.append("")
        if op.get("summary"):
            lines.append(f"- 意见摘要：{op['summary']}")
        if op.get("applicability"):
            lines.append("- 适用条件与前提：")
            for x in op["applicability"]:
                lines.append(f"  - {x}")
        if op.get("steps"):
            lines.append("- 实施路径：")
            for i, s in enumerate(op["steps"], 1):
                if isinstance(s, dict):
                    lines.append(f"  {i}. {s.get('step')}：{s.get('detail') or ''}")
                else:
                    lines.append(f"  {i}. {s}")
        if op.get("materials"):
            lines.append("- 需准备的资料/证据：")
            for x in op["materials"]:
                lines.append(f"  - {x}")
        if op.get("accounting_tax"):
            lines.append("- 会计与税务处理要点：")
            for x in op["accounting_tax"]:
                lines.append(f"  - {x}")
        if op.get("red_lines"):
            lines.append("- 合规红线与风险：")
            for x in op["red_lines"]:
                lines.append(f"  - {x}")
        if op.get("not_applicable"):
            lines.append("- 不适用/需谨慎情形：")
            for x in op["not_applicable"]:
                lines.append(f"  - {x}")
        refs = op.get("refs") or []
        if refs:
            lines.append("- 参考依据：")
            for r in refs:
                lines.append(f"  - [{r.get('id')}] {r.get('doc_no') or '（无文号）'} {r.get('title') or ''}")
        if op.get("disclaimer"):
            lines.append("")
            lines.append(f"> {op['disclaimer']}")
        lines.append("")
