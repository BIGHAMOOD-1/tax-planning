"""生成数据集注册表草稿 config/dataset_registry.yaml。

以 DES 为依据，自动判定每张表的：
    shape (wide/detail/event/reference)、entity/time、base_keys、dimensions、
    event_key、code_maps、total_keywords。
生成后建议人工确认/锁定；运行时以本表为准（自动识别仅作校验）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from src.common import CONFIG, ROOT, get_logger  # noqa: E402
from src.data.registry import discover_datasets, read_code_maps, read_des  # noqa: E402

log = get_logger()

ITEM_KEYWORDS = ["项目", "科目类别", "借款类别", "递延所得税类型", "类别", "类型",
                 "名称", "人员明细"]

# 维度列：用"精确中文名"白名单，避免"影响净利润的其他项目"这类误匹配
DIM_CN = {
    "项目", "项目名称", "项目编码", "项目类型", "具体项目", "科目类别", "科目类型", "科目名称",
    "借款类别", "递延所得税类型", "类别", "人员明细", "子公司名称", "关联方名称", "关联公司名称",
    "机构名称", "业务往来机构名称", "使用权资产项目", "无形资产项目", "固定资产项目",
    "长期投资项目", "投资收益明细项目", "其他流动资产项目", "存货项目", "应付款项科目",
    "应收款项科目", "账龄", "账龄或项目", "是否流通", "股票种类明细", "税种", "研发项目",
    "单位或项目名称", "票据种类编码", "投资类型", "种类", "认定项目类型", "认定对象名称",
}
# 这些列是"主键维度"，不算项目维度
KEY_CN = {"报表类型", "期间归属", "证券代码", "股票代码", "统计截止日期", "截止日期",
          "证券简称", "公司中文全称", "数据来源", "币种"}
ENTITY_CN = ["证券代码", "股票代码"]
TIME_CN = ["统计截止日期", "截止日期", "公告日期"]
REPORT_TYPE_CN = ["报表类型"]
PERIOD_CN = ["期间归属"]


def _find(des: dict[str, str], cns: list[str]) -> list[str]:
    """在 DES 字典中找出中文名含任一关键词的字段代码列表。"""
    return [code for code, cn in des.items() if any(k in cn for k in cns)]


def build() -> dict:
    """扫描全部数据集，依据 DES 推断表形态与去重/聚合策略，返回注册表草稿。"""
    datasets = discover_datasets()
    out = {}
    for ds in datasets:
        des = read_des(ds)
        cms = read_code_maps(ds)
        entity = _find(des, ENTITY_CN)
        time = _find(des, TIME_CN)
        rtype = _find(des, REPORT_TYPE_CN)
        period = _find(des, PERIOD_CN)
        event = [c for c in des if "eventid" in c.lower()]
        key_codes = set(entity) | set(time) | set(rtype) | set(period)
        # 维度列：中文名精确命中白名单且不属于主键维度
        dims = [c for c, cn in des.items()
                if cn.strip() in DIM_CN and c not in key_codes]

        # 表形态判定优先级：事件 > 参考（缺主键）> 明细（有维度）> 宽表
        if event:
            shape, event_key = "event", event[:1]
        elif not entity or not time:
            shape, event_key = "reference", []
        elif dims:
            shape, event_key = "detail", []
        else:
            shape, event_key = "wide", []

        base_keys = []
        if rtype:
            base_keys.append(rtype[0])
        if period:
            base_keys.append(period[0])

        out[str(ds.dataset_id)] = {
            "name": ds.dataset_name,
            "shape": shape,
            "entity": entity[0] if entity else "",
            "time": time[0] if time else "",
            "base_keys": base_keys,
            "dimensions": dims,
            "event_key": event_key,
            # dedup/aggregate 由 shape 派生，运行时按此执行
            "dedup": ("entity_time_event" if shape == "event"
                      else "entity_time_dimensions" if shape == "detail"
                      else "entity_time" if shape == "wide" else "none"),
            "aggregate": ("event" if shape == "event" else
                          "detail" if shape == "detail" else
                          "wide" if shape == "wide" else "skip"),
            "include_total": False,
            "total_keywords": ["合计", "小计"],
            "memo_prefixes": ["其中", "减"],
            "code_maps": cms,
        }
    return out


def main():
    """生成并写出 config/dataset_registry.yaml，并打印明细/事件表便于人工确认。"""
    reg = build()
    path = ROOT / "config" / "dataset_registry.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"datasets": reg}, f, allow_unicode=True, sort_keys=False)
    from collections import Counter
    shapes = Counter(v["shape"] for v in reg.values())
    log.info("注册表已生成 %s（%s 张表）: %s", path, len(reg), dict(shapes))
    # 打印明细/事件表便于确认
    for did, v in reg.items():
        if v["shape"] in ("detail", "event"):
            log.info("  %s %s | shape=%s dims=%s event_key=%s code_maps=%s",
                     did, v["name"], v["shape"], v["dimensions"], v["event_key"],
                     list(v["code_maps"].keys()))


if __name__ == "__main__":
    main()
