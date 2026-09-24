"""API 适配层：读取既有产物（outputs / profile / evidence.db / KB），不承载业务逻辑。

设计原则：
- 路由层只做参数编排，所有「读取产物 + 轻量补全」集中在此，便于统一口径；
- 产物缺失或损坏时返回 None/空列表而非抛异常，保证接口在部分产物缺失时仍可用；
- 部分字段（人类名、绝对链接、结论状态）在读取时补全，避免为展示而重跑流水线。
"""
from __future__ import annotations

import json
from pathlib import Path

from src.common import CONFIG, ROOT, abs_url, get_logger

log = get_logger()

OUTPUTS = Path(CONFIG["paths"]["outputs"])
FEATURES = Path(CONFIG["paths"]["features_dir"])
PROCESSED = Path(CONFIG["paths"]["data_processed"])
VALIDATION = ROOT / "validation"

_BAD_NOTE_TOKENS = ("conditions_met", "conditions_failed", "conditions_unknown",
                    "data_gaps", "required_evidence", "规则1", "规则2", "规则3", "规则4",
                    "规则 1", "规则 2", "规则 3", "规则 4")


def _enrich_plan(d: dict) -> dict:
    """读取时补全：字段人类名 / 政策绝对链接 / 过滤 LLM 规则复述行。

    仅做展示层增强，不改动方案的核心结论与证据链结构。
    """
    from src.evidence.sources import field_label
    lin = d.get("lineage") or {}
    dp = lin.get("data_pool") or {}
    for key in ("company_facts", "related_fields"):
        for f in (dp.get(key) or []):
            lab = field_label(str(f.get("field") or ""))
            f.setdefault("label", lab["label"])
            f.setdefault("formula", lab["formula"])
    for f in (lin.get("required_facts") or []):
        if not f.get("label"):
            lab = field_label(str(f.get("field") or ""))
            f["label"] = lab["label"]
            f["source"] = lab["source"]
            f["formula"] = lab["formula"]
    for pol in (dp.get("policies") or []):
        pol["url"] = abs_url(pol.get("url"))
    green = ((lin.get("ai_process") or {}).get("green") or {})
    note = green.get("ai_note")
    if note:
        green["ai_note"] = [x for x in note
                            if not any(b in str(x) for b in _BAD_NOTE_TOKENS)]

    # 具体措施（读取时拼装，无需重跑）：
    #   measures    = 确定性行动清单（补齐材料）
    #   measures_ai = AI 建议措施（取最后一轮 Red 的 measures）
    plan = d.get("plan") or {}
    ev = lin.get("evidence_required") or []
    plan["measures"] = [f"补齐材料：{e}" for e in ev] or list(plan.get("measures") or [])
    debate = lin.get("debate") or []
    red_measures = (debate[-1].get("red") or {}).get("measures") if debate else None
    plan["measures_ai"] = list(red_measures or [])
    d["plan"] = plan
    return d

_profile_cols_cache = None
_company_cache = None


# ---------------------------------------------------------------- outputs

def list_analyses() -> list[dict]:
    """列出已有分析条目（读取 index.json 并剔除产物已删除的孤儿条目）。"""
    p = OUTPUTS / "index.json"
    if not p.exists():
        return []
    try:
        entries = json.loads(p.read_text(encoding="utf-8")).get("analyses", [])
    except Exception:  # noqa: BLE001
        return []
    # 读取时重算 brief（优先 推荐/CONFIRMED），无需重跑；并过滤"孤儿条目"（产物已被删除）
    from src.report.view_model import _brief
    out: list[dict] = []
    for e in entries:
        # 目录已被清理但索引仍残留时跳过，避免前端点进去 404
        if not (_run_dir(e.get("stock_code", ""), e.get("year") or 0) / "result.json").exists():
            continue
        try:
            v = load_result(e.get("stock_code"), e.get("year"))
            if v:
                e["brief"] = _brief(v.get("plan_index") or [],
                                    (v.get("diagnostics_summary") or {}).get("items") or [])
        except Exception:  # noqa: BLE001
            pass
        out.append(e)
    return out


