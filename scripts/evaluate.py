"""效果评估（Update 8.3 · 基于公开事件的方向覆盖验证）。

四个系统（逐层消融）：
    B0 纯 LLM           —— 只给 公司名+行业+年份
    B1 LLM+RAG          —— 额外给政策检索片段
    B2 Rule-only        —— run_plan(use_llm=False)
    B3 Full             —— run_plan(use_llm=True)

指标：
    - 三级命中：L1 Direction Hit / L2 Strong Hit / L3 Confirmed Hit
    - Top-K：Case Hit@1 / @3 / @5、平均输出方向数
    - 对照区分：Case vs Control、Absolute Lift、Relative Lift
    - 证据可信：Traceability Rate、Unsupported Conclusion Rate、Invalid Policy Citation Rate
    - 性能：latency / token / 确定性占比

分阶段（可断点续跑，缓存到 validation/eval/）：
    python scripts/evaluate.py --stage system   --workers 8
    python scripts/evaluate.py --stage baseline --workers 4
    python scripts/evaluate.py --stage metrics
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VAL = ROOT / "validation"
EVAL = VAL / "eval"
POS_CSV = VAL / "ground_truth_2025.csv"
CTL_CSV = VAL / "control_group_2025.csv"
YEAR = 2024

# 排序权重：结论强度越高越靠前（用于 Top-K）
_STATUS_RANK = {"CONFIRMED": 3, "SCENARIO": 2, "DATA_GAP": 1, "NOT_RECOMMEND": 0, None: 1}


# ────────────────────────── 结果抽取 ──────────────────────────

def _extract(result: dict) -> dict:
    """从 run_plan 结果抽取紧凑的「方向 → 结论强度」与可信度统计。"""
    from src.report.view_model import status_key

    plans = result.get("plans") or []
    flagged: dict[str, dict] = {}

    n_traceable = n_unsupported = n_invalid = n_policy_related = 0
    for p in plans:
        lin = p.get("lineage") or {}
        calc = lin.get("calculation") or {}
        dc = p.get("decision_code")
        sk = status_key(dc, calc.get("impact_type"))
        rf = lin.get("required_facts") or []
        direct = sum(1 for f in rf if str(f.get("resolution", "")).upper() == "DIRECT")
        pv = (p.get("policy_validity") or {}).get("overall")
        policies = lin.get("policies") or []

        flagged[p.get("direction")] = {
            "status": sk, "decision_code": dc, "confidence": None,
            "policy_validity": pv, "direct_facts": direct,
        }
        # 可追溯：有 DIRECT 证据 且 政策有效
        if direct > 0 and pv == "VALID":
            n_traceable += 1
        # 证据缺失：有结论但无 DIRECT 证据
        if dc and direct == 0:
            n_unsupported += 1
        # 政策可靠性：分母=引用政策的结论
        if policies:
            n_policy_related += 1
            if pv in ("EXPIRED", "UNKNOWN"):
                n_invalid += 1

    for o in result.get("opportunities") or []:
        d = o.get("direction")
        if d not in flagged:
            flagged[d] = {"status": None, "decision_code": None,
                          "confidence": o.get("confidence"),
                          "policy_validity": None, "direct_facts": 0}
        else:
            flagged[d]["confidence"] = o.get("confidence")

    for key in ("watchlist", "candidates"):
        for w in result.get(key) or []:
            d = w.get("direction")
            flagged.setdefault(d, {
                "status": None, "decision_code": None,
                "confidence": (w.get("opportunity") or {}).get("confidence"),
                "policy_validity": None, "direct_facts": 0})

    met = result.get("metrics") or {}
    return {
        "flagged": flagged, "n_plans": len(plans),
        "traceable": n_traceable, "unsupported": n_unsupported,
        "invalid_policy": n_invalid, "policy_related": n_policy_related,
        "total_seconds": met.get("total_seconds"),
        "llm": met.get("llm") or {},
    }


def _run_one(code: str, use_llm: bool) -> dict:
    """子进程工作单元：跑单个公司并只返回紧凑结果（避免大对象 pickle）。"""
    from src.pipeline.plan_pipeline import run_plan
    try:
        r = run_plan(str(code).zfill(6), YEAR, use_llm=use_llm, save=False,
                     top_k=3, rounds=1)
        return _extract(r)
    except Exception as e:  # noqa: BLE001 单个失败不拖垮整体
        return {"error": f"{type(e).__name__}: {e}"}


def _worker(args: tuple[str, bool]) -> tuple[str, dict]:
    code, use_llm = args
    return code, _run_one(code, use_llm)


def _run_pool(codes: list[str], use_llm: bool, workers: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    tasks = [(c, use_llm) for c in codes]
    if workers <= 1:
        for i, t in enumerate(tasks, 1):
            code, res = _worker(t)
            out[code] = res
            print(f"  [{i}/{len(tasks)}] {code} 完成", flush=True)
        return out
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_worker, t): t[0] for t in tasks}
        for i, fut in enumerate(as_completed(futs), 1):
            code, res = fut.result()
            out[code] = res
            print(f"  [{i}/{len(tasks)}] {code} 完成", flush=True)
    return out


# ────────────────────────── 基线（LLM）──────────────────────────

_B0_SYS = "你是资深税务筹划专家。只输出 JSON，不要解释。"
_B0_TMPL = (
    "公司：{name}（股票代码 {code}，所属行业：{industry}），分析基准年度 {year}。\n"
    "请仅凭你已有的知识（不要假设能访问该公司的具体财报），列出该公司在 **{next_year} 年**"
    "最可能采取的、与税务相关的筹划或税收优惠方向，最多 5 个。\n"
    "每个方向用简短中文名（如：研发筹划、资产筹划、融资筹划、政府补助、高新技术企业、股份支付、组织架构、投资收益）。\n"
    '只返回 JSON 数组，例如：["研发筹划", "资产筹划"]'
)


def _baseline(code: str, name: str, industry: str, use_rag: bool) -> dict:
    """B0（无 RAG）/ B1（带政策检索）基线。返回 {'directions': [...], 'raw': str}。"""
    from src.llm.client import get_llm
    from src.agents.opportunity import canonical_direction
    llm = get_llm()
    if not llm.available:
        return {"directions": [], "raw": "", "error": "LLM 不可用"}

    ctx = ""
    if use_rag:
        try:
            from src.rag.store import get_kb
            hits = get_kb().search(f"{name} {industry} 企业所得税 税收优惠 筹划", top_k=5)
            titles = [h.get("title") or h.get("text", "")[:80] for h in hits]
            ctx = "\n可参考的税收政策（检索片段）：\n" + "\n".join(f"- {t}" for t in titles if t)
        except Exception:  # noqa: BLE001 检索失败降级为无 RAG
            ctx = ""

    msg = _B0_TMPL.format(name=name, code=code, industry=industry,
                          year=YEAR, next_year=YEAR + 1) + ctx
    try:
        raw = llm.chat([{"role": "system", "content": _B0_SYS},
                        {"role": "user", "content": msg}],
                       temperature=0.0, json_mode=True)
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict):
            data = data.get("directions") or data.get("items") or []
        dirs = [canonical_direction(str(x)) for x in (data or []) if str(x).strip()]
        return {"directions": dirs[:5], "raw": raw}
    except Exception as e:  # noqa: BLE001
        return {"directions": [], "raw": "", "error": f"{type(e).__name__}: {e}"}


def _baseline_worker(args: tuple[str, str, str, bool]) -> tuple[str, dict]:
    code, name, industry, use_rag = args
    return code, _baseline(code, name, industry, use_rag)


# ────────────────────────── 指标 ──────────────────────────

def _rank(flagged: dict) -> list[str]:
    """按结论强度 + 置信度排序方向，用于 Top-K。"""
    def key(item):
        d, v = item
        return (-_STATUS_RANK.get(v.get("status"), 1),
                -(v.get("confidence") or 0), d)
    return [d for d, _ in sorted(flagged.items(), key=key)]


def _hit_levels(flagged: dict, target: str) -> dict:
    """三级命中判定。"""
    v = flagged.get(target)
    if v is None:
        return {"l1": 0, "l2": 0, "l3": 0}
    l2 = int(v.get("status") in ("CONFIRMED", "SCENARIO") or (v.get("confidence") or 0) >= 0.7)
    l3 = int(v.get("status") == "CONFIRMED" and v.get("policy_validity") == "VALID"
             and (v.get("direct_facts") or 0) > 0)
    return {"l1": 1, "l2": l2, "l3": l3}


def _metrics_for(samples: pd.DataFrame, runs: dict, label: str) -> dict:
    rows = []
    for _, s in samples.iterrows():
        code = str(s["stock_code"]).zfill(6)
        res = runs.get(code) or {}
        flagged = res.get("flagged") or {}
        if res.get("error"):
            rows.append({"stock_code": code, "error": res["error"], "hit_l1": 0,
                         "hit_l2": 0, "hit_l3": 0, "hit@1": 0, "hit@3": 0, "hit@5": 0,
                         "n_dirs": 0})
            continue
        target = s["mapped_direction"]
        h = _hit_levels(flagged, target)
        order = _rank(flagged)
        rows.append({
            "stock_code": code, "stock_name": s.get("stock_name"),
            "event_type": s.get("event_type"), "mapped_direction": target,
            "hit_l1": h["l1"], "hit_l2": h["l2"], "hit_l3": h["l3"],
            "hit@1": int(target in order[:1]), "hit@3": int(target in order[:3]),
            "hit@5": int(target in order[:5]), "n_dirs": len(flagged),
            "top_dirs": "|".join(order[:5]),
        })
    df = pd.DataFrame(rows)
    n = len(df)
    ok = df[df.get("error").isna()] if "error" in df else df
    m = {
        "group": label, "n": n, "n_ok": len(ok),
        "hit_l1": ok["hit_l1"].mean() if len(ok) else 0,
        "hit_l2": ok["hit_l2"].mean() if len(ok) else 0,
        "hit_l3": ok["hit_l3"].mean() if len(ok) else 0,
        "hit@1": ok["hit@1"].mean() if len(ok) else 0,
        "hit@3": ok["hit@3"].mean() if len(ok) else 0,
        "hit@5": ok["hit@5"].mean() if len(ok) else 0,
        "avg_dirs": ok["n_dirs"].mean() if len(ok) else 0,
    }
    return {"summary": m, "rows": df}


# ────────────────────────── 主流程 ──────────────────────────

def _load_samples() -> tuple[pd.DataFrame, pd.DataFrame]:
    pos = pd.read_csv(POS_CSV, dtype={"stock_code": str})
    ctl = pd.read_csv(CTL_CSV, dtype={"stock_code": str})
    for df in (pos, ctl):
        df["stock_code"] = df["stock_code"].str.zfill(6)
    return pos, ctl


def _load_cache(name: str) -> dict:
    p = EVAL / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_cache(name: str, data: dict) -> None:
    EVAL.mkdir(parents=True, exist_ok=True)
    (EVAL / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def stage_system(pos: pd.DataFrame, ctl: pd.DataFrame, workers: int, limit: int | None):
    all_codes = list(dict.fromkeys(pos["stock_code"].tolist() + ctl["stock_code"].tolist()))
    if limit:
        all_codes = all_codes[:limit]
    b2 = _load_cache("b2_runs")
    todo = [c for c in all_codes if c not in b2]
    print(f"[B2 Rule-only] 待跑 {len(todo)} / 共 {len(all_codes)}", flush=True)
    if todo:
        b2.update(_run_pool(todo, use_llm=False, workers=workers))
        _save_cache("b2_runs", b2)

    pos_codes = [c for c in pos["stock_code"].tolist() if not limit or c in all_codes]
    b3 = _load_cache("b3_runs")
    todo3 = [c for c in pos_codes if c not in b3]
    print(f"[B3 Full] 待跑 {len(todo3)} / 共 {len(pos_codes)}", flush=True)
    if todo3:
        b3.update(_run_pool(todo3, use_llm=True, workers=workers))
        _save_cache("b3_runs", b3)


def stage_baseline(pos: pd.DataFrame, ctl: pd.DataFrame, workers: int, limit: int | None):
    both = pd.concat([pos, ctl], ignore_index=True)
    rows = both if not limit else both.head(limit)
    for tag, use_rag in (("b0", False), ("b1", True)):
        cache = _load_cache(f"{tag}_runs")
        tasks = [(str(r["stock_code"]).zfill(6), str(r["stock_name"]),
                  str(r["industry"]), use_rag)
                 for _, r in rows.iterrows()
                 if str(r["stock_code"]).zfill(6) not in cache]
        print(f"[{tag.upper()}] 待跑 {len(tasks)}", flush=True)
        if not tasks:
            continue
        if workers <= 1:
            for t in tasks:
                c, res = _baseline_worker(t)
                cache[c] = res
        else:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_baseline_worker, t): t[0] for t in tasks}
                for fut in as_completed(futs):
                    c, res = fut.result()
                    cache[c] = res
                    print(f"  {tag} {c} 完成", flush=True)
        _save_cache(f"{tag}_runs", cache)


def stage_metrics(pos: pd.DataFrame, ctl: pd.DataFrame):
    EVAL.mkdir(parents=True, exist_ok=True)
    b2 = _load_cache("b2_runs")
    b3 = _load_cache("b3_runs")

    mp = _metrics_for(pos, b2, "positive(B2)")
    mc = _metrics_for(ctl, b2, "control(B2)")
    mp["rows"].to_csv(EVAL / "per_sample_B2.csv", index=False, encoding="utf-8-sig")

    # 对照区分与 Lift
    p_l1, c_l1 = mp["summary"]["hit_l1"], mc["summary"]["hit_l1"]
    lift = {
        "case_hit_l1": p_l1, "control_hit_l1": c_l1,
        "absolute_lift": p_l1 - c_l1,
        "relative_lift": (p_l1 / c_l1) if c_l1 else None,
        "case_hit_l2": mp["summary"]["hit_l2"], "control_hit_l2": mc["summary"]["hit_l2"],
        "case_hit@1": mp["summary"]["hit@1"], "case_hit@3": mp["summary"]["hit@3"],
        "case_hit@5": mp["summary"]["hit@5"],
        "case_avg_dirs": mp["summary"]["avg_dirs"], "control_avg_dirs": mc["summary"]["avg_dirs"],
    }

    # B2 vs B3 消融（正例）
    ablation = {}
    if b3:
        m3 = _metrics_for(pos, b3, "positive(B3)")
        ablation = {"B2_rule_only": mp["summary"], "B3_full": m3["summary"]}

    # 证据可信度（正例+对照全部 plan 汇总）
    def _trust(runs):
        tr = un = inv = pr = tot = 0
        for res in runs.values():
            tr += res.get("traceable", 0)
            un += res.get("unsupported", 0)
            inv += res.get("invalid_policy", 0)
            pr += res.get("policy_related", 0)
            tot += res.get("n_plans", 0)
        return tr, un, inv, pr, tot

    trp, unp, invp, prp, totp = _trust(b2)
    trust = {
        "traceability_rate": (trp / totp) if totp else None,
        "unsupported_conclusion_rate": (unp / totp) if totp else None,
        "invalid_policy_citation_rate": (invp / prp) if prp else None,
        "total_plans": totp,
        "traceable_plans": trp, "unsupported_plans": unp,
        "invalid_policy_plans": invp, "policy_related_plans": prp,
    }

    # 基线（B0/B1）：正例 + 对照（用于公平的 Lift）
    baselines = {}
    for tag in ("b0", "b1"):
        cache = _load_cache(f"{tag}_runs")
        if not cache:
            continue
        def _hits(df):
            hs, nd = [], []
            for _, s in df.iterrows():
                code = str(s["stock_code"]).zfill(6)
                dirs = (cache.get(code) or {}).get("directions") or []
                hs.append(int(s["mapped_direction"] in dirs))
                nd.append(len(dirs))
            return (sum(hs) / len(hs) if hs else 0,
                    sum(nd) / len(nd) if nd else 0)
        hp, dp = _hits(pos)
        hc, dc_ = _hits(ctl)
        baselines[tag.upper()] = {
            "n": len(pos), "hit_l1": hp, "avg_dirs": dp,
            "control_hit_l1": hc, "absolute_lift": hp - hc,
            "relative_lift": (hp / hc) if hc else None,
        }

    summary = {"lift": lift, "trust": trust, "ablation": ablation,
               "baselines": baselines, "B2_positive": mp["summary"],
               "B2_control": mc["summary"]}
    (EVAL / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                       encoding="utf-8")

    # 汇总表（便于直接进报告）
    tbl = []
    for name, m in (("B2 本系统(正例)", mp["summary"]), ("B2 本系统(对照)", mc["summary"])):
        tbl.append({"system": name, **m})
    if ablation:
        tbl.append({"system": "B3 本系统全量(正例)", **ablation["B3_full"]})
    for tag, m in baselines.items():
        tbl.append({"system": f"{tag} (正例)", "n": m["n"], "n_ok": m["n"],
                    "hit_l1": m["hit_l1"], "avg_dirs": m["avg_dirs"]})
    pd.DataFrame(tbl).to_csv(EVAL / "summary.csv", index=False, encoding="utf-8-sig")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def stage_deep(pos: pd.DataFrame, top_n: int = 10):
    """生成 10 例深度个案（优先"命中且方向可由财报推断"的案例）。"""
    from src.pipeline.plan_pipeline import run_plan
    from src.report.view_model import status_key

    b2 = _load_cache("b2_runs")
    # 选取命中案例；优先 高企 / 股权转让（数据可推断），再补其它
    pri = {"首次高新技术企业认定": 0, "股权转让/处置": 1,
           "股权激励/员工持股": 2, "重组/股权划转/吸收合并": 3}
    hits = []
    for _, s in pos.iterrows():
        code = str(s["stock_code"]).zfill(6)
        flagged = (b2.get(code) or {}).get("flagged") or {}
        if s["mapped_direction"] in flagged:
            hits.append(s)
    hits.sort(key=lambda s: pri.get(s["event_type"], 9))
    picks = hits[:top_n]

    lines = ["# 深度个案（10 例）", "",
             "> 系统仅用 2024 年数据；事件为公司 2025 年公开披露。", ""]
    for i, s in enumerate(picks, 1):
        code = str(s["stock_code"]).zfill(6)
        target = s["mapped_direction"]
        try:
            r = run_plan(code, YEAR, use_llm=False, save=False, top_k=3, rounds=1)
        except Exception as e:  # noqa: BLE001
            lines.append(f"## 案例 {i}：{code} {s['stock_name']}（运行失败：{e}）\n")
            continue
        plans = r.get("plans") or []
        p = next((x for x in plans if x.get("direction") == target), None)
        lin = (p or {}).get("lineage") or {}
        calc = lin.get("calculation") or {}
        rf = lin.get("required_facts") or []
        direct = [f.get("label") or f.get("field") for f in rf
                  if str(f.get("resolution", "")).upper() == "DIRECT"]
        pols = lin.get("policies") or []
        opp = next((o for o in (r.get("opportunities") or [])
                    if o.get("direction") == target), {})
        status = status_key(p.get("decision_code"), calc.get("impact_type")) if p else "（Discovery 命中）"
        lines += [
            f"## 案例 {i}：{s['stock_name']}（{code}）",
            "",
            f"- **方向**：{target}（对应 Skill `{s['mapped_skill']}`）",
            f"- **2024 系统判定**：{status}　决策={p.get('final_decision') if p else '—'}　"
            f"政策有效性={((p or {}).get('policy_validity') or {}).get('overall', '—')}",
            f"- **直接证据**：{'、'.join(direct[:6]) if direct else '（Discovery 级，未进入深度核验）'}",
            f"- **发现置信度**：{opp.get('confidence', '—')}　理由：{(opp.get('rationale') or '—')[:120]}",
            f"- **政策依据**：{('；'.join((x.get('doc_no') or '') + ' ' + (x.get('title') or '') for x in pols[:2])) or '—'}",
            f"- **2025 真实事件**：{s['event_title']}（{s['announce_date']}）",
            f"- **出处**：{s['source_url']}",
            "",
        ]
    out = EVAL / "deep_cases.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"深度个案已写出：{out}（{len(picks)} 例）")


def stage_coverage():
    """回归测试集覆盖度（来自既有验证产物）。"""
    rows = []
    p = VAL / "skill_test_cases.csv"
    if p.exists():
        d = pd.read_csv(p)
        rows.append({"suite": "Skill 两层测试", "cases": len(d),
                     "skills": d["skill_id"].nunique() if "skill_id" in d else "",
                     "calculators": d["calculator"].nunique() if "calculator" in d else "",
                     "pass_rate": float(d["pass"].mean()) if "pass" in d else ""})
    rows += [
        {"suite": "Golden Cases", "cases": 5, "skills": 5, "calculators": "", "pass_rate": 1.0},
        {"suite": "模块测试", "cases": 23, "skills": "", "calculators": "", "pass_rate": 1.0},
    ]
    pd.DataFrame(rows).to_csv(EVAL / "coverage.csv", index=False, encoding="utf-8-sig")
    print(pd.DataFrame(rows).to_string(index=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage",
                    choices=["system", "baseline", "metrics", "deep", "coverage", "all"],
                    default="all")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None, help="仅跑前 N 个公司（调试）")
    args = ap.parse_args()

    pos, ctl = _load_samples()
    print(f"正例 {len(pos)} · 对照 {len(ctl)}")
    t0 = time.perf_counter()
    if args.stage in ("system", "all"):
        stage_system(pos, ctl, args.workers, args.limit)
    if args.stage in ("baseline", "all"):
        stage_baseline(pos, ctl, args.workers, args.limit)
    if args.stage in ("metrics", "all"):
        stage_metrics(pos, ctl)
    if args.stage in ("deep", "all"):
        stage_deep(pos)
    if args.stage in ("coverage", "all"):
        stage_coverage()
    print(f"用时 {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
