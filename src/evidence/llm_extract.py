"""LLM 抽取通道（补充通道，优先级最低）。

流程：
    Document -> RAG 检索相关片段 -> LLM 抽取(JSON) -> **引用校验** -> Evidence(candidate)

防幻觉（强制）：
1. LLM 必须返回**原文引用片段** `quote`；
2. 程序校验：`quote` 必须出现在所检索到的 chunk 中；
3. 程序校验：返回的数值/文本必须出现在 `quote` 中；
4. 单位由程序按 quote 归一（不信任 LLM 的换算）。
未通过校验的候选一律丢弃。
"""
from __future__ import annotations

from src.common import get_logger
from src.evidence.contract import DOMAIN_FACT_TYPES
from src.evidence.ingest import document_fact

log = get_logger()

UNIT_FACTORS = {"元": 1.0, "万元": 1e4, "万": 1e4, "亿元": 1e8, "亿": 1e8, "千元": 1e3}

# 各事实类型的检索问句（用于 RAG）
FACT_QUERIES = {
    "rd_spend_sum": "研发投入 研发费用 金额",
    "is_rd_expense": "研发费用 金额",
    "rd_person": "研发人员 数量 人数",
    "gov_subsidy_total": "政府补助 计入当期损益 金额",
    "is_income_tax": "所得税费用 金额",
    "is_interest_expense": "利息支出 利息费用",
    "is_revenue": "营业收入 金额",
    "bs_total_assets": "资产总计 总资产",
    "bs_total_liabilities": "负债合计",
    "bs_fixed_assets": "固定资产",
    "is_hightech": "高新技术企业 资格 证书",
}


def _norm(s: str) -> str:
    """归一化文本：去除所有空白与中英文逗号，便于引用与数值的包含比对。"""
    return "".join(str(s or "").split()).replace(",", "").replace("，", "")


def _fmt_variants(v) -> set[str]:
    """枚举数值的多种常见写法（千分位/小数/取整等），用于引用校验时宽松匹配。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return {_norm(v)}
    out = set()
    for s in (f"{f:.2f}", f"{f:,.2f}", f"{f:.0f}", f"{f:,.0f}"):
        out.add(_norm(s))
        # 同时加入去掉尾随 0 与小数点的写法（如 100.00 -> 100）
        out.add(_norm(s.rstrip("0").rstrip(".")))
    return {x for x in out if x}


def validate_quote(quote: str, chunk: str, value, value_kind: str = "amount") -> bool:
    """程序校验：引用出现在 chunk，且数值/文本出现在引用中。"""
    if not quote or not chunk:
        return False
    q = _norm(quote)
    if q not in _norm(chunk):
        return False
    if value_kind == "text":
        return _norm(value) in q
    return any(v and v in q for v in _fmt_variants(value))


def _to_amount(num, unit: str) -> float | None:
    """把 (数值, 单位) 归一到元；无法解析返回 None。"""
    try:
        return float(str(num).replace(",", "")) * UNIT_FACTORS.get(str(unit or "").strip(), 1.0)
    except (TypeError, ValueError):
        return None


def _rule_extract(fact_type: str, text: str) -> list[dict]:
    """无 LLM 时的确定性兜底：复用关键词+正则。"""
    from src.evidence import pdf_parser
    kws = pdf_parser.DEFAULT_KEYWORDS.get(fact_type)
    if not kws:
        return []
    out = []
    for ft, val, unit, kind, raw in pdf_parser._iter_text_facts(text, {fact_type: kws}):
        out.append({"value": val, "unit": "元" if kind == "amount" else "", "caliber": "",
                    "quote": raw, "value_kind": kind})
    return out


def _llm_extract(llm, fact_type: str, text: str) -> list[dict]:
    """调用 LLM 从片段抽取事实；要求逐字引用，失败/未命中返回空列表。"""
    desc = DOMAIN_FACT_TYPES.get(fact_type, fact_type)
    prompt = (
        "你是财务事实抽取器。请仅依据下面【片段】抽取指定事实，"
        "必须给出原文引用 quote（逐字复制），不得编造。\n"
        f"目标事实：{fact_type}（{desc}）\n"
        "只输出 JSON：{\"found\": true/false, \"value\": 数值或文本, \"unit\": \"元/万元/亿元或空\", "
        "\"caliber\": \"合并/母公司或空\", \"quote\": \"原文片段\"}\n"
        f"【片段】\n{text[:2000]}"
    )
    try:
        data = llm.try_chat_json([{"role": "user", "content": prompt}])
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM 抽取失败：%s", str(exc)[:120])
        return []
    # 模型明确表示未找到时返回空，交由上层继续其它片段
    if not data or not data.get("found"):
        return []
    return [data]


def extract_document_facts(stock_code: str, fact_types: list[str], kb=None, llm=None,
                           doc_type: str | None = None, report_year: int | None = None,
                           document_id: str | None = None, top_k: int = 3,
                           use_llm: bool = True, max_per_fact: int = 3) -> list:
    """从文档语料检索并抽取候选事实（默认 candidate，需人工确认）。"""
    out, seen = [], set()
    filters = {"stock_code": str(stock_code).zfill(6)}
    if report_year is not None:
        filters["report_year"] = report_year
    if doc_type:
        filters["doc_type"] = doc_type
    if kb is None:
        from src.rag.store import get_kb
        kb = get_kb()

    for ft in fact_types:
        query = FACT_QUERIES.get(ft, DOMAIN_FACT_TYPES.get(ft, ft))
        try:
            hits = kb.search(query, corpus="document", top_k=top_k, filters=filters)
        except Exception as exc:  # noqa: BLE001
            log.warning("文档检索失败：%s", str(exc)[:120])
            hits = []
        n_ft = 0
        for h in hits:
            chunk = h.get("text") or ""
            items = _llm_extract(llm, ft, chunk) if (use_llm and llm is not None) else _rule_extract(ft, chunk)
            for it in items:
                quote = str(it.get("quote") or "")
                kind = it.get("value_kind") or ("text" if isinstance(it.get("value"), str)
                                                and not str(it.get("value")).replace(".", "").isdigit()
                                                else "amount")
                # 防幻觉硬校验：引用须出现在 chunk 且数值须出现在引用中，否则丢弃
                if not validate_quote(quote, chunk, it.get("value"), kind):
                    continue
                if kind == "text":
                    value = str(it.get("value"))
                else:
                    value = _to_amount(it.get("value"), it.get("unit"))
                    if value is None:
                        continue
                # 同 (事实, 值) 去重，避免多片段重复命中
                key = (ft, round(value, 2) if isinstance(value, float) else value)
                if key in seen:
                    continue
                seen.add(key)
                out.append(document_fact(
                    stock_code, ft, value, doc_type=h.get("doc_type", doc_type or "other"),
                    document_id=h.get("document_id", document_id or ""),
                    document_date=str(h.get("document_date", "")),
                    report_year=h.get("report_year", report_year),
                    unit="元" if kind == "amount" else "",
                    caliber=str(it.get("caliber") or ""),
                    quote=quote, location=f"chunk:{h.get('chunk_id', '')}",
                    extraction_method="llm_assisted" if (use_llm and llm is not None) else "pdf_text",
                    extraction_confidence=0.6 if (use_llm and llm is not None) else 0.5))
                n_ft += 1
                if n_ft >= max_per_fact:
                    break
            if n_ft >= max_per_fact:
                break
    log.info("文档抽取：%s 类事实 -> %s 条候选（已过引用校验）", len(fact_types), len(out))
    return out