def _run_dir(code: str, year: int) -> Path:
    """产物目录约定：outputs/{6位代码}_{年度}。"""
    return OUTPUTS / f"{str(code).zfill(6)}_{year}"


def load_result(code: str, year: int) -> dict | None:
    """读取某企业/年度的 result.json，并重算各结论的 status_key。"""
    p = _run_dir(code, year) / "result.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    # 读取时重算结论状态（结论 + 金额），避免旧 fixture 状态键过期
    try:
        from src.report.view_model import status_key
        for e in (d.get("plan_index") or []):
            e["status_key"] = status_key(e.get("decision_code"), e.get("impact_type"))
    except Exception:  # noqa: BLE001
        pass
    return d


def load_plan(code: str, year: int, direction: str) -> dict | None:
    """按方向读取方案 JSON（方向可命中 direction 字段或文件名），并做展示层增强。"""
    plans_dir = _run_dir(code, year) / "plans"
    if not plans_dir.exists():
        return None
    for p in plans_dir.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        # 兼容两种命名：direction 字段或文件主干名
        if str(d.get("direction")) == str(direction) or p.stem == direction:
            from src.report.labels import relabel_roles_deep
            return relabel_roles_deep(_enrich_plan(d))
    return None


def analysis_metrics(code: str, year: int) -> dict | None:
    """运行指标（Token / 耗时 / 阶段）。"""
    p = _run_dir(code, year) / "metrics.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _plan_file(code: str, year: int, direction: str) -> Path | None:
    """定位方案文件路径（与 load_plan 的匹配口径一致），供回写使用。"""
    plans_dir = _run_dir(code, year) / "plans"
    if not plans_dir.exists():
        return None
    for p in plans_dir.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if str(d.get("direction")) == str(direction) or p.stem == direction:
            return p
    return None


def plan_opinion(code: str, year: int, direction: str, refresh: bool = False) -> dict | None:
    """合规税务意见：命中缓存直接返回；否则（重新）生成并写回方案文件。"""
    d = load_plan(code, year, direction)
    if not d:
        return None
    if d.get("opinion") and not refresh:
        return d["opinion"]
    from src.agents.advisor import build_opinion
    kb = None
    try:
        from src.rag.store import get_kb
        kb = get_kb()
    except Exception:  # noqa: BLE001
        kb = None
    opinion = build_opinion(d, kb=kb, use_llm=True)
    d["opinion"] = opinion
    fp = _plan_file(code, year, direction)
    if fp is not None:
        try:
            from src.common import save_json
            save_json(d, fp)
        except Exception:  # noqa: BLE001
            pass
    return opinion


# ---------------------------------------------------------------- companies

def _company_frame():
    """加载企业画像特征表（仅取展示所需列），结果进程内缓存避免重复读盘。"""
    global _company_cache
    if _company_cache is None:
        import pandas as pd
        cols = ["stock_code", "short_name", "industry_name", "year"]
        df = pd.read_parquet(FEATURES / "profile_features.parquet", columns=cols)
        # 统一代码为 6 位字符串，避免前导零丢失导致匹配失败
        df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)
        _company_cache = df
    return _company_cache


def list_companies(q: str | None = None, limit: int = 50) -> list[dict]:
    """按企业聚合画像表，返回代码/简称/行业/覆盖年度；q 支持代码或名称模糊匹配。"""
    df = _company_frame()
    g = df.groupby("stock_code")
    out = []
    for code, sub in g:
        # 同一企业多年多行，取首个非空简称/行业作为代表值
        name = sub["short_name"].dropna().iloc[0] if sub["short_name"].notna().any() else None
        ind = sub["industry_name"].dropna().iloc[0] if sub["industry_name"].notna().any() else None
        if q:
            qq = str(q)
            if qq not in code and (not name or qq not in str(name)):
                continue
        out.append({"stock_code": code, "short_name": name, "industry_name": ind,
                    "years": sorted(int(y) for y in sub["year"].dropna().unique())})
        # 提前截断，避免对全量企业做无谓的后续处理
        if len(out) >= limit:
            break
    return out


