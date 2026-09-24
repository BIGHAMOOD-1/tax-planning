"""导出（Update 5.2）：证据链 Markdown / 完整报告。

- `render_chain_markdown(plan_doc)`：把单个方向的 7 节点证据链渲染为 Markdown。
- `load_report_markdown(code, year)`：读取整份报告 report.md。
- `render_opinion_markdown(doc, opinion)`：把合规税务意见渲染为 Markdown。

约定：所有导出文本都以 AI 声明开头/结尾；缺失值统一显示为「—」。
用途：供 API/UI 下载，以及作为 PDF 渲染（pdf.py）的输入文本。
"""
from __future__ import annotations

from pathlib import Path

from src.common import CONFIG
from src.report.notice import AI_NOTICE, AI_NOTICE_SHORT

# 政策时效枚举的中文展示映射（未知值原样回显，避免丢失信息）
_POLICY_VALIDITY = {"VALID": "有效", "EXPIRED": "已失效",
                    "NOT_YET_EFFECTIVE": "尚未生效", "UNKNOWN": "未知（需人工核验）"}


def _money(v) -> str:
    """金额自适应格式化：≥1亿→亿元、≥1万→万元、否则元；None→「—」。"""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    a = abs(f)
    if a >= 1e8:
        return f"{f / 1e8:,.2f} 亿元"
    if a >= 1e4:
        return f"{f / 1e4:,.2f} 万元"
    return f"{f:,.2f} 元"


def _val(v) -> str:
    """证据链取值格式化：数值走金额格式，其余转字符串，None→「—」。"""
    if isinstance(v, (int, float)):
        return _money(v)
    return "—" if v is None else str(v)


def render_chain_markdown(doc: dict) -> str:
    """把单个方向的证据链渲染为 Markdown（7 节点 + 旁支说明）。

    7 节点：企业事实 / 诊断信号 / 税务方向 / 政策依据 / 条件核验 / 税额计算 / 最终结论。
    参数 doc 为 save_plan 落盘的 plan 文档（含 lineage、policy_validity 等）。
    """
    lin = doc.get("lineage") or {}
    calc = lin.get("calculation") or {}
    dp = lin.get("data_pool") or {}
    conds = dp.get("conditions") or {}
    facts = lin.get("required_facts") or []
    # 政策时效取 overall 汇总值；缺省视为 UNKNOWN（保守，不默认有效）
    pv = (doc.get("policy_validity") or {}).get("overall") or "UNKNOWN"

    L: list[str] = []
    L.append(f"# {doc.get('direction')} · 证据链报告")
    L.append("")
    L.append(AI_NOTICE_SHORT)
    L.append("")
    L.append(f"- 企业：{doc.get('stock_code')} · {doc.get('year')}")
    if (doc.get("skill") or {}).get("name"):
        L.append(f"- Skill：{doc['skill']['name']}（{doc['skill'].get('id')}）")
    L.append(f"- 结论：**{doc.get('final_decision')}**（{doc.get('decision_code')}）")
    L.append(f"- 确认税务影响：{_money(doc.get('estimated_tax_impact'))}")
    L.append(f"- 情景测算（非结论）：{_money(doc.get('scenario_tax_impact'))}")
    L.append(f"- 金额语义：{calc.get('calculation_type') or '—'}　"
             f"影响类型：{calc.get('impact_type') or '—'}")
    L.append(f"- 政策时效：{_POLICY_VALIDITY.get(pv, pv)}")
    L.append("")

    L.append("## ① 企业事实")
    # ① 企业事实：本方向实际用到的画像字段（含来源），是证据链的起点
    for f in dp.get("company_facts") or []:
        # 展示编号 + 人类名 + 取值 + 来源，保证可回溯
        L.append(f"- [{f.get('id')}] {f.get('label') or f.get('field')} = {_val(f.get('value'))}"
                 f"（来源：{f.get('source_text') or '—'}）")
    L.append("")

    L.append("## ② 诊断信号")
    # ② 诊断：本方向命中的观察信号，解释「为何值得查」
    diags = dp.get("diagnostics") or []
    L.append(f"- 命中 {len(diags)} 项")
    for d in diags:
        L.append(f"- [{d.get('severity')}] {d.get('name')}"
                 + (f"：{d.get('note')}" if d.get("note") else ""))
    L.append("")

    L.append("## ③ 税务方向")
    # ③ 方向：明确本证据链针对的筹划方向
    L.append(f"- {doc.get('direction')}")
    L.append("")

    L.append("## ④ 政策依据")
    # ④ 政策：列出依据文号/标题/日期与链接；带元数据便于判断来源可靠性
    for p in dp.get("policies") or []:
        meta = p.get("meta") or {}
        extra = ""
        if meta:
            # 附政策元数据（来源/元数据置信度/核验状态），让读者判断依据可靠性
            extra = (f"　[元数据 {meta.get('source')}/{meta.get('metadata_confidence')}"
                     f"/{meta.get('verification')}]")
        L.append(f"- [{p.get('id')}] {p.get('doc_no') or '（无文号）'} {p.get('title')}"
                 f"（{p.get('date')}）{extra}")
        if p.get("url"):
            L.append(f"  - 链接：{p['url']}")
    L.append("")

    L.append("## ⑤ 条件核验")
    # ⑤ 条件：先列不满足/未知（风险优先），再列通过项，便于快速定位缺口
    L.append(f"- 通过 {len(conds.get('passed') or [])} · "
             f"不满足 {len(conds.get('failed') or [])} · "
             f"未知 {len(conds.get('unknown') or [])}")
    for c in conds.get("failed") or []:
        L.append(f"- ✕ {c.get('desc') or c.get('field')}")
    for c in conds.get("unknown") or []:
        L.append(f"- ? {c.get('desc') or c.get('field')}")
    for c in conds.get("passed") or []:
        L.append(f"- ✓ {c.get('desc') or c.get('field')}")
    L.append("")

    L.append("## ⑥ 税额计算")
    # ⑥ 计算：分层展示计算器输出；不可计算时列出缺失输入，避免只给结论不给过程
    if calc.get("calculator"):
        # 计算器 id 帮助读者定位到具体算法
        L.append(f"- 计算器：{calc.get('calculator')}")
    for layer in calc.get("layers") or []:
        note = layer.get("note") or layer.get("source") or ""
        L.append(f"- {layer.get('layer')}：{_val(layer.get('value'))}"
                 + (f"（{note}）" if note else ""))
    if calc.get("not_calculable_inputs"):
        L.append(f"- 缺失输入：{'、'.join(calc['not_calculable_inputs'])}")
    L.append("")

    L.append("## ⑦ 最终结论")
    # ⑦ 结论：Green 的决策理由（与报告中「结论」一致，保证两处口径相同）
    green = ((lin.get("ai_process") or {}).get("green") or {})
    # 逐条输出结论理由
    for r in green.get("reasons") or []:
        L.append(f"- {r}")
    L.append("")

    L.append("### 旁支 · 审查说明（机会分析 / 合规审查）")
    # 旁支：完整辩论轨迹（每轮 Red/Blue 立场），主链之外的审计信息
    for h in lin.get("debate") or []:
        # 每轮一行 Red/Blue 立场，便于快速扫读对抗过程
        L.append(f"- 第 {h.get('round')} 轮")
        L.append(f"  - 机会分析：{(h.get('red') or {}).get('position') or '—'}")
        L.append(f"  - 合规审查：{(h.get('blue') or {}).get('position') or '—'}")
    L.append("")

    L.append("### 旁支 · Evidence / 来源")
    # 旁支：必需事实的具备情况与待补证据清单
    L.append(f"- 必需事实 {len(facts)} 项（{sum(1 for f in facts if f.get('present'))} 项已有）")
    # 待补证据：企业需提供的材料清单
    for e in lin.get("evidence_required") or []:
        L.append(f"- 待补：{e}")
    L.append("")
    L.append("---")
    L.append(f"**{AI_NOTICE}**")
    return "\n".join(L)


