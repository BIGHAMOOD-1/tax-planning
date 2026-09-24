"""回填历史 fixture 的确定性字段（Update 5.2 修复）。

旧 fixture 生成于 `calculation_type` / `policy_validity` / 政策元数据之前，
导致"有确认影响但无金额语义/政策时效"。本脚本**确定性回填**（不重跑 LLM）：

- `lineage.calculation.calculation_type`（由 calculator 静态类型推导）
- `data_pool.policies[].meta`（政策元数据治理字段）
- `data_pool.policy_validity` 与顶层 `policy_validity`
- `result.json` 的 `plan_index[].calculation_type / policy_validity / status_key`

用法:
    python scripts/backfill_fixtures.py            # 全部
    python scripts/backfill_fixtures.py 300778 2024
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import CONFIG, get_logger  # noqa: E402

log = get_logger()


def _load(p: Path):
    """读取 JSON（strict=False 兼容旧 fixture 中的控制字符）；失败返回 None。"""
    try:
        return json.loads(p.read_text(encoding="utf-8"), strict=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("读取失败 %s：%s", p, str(exc)[:120])
        return None


def _dump(p: Path, data) -> None:
    """以 UTF-8、缩进 2 写回 JSON（保留中文）。"""
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def backfill_run(run_dir: Path) -> int:
    """回填单个产物目录（plans + result.json），返回更新的文件数。"""
    from src.evidence.policy_meta import build_metadata
    from src.evidence.policy_validity import summarize
    from src.report.view_model import status_key
    from src.rules.calculator import calculation_type_of

    # 目录名约定 <代码>_<年度>，取末段作为评估基准年
    try:
        year = int(run_dir.name.split("_")[-1])
    except ValueError:
        return 0
    as_of = f"{year}-12-31"
    changed = 0
    ctype_by_dir: dict[str, str | None] = {}
    pv_by_dir: dict[str, str | None] = {}

    plans_dir = run_dir / "plans"
    if plans_dir.exists():
        for fp in plans_dir.glob("*.json"):
            d = _load(fp)
            if not isinstance(d, dict):
                continue
            lin = d.setdefault("lineage", {})
            calc = lin.get("calculation") or {}
            cid = calc.get("calculator")
            if cid:
                # 由计算器静态类型推导金额语义；不可计算时显式标注
                calc["calculation_type"] = (
                    "NOT_CALCULABLE" if calc.get("impact_type") == "NOT_CALCULABLE"
                    else calculation_type_of(cid))
                # 语义登记（Update 7.2）：补齐方向/含义/状态（确定性，非 LLM）
                from src.rules.semantics import default_direction, semantics_of
                sem = semantics_of(cid)
                it = calc.get("impact_type")
                status = {"CONFIRMED_IMPACT": "CONFIRMED", "SCENARIO_IMPACT": "SCENARIO",
                          "NOT_CALCULABLE": "NOT_CALCULABLE"}.get(it, "NO_CALCULATION")
                direction = ("none" if status == "NOT_CALCULABLE"
                             else (sem.get("impact_direction") or default_direction(calc["calculation_type"])))
                # 金额按优先级回退：确认影响 > 情景影响 > results 内 > 顶层字段
                amt = (calc.get("confirmed_tax_impact") or calc.get("scenario_tax_impact")
                       or (calc.get("results") or {}).get("confirmed_tax_impact")
                       or (calc.get("results") or {}).get("scenario_tax_impact")
                       or d.get("estimated_tax_impact") or d.get("scenario_tax_impact"))
                calc["calculation_status"] = status
                calc["direction"] = direction
                try:
                    calc["impact_amount"] = abs(float(amt)) if amt is not None else None
                except (TypeError, ValueError):
                    calc["impact_amount"] = None
                calc["impact_basis"] = sem.get("economic_meaning")
                calc["baseline"] = sem.get("baseline")
                calc["action"] = sem.get("action")
                if sem.get("caveat"):
                    calc["caveat"] = sem["caveat"]
            lin["calculation"] = calc
            dp = lin.setdefault("data_pool", {})
            pols = dp.get("policies") or []
            for p in pols:
                if "meta" not in p:
                    p["meta"] = build_metadata(p, as_of)
            pv = summarize(pols, as_of)
            dp["policy_validity"] = pv
            d["policy_validity"] = pv
            direction = d.get("direction")
            ctype_by_dir[str(direction)] = calc.get("calculation_type")
            pv_by_dir[str(direction)] = pv.get("overall")
            _dump(fp, d)
            changed += 1

    rj = run_dir / "result.json"
    if rj.exists():
        r = _load(rj)
        if isinstance(r, dict):
            for e in r.get("plan_index", []):
                direction = str(e.get("direction"))
                if direction in ctype_by_dir:
                    e["calculation_type"] = ctype_by_dir[direction]
                    e["policy_validity"] = pv_by_dir.get(direction)
                    e["status_key"] = status_key(e.get("decision_code"), e.get("impact_type"))
            _dump(rj, r)
            changed += 1
    return changed


def main():
    """命令行入口：可指定 <代码> <年度>，否则回填 outputs 下全部产物目录。"""
    root = Path(CONFIG["paths"]["outputs"])
    args = sys.argv[1:]
    if len(args) >= 2:
        # 指定单次：<代码> <年度>
        targets = [root / f"{str(args[0]).zfill(6)}_{int(args[1])}"]
    else:
        # 全量：遍历形如 *_* 的目录
        targets = [d for d in sorted(root.glob("*_*")) if d.is_dir() and "_" in d.name]
    total = 0
    for d in targets:
        n = backfill_run(d)
        if n:
            log.info("回填 %s：%s 个文件", d.name, n)
        total += n
    log.info("完成，共更新 %s 个文件", total)


if __name__ == "__main__":
    main()