def company_detail(code: str) -> dict | None:
    """返回单个企业的详情（含最近年度画像行）；不存在返回 None。"""
    df = _company_frame()
    sub = df[df["stock_code"] == str(code).zfill(6)]
    if sub.empty:
        return None
    years = sorted(int(y) for y in sub["year"].dropna().unique())
    # latest 取年度最大的一行，用于展示企业最新画像
    latest = sub[sub["year"] == max(years)].iloc[0].to_dict() if years else {}
    return {"stock_code": str(code).zfill(6),
            "short_name": latest.get("short_name"),
            "industry_name": latest.get("industry_name"),
            "years": years, "latest": latest}


# ---------------------------------------------------------------- meta

def meta_stats() -> dict:
    """汇总统计：企业/画像规模、知识库规模、证据库状态。

    各子块相互独立，任一产物缺失只影响对应子块（返回空 dict），不影响整体接口。
    """
    import pandas as pd
    df = _company_frame()
    data = {"companies": int(df["stock_code"].nunique()),
            "company_years": int(len(df)),
            "years": [int(y) for y in sorted(df["year"].dropna().unique())]}
    try:
        # 直接读 parquet schema 统计字段数，无需加载全表
        import pyarrow.parquet as pq
        data["profile_fields"] = len(pq.ParquetFile(FEATURES / "profile_features.parquet").schema.names)
        data["derived_fields"] = len(pq.ParquetFile(FEATURES / "derived" / "derived_metrics.parquet").schema.names)
    except Exception:  # noqa: BLE001
        pass

    knowledge = {}
    try:
        pc = pd.read_parquet(Path(CONFIG["rag"]["policy_dir"]) / "policy_corpus.parquet", columns=["title"])
        knowledge["policy_articles"] = int(len(pc))
        knowledge["policy_chunks"] = int(len(pd.read_parquet(Path(CONFIG["rag"]["index_dir"]) / "policy" / "chunks.parquet", columns=["chunk_id"])))
    except Exception:  # noqa: BLE001
        pass
    try:
        knowledge["case_chunks"] = int(len(pd.read_parquet(Path(CONFIG["rag"]["index_dir"]) / "case" / "chunks.parquet", columns=["chunk_id"])))
    except Exception:  # noqa: BLE001
        pass

    evidence = {}
    try:
        from src.evidence.store import EvidenceStore
        st = EvidenceStore()
        rows = st.query()
        # confirmed=已人工确认，pending(candidate)=待审核，用于首页展示处理进度
        evidence = {"total": len(rows),
                    "confirmed": sum(1 for r in rows if r.get("verification_status") == "confirmed"),
                    "pending": sum(1 for r in rows if r.get("verification_status") == "candidate")}
    except Exception:  # noqa: BLE001
        pass
    return {"data": data, "knowledge": knowledge, "evidence": evidence}


# ---------------------------------------------------------------- kb

def kb_search(q: str, corpus: str = "policy", top_k: int = 5) -> list[dict]:
    """知识库检索，返回精简后的命中（含绝对链接与 300 字摘要）。"""
    from src.rag.store import get_kb
    kb = get_kb()
    hits = kb.search(q, corpus=corpus, top_k=top_k)
    return [{"title": h.get("title"), "doc_no": h.get("doc_no"), "channel": h.get("channel"),
             "date": h.get("date"), "score": round(float(h.get("score", 0.0)), 4),
             "url": abs_url(h.get("url")),
             "excerpt": (h.get("text") or "")[:300]} for h in hits]


# ---------------------------------------------------------------- evidence