def load_report_markdown(code: str, year: int) -> str | None:
    """读取指定企业的整份报告 report.md；不存在返回 None。"""
    p = Path(CONFIG["paths"]["outputs"]) / f"{str(code).zfill(6)}_{year}" / "report.md"
    return p.read_text(encoding="utf-8") if p.exists() else None


def render_opinion_markdown(doc: dict, opinion: dict) -> str:
    """把单个方向的合规税务意见渲染为 Markdown。

    参数：doc 该方向 plan 文档（提供企业/结论信息）；opinion 意见结构
    （applicability/steps/materials/accounting_tax/red_lines/not_applicable/refs）。
    """
    op = opinion or {}
    L: list[str] = []
    L.append(f"# {doc.get('direction')} · 合规税务意见")
    L.append("")
    L.append(f"**{AI_NOTICE}**")
    L.append("")
    L.append(f"- 企业：{doc.get('stock_code')} · {doc.get('year')}")
    L.append(f"- 结论（Green）：**{doc.get('final_decision')}**（{doc.get('decision_code')}）")
    if op.get("summary"):
        L.append(f"- 意见摘要：{op['summary']}")
    L.append("")

    def section(title: str, items, numbered: bool = False):
        """渲染一个小节：items 为空则整节省略；numbered 控制有序/无序列表。"""
        if not items:
            return
        L.append(f"## {title}")
        for i, it in enumerate(items, 1):
            if isinstance(it, dict):
                step = it.get("step") or ""
                detail = it.get("detail") or ""
                L.append(f"{i}. **{step}**{'：' + detail if detail else ''}" if numbered
                         else f"- {step}{'：' + detail if detail else ''}")
            else:
                L.append(f"{i}. {it}" if numbered else f"- {it}")
        L.append("")

    section("一、适用条件与前提", op.get("applicability"))
    # 二、实施路径用有序列表（步骤有先后），其余用无序列表
    section("二、实施路径", op.get("steps"), numbered=True)
    section("三、需准备的资料/证据", op.get("materials"))
    section("四、会计与税务处理要点", op.get("accounting_tax"))
    section("五、合规红线与风险", op.get("red_lines"))
    section("六、不适用/需谨慎情形", op.get("not_applicable"))

    refs = op.get("refs") or []
    if refs:
        L.append("## 七、参考依据")
        # 参考依据逐条列文号/标题/链接，便于读者溯源核对
        for r in refs:
            rid = r.get("id") or ""
            no = r.get("doc_no") or "（无文号）"
            title = r.get("title") or ""
            url = r.get("url") or ""
            line = f"- [{rid}] {no} {title}"
            if url:
                line += f"（{url}）"
            L.append(line)
        L.append("")
    L.append("---")
    L.append(op.get("disclaimer") or "")
    return "\n".join(L) + "\n"
