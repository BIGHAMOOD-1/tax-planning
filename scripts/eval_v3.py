"""效果评估 v3 · 合规精度评估（Update 8.3 v3 / 9.1）。

评估对象：系统给出的税务建议**本身是否站得住**（政策真实/有效 + 证据接地 + 不过度声称）。
不做方向覆盖/Lift。

指标：
    M0 建议完整性（质量检查，不计分）
    M1 政策真实率
    M2 政策现行有效率
    M4 证据接地率
    M5 过度声称率（风险代理指标）

对比对象：
    B0' 大模型直答（给同源数据摘要）
    B1' 大模型 + 政策检索（给同源数据摘要）
    B2 Deterministic System（Rule + Diagnostic + Policy + Evidence Gate）
    B3 Full System（B2 + LLM Discovery/Reasoning）

分阶段：
    python scripts/eval_v3.py --stage system   --workers 8
    python scripts/eval_v3.py --stage baseline --workers 4
    python scripts/eval_v3.py --stage metrics
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VAL = ROOT / "validation"
OUT = VAL / "eval_v3"
POS_CSV = VAL / "ground_truth_2025.csv"
CTL_CSV = VAL / "control_group_2025.csv"
CORPUS = ROOT / "knowledge" / "policy_corpus" / "policy_corpus.parquet"
YEAR = 2024

# —— 企业数据摘要：仅原始报表科目（金额/数量），不含 flag/label/proxy/ratio ——
SUMMARY_FIELDS = [
    ("csmar__OperatingRevenue", "营业收入"),
    ("bs_total_assets", "资产总额"),
    ("is_total_profit", "利润总额"),
    ("rd_spend_sum", "研发费用"),
    ("rd_person", "研发人员数"),
    ("bs_fixed_assets", "固定资产"),
    ("bs_cip", "在建工程"),
    ("bs_intangible_assets", "无形资产"),
    ("bs_accounts_receivable", "应收账款"),
    ("is_finance_expense", "财务费用"),
    ("interest_expense_best", "利息费用"),
    ("is_income_tax", "所得税费用"),
    ("cf_tax_paid", "实缴税费"),
    ("is_tax_surcharge", "税金及附加"),
    ("gov_subsidy_total", "政府补助"),
    ("investment_income_detail_sum", "投资收益"),
    ("employee_compensation_base", "职工薪酬"),
    ("subsidiary_count", "子公司数"),
    ("subsidiary_overseas_count", "海外子公司数"),
]


def _fmt_money(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:,.0f} 元"


# ────────────────────── 政策库解析（M1/M2）──────────────────────

_CORPUS_CACHE: dict = {"loaded": False, "doc": {}, "titles": []}


def _norm(s: str) -> str:
    s = str(s or "").replace("中华人民共和国", "")
    return re.sub(r"[\s　()（）〔〕\[\]【】《》,，.。、;；:：\-—_/]+", "", s).lower()


def _load_corpus():
    if _CORPUS_CACHE["loaded"]:
        return
    df = pd.read_parquet(CORPUS, columns=["doc_no", "title", "written_date", "effect_level", "tax_type"])
    doc = {}
    titles = []
    for r in df.itertuples(index=False):
        rec = {"doc_no": r.doc_no, "title": r.title, "date": r.written_date}
        if r.doc_no and str(r.doc_no).strip():
            doc.setdefault(_norm(r.doc_no), rec)
        if r.title:
            titles.append((_norm(r.title), rec))
    _CORPUS_CACHE.update({"loaded": True, "doc": doc, "titles": titles})


def resolve_policy(policy_str: str):
    """把引用政策字符串解析到政策库记录（文号优先，其次标题包含）。"""
    _load_corpus()
    key = _norm(policy_str)
    if not key:
        return None
    if key in _CORPUS_CACHE["doc"]:
        return _CORPUS_CACHE["doc"][key]
    for nt, rec in _CORPUS_CACHE["titles"]:
        if nt and (nt in key or key in nt) and min(len(nt), len(key)) >= 6:
            return rec
    return None


def classify_validity(policy_str: str, as_of: str = "2024-12-31"):
    from src.evidence.policy_validity import classify_policy
    rec = resolve_policy(policy_str)
    if not rec:
        return "NOT_FOUND"
    return classify_policy({"doc_no": rec["doc_no"], "title": rec["title"],
                            "date": rec["date"]}, as_of)["validity"]


# ────────────────────── 系统侧（B2/B3）──────────────────────

def _ours_advice(result: dict) -> list[dict]:
    from src.report.view_model import status_key
    by_dir: dict[str, dict] = {}
    for p in result.get("plans") or []:
        lin = p.get("lineage") or {}
        calc = lin.get("calculation") or {}
        sk = status_key(p.get("decision_code"), calc.get("impact_type"))
        rf = lin.get("required_facts") or []
        direct = sum(1 for f in rf if str(f.get("resolution", "")).upper() == "DIRECT")
        pols = lin.get("policies") or []
        pol = ""
        if pols:
            pol = str(pols[0].get("doc_no") or pols[0].get("title") or "")
        by_dir[p.get("direction")] = {
            "direction": p.get("direction"), "policy": pol,
            "confidence": None, "high": sk in ("CONFIRMED", "SCENARIO"),
            "grounded": direct > 0,
            "reason": (p.get("final_decision") or ""),
        }
    for o in result.get("opportunities") or []:
        d = o.get("direction")
        conf = o.get("confidence") or 0
        cur = by_dir.get(d)
        if cur:
            cur["confidence"] = conf
            cur["high"] = cur["high"] or conf >= 0.7
        else:
            by_dir[d] = {"direction": d, "policy": "", "confidence": conf,
                         "high": conf >= 0.7, "grounded": False,
                         "reason": (o.get("rationale") or "")[:60]}
    return list(by_dir.values())


def _run_one(code: str, use_llm: bool) -> dict:
    from src.pipeline.plan_pipeline import run_plan
    try:
        r = run_plan(str(code).zfill(6), YEAR, use_llm=use_llm, save=False, top_k=3, rounds=1)
        return {"advice": _ours_advice(r)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _worker(args):
    code, use_llm = args
    return code, _run_one(code, use_llm)


# ────────────────────── 基线（B0'/B1'，给数据）──────────────────────

_SYS = "你是资深税务筹划专家。只输出 JSON，不要解释。"
_TMPL = (
    "以下是某上市公司 {YEAR} 年的关键财务数据（单位：元）：\n{DATA}\n"
    "所属行业：{INDUSTRY}\n\n"
    "请基于以上数据，列出该公司 {NEXT} 年最值得关注的税务筹划 / 税收优惠方向，最多 5 条。\n"
    "每条必须包含四个字段：\n"
    '  "direction"：方向名称（简短中文，如 研发筹划 / 高新技术企业 / 资产筹划）；\n'
    '  "policy"：依据的政策（文号或名称；不确定填 ""）；\n'
    '  "confidence"：high / medium / low；\n'
    '  "reason"：一句话理由。\n'
    '只返回 JSON 数组，例如：[{"direction":"研发筹划","policy":"财税〔2018〕99号","confidence":"high","reason":"研发费用较大"}]'
)


def _baseline(code: str, name: str, industry: str, use_rag: bool) -> dict:
    from src.llm.client import get_llm
    from src.pipeline.plan_pipeline import load_profile
    llm = get_llm()
    if not llm.available:
        return {"advice": [], "error": "LLM 不可用"}
    prof = load_profile(str(code).zfill(6), YEAR)
    lines = []
    for f, label in SUMMARY_FIELDS:
        v = prof.get(f)
        if v is not None and v != "":
            lines.append(f"- {label}：{_fmt_money(v)}")
    data = "\n".join(lines) if lines else "（无数据）"

    ctx = ""
    if use_rag:
        try:
            from src.rag.store import get_kb
            hits = get_kb().search(f"{name} {industry} 企业所得税 税收优惠 筹划", top_k=5)
            titles = [h.get("title") or "" for h in hits]
            ctx = "\n可参考的税收政策（检索片段）：\n" + "\n".join(f"- {t}" for t in titles if t)
        except Exception:  # noqa: BLE001
            ctx = ""

    msg = (_TMPL.replace("{YEAR}", str(YEAR)).replace("{NEXT}", str(YEAR + 1))
           .replace("{DATA}", data).replace("{INDUSTRY}", industry)) + ctx
    try:
        raw = llm.chat([{"role": "system", "content": _SYS},
                        {"role": "user", "content": msg}], temperature=0.0, json_mode=True)
        data2 = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data2, dict):
            data2 = data2.get("items") or data2.get("directions") or []
        advice = []
        for it in (data2 or [])[:5]:
            if not isinstance(it, dict):
                continue
            advice.append({
                "direction": str(it.get("direction") or "").strip(),
                "policy": str(it.get("policy") or "").strip(),
                "confidence": str(it.get("confidence") or "").strip(),
                "high": str(it.get("confidence") or "").strip().lower() in ("high", "高"),
                "grounded": False,   # 基线无证据链
                "reason": str(it.get("reason") or "").strip(),
            })
        return {"advice": advice, "raw": raw}
    except Exception as e:  # noqa: BLE001
        return {"advice": [], "error": f"{type(e).__name__}: {e}"}


def _baseline_worker(args):
    code, name, industry, use_rag = args
    return code, _baseline(code, name, industry, use_rag)


# ────────────────────── 打分（M0/M1/M2/M4/M5）──────────────────────

def score_advice(advice: list[dict], corroborated: set[str]) -> dict:
    n = len(advice)
    if n == 0:
        return {"n": 0, "m0": None, "m1": None, "m2": None, "m4": None, "m5": None,
                "n_policy": 0, "n_policy_ok": 0, "n_valid": 0, "n_grounded": 0,
                "n_high": 0, "n_over": 0}
    m0 = sum(1 for a in advice if a["direction"] and a["policy"]) / n
    pol = [a for a in advice if a["policy"]]
    ok = [a for a in pol if resolve_policy(a["policy"])]
    valid = [a for a in ok if classify_validity(a["policy"]) == "VALID"]
    grounded = [a for a in advice if a["grounded"]]
    high = [a for a in advice if a["high"]]
    over = [a for a in high if a["direction"] not in corroborated]
    return {
        "n": n, "m0": m0,
        "m1": (len(ok) / len(pol)) if pol else None,
        "m2": (len(valid) / len(pol)) if pol else None,
        "m4": len(grounded) / n,
        "m5": (len(over) / len(high)) if high else None,
        "n_policy": len(pol), "n_policy_ok": len(ok), "n_valid": len(valid),
        "n_grounded": len(grounded), "n_high": len(high), "n_over": len(over),
    }


def _aggregate(rows: list[dict]) -> dict:
    """按样本聚合：比例类取总计数比，避免小样本均值失真。"""
    n = sum(r["n"] for r in rows)
    npol = sum(r["n_policy"] for r in rows)
    nok = sum(r["n_policy_ok"] for r in rows)
    nval = sum(r["n_valid"] for r in rows)
    ngr = sum(r["n_grounded"] for r in rows)
    nhigh = sum(r["n_high"] for r in rows)
    nover = sum(r["n_over"] for r in rows)
    return {
        "advice_total": n, "policy_cited": npol,
        "m0": (sum((r["m0"] or 0) * r["n"] for r in rows) / n) if n else None,
        "m1": (nok / npol) if npol else None,
        "m2": (nval / npol) if npol else None,
        "m4": (ngr / n) if n else None,
        "m5": (nover / nhigh) if nhigh else None,
        "high_conf_claims": nhigh, "overclaims": nover,
    }


# ────────────────────── 主流程 ──────────────────────

def _samples():
    pos = pd.read_csv(POS_CSV, dtype={"stock_code": str})
    ctl = pd.read_csv(CTL_CSV, dtype={"stock_code": str})
    for df in (pos, ctl):
        df["stock_code"] = df["stock_code"].str.zfill(6)
    return pos, ctl


def _cache(name):
    p = OUT / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _run_pool(tasks, fn, workers, tag):
    out = {}
    if workers <= 1:
        for t in tasks:
            c, r = fn(t)
            out[c] = r
            print(f"  [{tag}] {c} 完成", flush=True)
        return out
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, t): t[0] for t in tasks}
        for fut in as_completed(futs):
            c, r = fut.result()
            out[c] = r
            print(f"  [{tag}] {c} 完成", flush=True)
    return out


def stage_system(pos, ctl, workers, limit):
    codes = list(dict.fromkeys(pos["stock_code"].tolist() + ctl["stock_code"].tolist()))
    if limit:
        codes = codes[:limit]
    for tag, use_llm in (("b2", False), ("b3", True)):
        cache = _cache(tag)
        todo = [c for c in codes if c not in cache]
        print(f"[{tag}] 待跑 {len(todo)}/{len(codes)}", flush=True)
        if todo:
            cache.update(_run_pool([(c, use_llm) for c in todo], _worker, workers, tag))
            _save(tag, cache)


def stage_baseline(pos, ctl, workers, limit):
    both = pd.concat([pos, ctl], ignore_index=True)
    if limit:
        both = both.head(limit)
    for tag, use_rag in (("b0p", False), ("b1p", True)):
        cache = _cache(tag)
        tasks = [(str(r["stock_code"]).zfill(6), str(r["stock_name"]), str(r["industry"]), use_rag)
                 for _, r in both.iterrows() if str(r["stock_code"]).zfill(6) not in cache]
        print(f"[{tag}] 待跑 {len(tasks)}", flush=True)
        if tasks:
            cache.update(_run_pool(tasks, _baseline_worker, workers, tag))
            _save(tag, cache)


def _corroborated(row) -> set[str]:
    d = row.get("mapped_direction")
    return {str(d)} if isinstance(d, str) and d else set()


def stage_metrics(pos, ctl):
    OUT.mkdir(parents=True, exist_ok=True)
    systems = {
        "B0' 大模型直答(给数据)": "b0p",
        "B1' 大模型+政策检索(给数据)": "b1p",
        "B2 Deterministic System": "b2",
        "B3 Full System": "b3",
    }
    all_rows = []
    summary = {}
    for label, key in systems.items():
        cache = _cache(key)
        if not cache:
            continue
        ours = key in ("b2", "b3")
        for grp, df in (("真阳组", pos), ("对照组", ctl)):
            rows = []
            for _, s in df.iterrows():
                code = str(s["stock_code"]).zfill(6)
                res = cache.get(code) or {}
                advice = res.get("advice") or []
                if ours:
                    # 只比较"系统给出的推荐"（已分析方案：有政策依据）；
                    # Discovery 候选（无政策/无证据）不计入，避免与基线的成品建议错位比较
                    advice = [a for a in advice if a.get("policy")]
                rows.append({"stock_code": code, **score_advice(advice, _corroborated(s))})
                for a in advice:
                    all_rows.append({"system": label, "group": grp, "stock_code": code,
                                     "direction": a.get("direction"), "policy": a.get("policy"),
                                     "confidence": a.get("confidence"), "high": a.get("high"),
                                     "grounded": a.get("grounded")})
            summary[f"{label} · {grp}"] = _aggregate(rows)
    pd.DataFrame(all_rows).to_csv(OUT / "advice_all.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"system": k, **v} for k, v in summary.items()]).to_csv(
        OUT / "summary.csv", index=False, encoding="utf-8-sig")
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["system", "baseline", "metrics", "all"], default="all")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    pos, ctl = _samples()
    print(f"真阳 {len(pos)} · 对照 {len(ctl)}")
    t0 = time.perf_counter()
    if args.stage in ("system", "all"):
        stage_system(pos, ctl, args.workers, args.limit)
    if args.stage in ("baseline", "all"):
        stage_baseline(pos, ctl, args.workers, args.limit)
    if args.stage in ("metrics", "all"):
        stage_metrics(pos, ctl)
    print(f"用时 {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