def evidence_pending() -> list[dict]:
    """待人工审核的证据条目。"""
    from src.evidence.store import EvidenceStore
    return EvidenceStore().pending()


def field_labels() -> dict:
    """全量 field -> 人类名（UI 统一展示，避免露出字段名）。"""
    from src.evidence.sources import all_field_labels
    return {"labels": all_field_labels()}


def evidence_review(evidence_id: str, action: str, value=None) -> str:
    """对单条证据执行审核动作，返回新状态。"""
    from src.evidence import review
    from src.evidence.store import EvidenceStore
    return review.act(EvidenceStore(), evidence_id, action, new_value=value)


def evidence_conflicts(code: str, year: int) -> list[dict]:
    """冲突列表：证据与画像不一致（按采用规则不覆盖）。"""
    from src.evidence.resolution import resolve_profile
    from src.pipeline.plan_pipeline import load_profile
    try:
        prof = load_profile(code, year)
    except Exception:  # noqa: BLE001
        return []
    _prof, res = resolve_profile(prof)
    return res.get("conflicts", [])


def evidence_resolve(evidence_id: str, choice: str) -> str:
    """冲突解决：choice ∈ {evidence, profile}。"""
    from src.evidence import review
    from src.evidence.store import EvidenceStore
    return review.resolve_conflict(EvidenceStore(), evidence_id, choice)


# ---------------------------------------------------------------- review

def review_skills() -> list[dict]:
    """读取 Skill 审计表（不存在则返回空列表）。"""
    import pandas as pd
    p = VALIDATION / "skill_audit.csv"
    if not p.exists():
        return []
    return pd.read_csv(p).fillna("").to_dict("records")


def review_skill(skill_id: str) -> dict | None:
    """聚合单个 Skill 的审计行、数据依赖、运行时定义与 YAML 原文。"""
    import pandas as pd
    rows = review_skills()
    row = next((r for r in rows if str(r.get("skill_id")) == skill_id), None)
    if row is None:
        return None
    dep = []
    dp = VALIDATION / "skill_data_dependency.csv"
    if dp.exists():
        d = pd.read_csv(dp).fillna("")
        dep = d[d["skill_id"] == skill_id].to_dict("records")
    from src.skills.engine import get_skills
    sk = get_skills().get(skill_id)
    return {"audit": row, "dependency": dep, "skill": sk.to_dict() if sk else None,
            "yaml": read_skill_yaml(skill_id)}


def review_engine() -> dict:
    """返回规则引擎的阈值、标签映射与原始 YAML 文本（供在线调参界面）。"""
    import yaml

    def _load(name):
        # 配置文件缺失时回退为空 dict，保证调参页仍可打开
        p = ROOT / "config" / name
        return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}

    th = _load("thresholds.yaml")
    fr = _load("fact_resolution.yaml")
    from src.evidence.sources import field_label
    field_labels = {k: field_label(k) for k in fr.keys()}
    return {"gate": th.get("gate", {}), "signals": th.get("signals", {}),
            "diagnostics": th.get("diagnostics", {}),
            "signal_labels": _load("signal_labels.yaml"),
            "diagnostic_meta": _load("diagnostic_meta.yaml"),
            "field_labels": field_labels,
            "thresholds_text": (ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8"),
            "fact_resolution": fr}


def save_thresholds(content: str) -> dict:
    """整体覆盖写入 thresholds.yaml；先做 YAML 合法性校验再落盘。"""
    import yaml
    yaml.safe_load(content)  # 校验 YAML 合法
    (ROOT / "config" / "thresholds.yaml").write_text(content, encoding="utf-8")
    return {"ok": True}


def update_thresholds_core(payload: dict) -> dict:
    """结构化更新核心阈值（gate / signals / category_thresholds），保留其它键。"""
    import yaml
    path = ROOT / "config" / "thresholds.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # 按区块浅合并：只覆盖传入的键，未提及的阈值保持不变
    if payload.get("gate"):
        cfg.setdefault("gate", {}).update(payload["gate"])
    if payload.get("signals"):
        cfg.setdefault("signals", {}).update(payload["signals"])
    if payload.get("category_thresholds"):
        cfg.setdefault("diagnostics", {}).setdefault("category_thresholds", {}).update(
            payload["category_thresholds"])
    # allow_unicode 保留中文；sort_keys=False 维持原有键序，便于人工 diff
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"ok": True}


# ---------------------------------------------------------------- skill yaml

def _skill_path(skill_id: str):
    """Skill 定义文件路径约定。"""
    return ROOT / "src" / "skills" / "definitions" / f"{skill_id}.yaml"


def read_skill_yaml(skill_id: str) -> dict | None:
    """读取 Skill YAML 原文；不存在返回 None。"""
    import yaml
    p = _skill_path(skill_id)
    if not p.exists():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def save_skill_yaml(skill_id: str, updates: dict) -> dict:
    """把结构化字段合并回 Skill yaml（保留未提供的键）。"""
    import yaml
    p = _skill_path(skill_id)
    if not p.exists():
        raise FileNotFoundError(f"未找到 Skill {skill_id}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    # 顶层字段直接替换；仅当传入非 None 时才覆盖，避免误清空
    for key in ("goal", "policies", "risks", "output_schema"):
        if key in updates and updates[key] is not None:
            raw[key] = updates[key]
    # eligibility_conditions / evidence_required 需映射到嵌套结构
    if "eligibility_conditions" in updates and updates["eligibility_conditions"] is not None:
        raw.setdefault("eligibility", {})["conditions"] = updates["eligibility_conditions"]
    if "evidence_required" in updates and updates["evidence_required"] is not None:
        raw.setdefault("calculation", {})["evidence_required"] = updates["evidence_required"]
    p.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"ok": True, "skill_id": skill_id}


def list_solutions(status: str | None = None) -> list[dict]:
    """列出解决方案库条目，可按审核状态过滤。"""
    from src.evidence.store import EvidenceStore
    return EvidenceStore().list_solutions(review_status=status)


def review_solution(solution_id: str, status: str, note: str = "") -> dict:
    """记录人工审核结论（状态 + 备注）。"""
    from src.evidence.store import EvidenceStore
    EvidenceStore().update_solution_review(solution_id, status, {"note": note, "reviewer": "human"})
    return {"ok": True, "solution_id": solution_id, "status": status}


def delete_solution(solution_id: str) -> dict:
    """删除解决方案，返回删除行数（0 表示未命中）。"""
    from src.evidence.store import EvidenceStore
    n = EvidenceStore().delete_solution(solution_id)
    return {"ok": bool(n), "deleted": n}


# ---------------------------------------------------------------- 材料摄取（预览 / 提交）

_IMPORTANCE_SOURCE = {"direct": "manual", "reference": "user_document"}


def fact_types() -> list[dict]:
    """事实类型词表：带人类名与说明（供下拉选择）。"""
    from src.evidence.contract import DOMAIN_FACT_TYPES, build_fact_type_vocab
    from src.evidence.sources import field_label
    v = build_fact_type_vocab()
    out: list[dict] = []
    for k in sorted(v):
        lab = field_label(k)
        label = lab["label"] or k
        if k in DOMAIN_FACT_TYPES:
            desc = DOMAIN_FACT_TYPES[k]
        elif lab["formula"]:
            desc = f"衍生：{lab['formula']}"
        elif lab["source"]:
            desc = f"来源：{lab['source']}"
        else:
            desc = v.get(k, "")
        out.append({"key": k, "label": label, "desc": desc})
    return out


def preview_text(stock_code: str, year: int, text: str, importance: str = "direct",
                 fact_type: str = "", unit: str = "", caliber: str = "") -> dict:
    """文本直接输入 → 候选证据（预览，不入库）。"""
    from src.evidence.ingest import manual_fact, validate_evidence
    if not str(text or "").strip():
        return {"candidates": [], "issues": ["文本为空"]}
    if not fact_type:
        return {"candidates": [], "issues": ["请选择事实类型（用于映射到画像字段）"]}
    st = _IMPORTANCE_SOURCE.get(importance, "user_document")
    ev = manual_fact(stock_code, year, fact_type, text, unit=unit, caliber=caliber,
                     source_type=st, source_ref="用户直接输入" if st == "manual" else "用户参考资料")
    return {"candidates": [ev.to_dict()], "issues": validate_evidence(ev)}


def _read_table(path: str, excel: bool):
    """统一以字符串读表，避免数值被 pandas 自动转为 float 丢失精度/前导零。"""
    import pandas as pd
    return pd.read_excel(path, dtype=str) if excel else pd.read_csv(path, dtype=str)


def _preview_table(path: str, stock_code: str, year: int, excel: bool) -> list:
    """解析 CSV/Excel：自动识别长表（fact_type/value）或宽表（列名为已知字段）。"""
    from src.evidence.contract import known_fields
    from src.evidence.ingest import ingest_long, ingest_wide
    df = _read_table(path, excel)
    cols = set(df.columns)
    # 缺省补充企业/年度，允许上传的表省略这两列
    if "stock_code" not in cols:
        df["stock_code"] = stock_code
    if "year" not in cols:
        df["year"] = year
    # 长表：含 fact_type/value（或 type/value）
    if {"value"} <= cols and ({"fact_type"} <= cols or {"type"} <= cols):
        type_col = "fact_type" if "fact_type" in cols else "type"
        return ingest_long(df, code_col="stock_code", year_col="year", type_col=type_col,
                           value_col="value",
                           unit_col="unit" if "unit" in cols else None,
                           caliber_col="caliber" if "caliber" in cols else None,
                           source_ref=str(Path(path).name))
    # 宽表：列名命中已知字段
    kf = known_fields()
    fm = {c: c for c in df.columns if c in kf}
    if fm:
        return ingest_wide(df, fm, code_col="stock_code", year_col="year",
                           source_ref=str(Path(path).name))
    # 两种结构都不匹配时明确报错，提示用户所需列格式
    raise ValueError("无法识别表结构：需含 fact_type/value 列，或列名为已知画像字段")


def preview_file(filename: str, content: bytes, stock_code: str, year: int,
                 importance: str = "reference") -> dict:
    """文件上传 → 候选证据（预览，不入库）。CSV/Excel/PDF（数字版）。"""
    import tempfile
    from src.evidence.ingest import validate_evidence
    ext = Path(filename).suffix.lower()
    tmp = Path(tempfile.gettempdir()) / "tax_upload"
    tmp.mkdir(parents=True, exist_ok=True)
    fp = tmp / filename
    fp.write_bytes(content)
    if ext == ".csv":
        evs = _preview_table(str(fp), stock_code, year, excel=False)
    elif ext in (".xlsx", ".xls"):
        evs = _preview_table(str(fp), stock_code, year, excel=True)
    elif ext == ".pdf":
        from src.evidence.pdf_parser import extract_pdf_facts
        evs = extract_pdf_facts(fp, stock_code, year, source_ref=filename)
    else:
        raise ValueError(f"暂不支持的文件类型：{ext}（支持 CSV / Excel / PDF）")
    issues = [i for e in evs for i in validate_evidence(e)]
    return {"candidates": [e.to_dict() for e in evs], "issues": issues}


def commit_candidates(items: list[dict]) -> list[str]:
    """把预览候选写入证据库。"""
    from src.evidence.contract import Evidence
    from src.evidence.store import EvidenceStore
    st = EvidenceStore()
    ids: list[str] = []
    for r in items:
        try:
            ev = Evidence.from_row(r)
        except Exception:  # noqa: BLE001
            # 单条脏数据跳过，不影响其余候选入库
            continue
        ev.evidence_id = ""          # 强制新编号
        ids.append(st.add(ev))
    return ids
