"""真阳/对照样本采集（Update 8.3 效果评估 · 步骤 1）。

数据来源：**巨潮资讯网全文检索**（竞赛手册推荐数据源，免登录 GET）。
    https://www.cninfo.com.cn/new/fulltextSearch/full?searchkey=..&sdate=..&edate=..

做两件事：
1. **真阳组**：对每个"状态变化类"事件关键词检索 2025-05-01~2025-12-31 的公告，
   剥离标题高亮、过滤掉"重新认定/延续"等非状态变化，与项目 master 取交集，
   随机抽 30 家（每类尽量 ≥3，且高企类要求 2024 尚未是高新）。
2. **对照组**：为每个真阳公司按"同行业 + 营收规模相近"匹配 2~3 家未发生任何
   主标签事件的公司。

输出：
    validation/ground_truth_2025.csv
    validation/control_group_2025.csv

用法：python scripts/collect_ground_truth.py [--seed 42] [--positives 30] [--controls-per 2]
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MASTER = ROOT / "data_processed" / "master.parquet"
OUT_POS = ROOT / "validation" / "ground_truth_2025.csv"
OUT_CTL = ROOT / "validation" / "control_group_2025.csv"

SEARCH_URL = "https://www.cninfo.com.cn/new/fulltextSearch/full"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://www.cninfo.com.cn/new/fulltextSearch?notautosubmit=&keyWord=",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# 事件窗口：严格晚于 FY2024 年报披露日（假设 2025-04-30 前披露），消除时间泄漏
SDATE = "2025-05-01"
EDATE = "2025-12-31"

# —— 主标签（状态变化类）：新发生的事件，用 2024 数据发现才有意义 ——
EVENTS = [
    {
        "event_type": "首次高新技术企业认定",
        "keywords": ["高新技术企业"],
        "direction": "高新技术企业",
        "skill": "hightech_enterprise",
        "include": ["认定", "通过"],
        "exclude": ["重新认定", "再次", "延续", "复审", "公示名单"],
        "need_not_hightech": True,          # 优先取 2024 尚未是高新（真正状态变化）
        "quota": 8,
    },
    {
        "event_type": "股权激励/员工持股",
        "keywords": ["股权激励", "限制性股票", "员工持股"],
        "direction": "股份支付",
        "skill": "share_based_payment",
        "include": ["激励", "持股", "授予"],
        "exclude": ["回购注销", "终止", "法律意见"],
        "need_not_hightech": False,
        "quota": 8,
    },
    {
        "event_type": "重组/股权划转/吸收合并",
        "keywords": ["股权划转", "吸收合并", "资产划转"],
        "direction": "组织架构",
        "skill": "organizational_restructuring",
        "include": [],
        "exclude": ["法律意见", "独立财务顾问"],
        "need_not_hightech": False,
        "quota": 7,
    },
    {
        "event_type": "股权转让/处置",
        "keywords": ["股权转让", "股份转让"],
        "direction": "投资收益",
        "skill": "investment_income",
        "include": [],
        # 排除：股东/实控人层面的转让、可转债/保荐等非"公司处置股权"事件
        "exclude": ["受让", "增持", "质押", "法律意见", "独立董事", "股东",
                    "实际控制人", "保荐", "可转换", "债券", "挂牌", "权益变动", "提示性"],
        "need_not_hightech": False,
        "quota": 7,
    },
]

_TAG = re.compile(r"<[^>]+>")


def _clean(title: str) -> str:
    """剥离标题中的 <em> 高亮标记与多余空白。"""
    return _TAG.sub("", title or "").strip()


def search(keyword: str, max_pages: int = 12) -> list[dict]:
    """巨潮全文检索（标题），返回去重后的公告列表。"""
    out: list[dict] = []
    page = 1
    while page <= max_pages:
        params = {
            "searchkey": keyword, "sdate": SDATE, "edate": EDATE,
            "isfulltext": "false", "sortName": "pubdate", "sortType": "desc",
            "pageNum": page,
        }
        for attempt in range(3):
            try:
                r = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=30)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:  # noqa: BLE001 网络抖动：退避重试
                if attempt == 2:
                    print(f"  [warn] {keyword} page{page} 失败：{e}")
                    return out
                time.sleep(1.5 * (attempt + 1))
        anns = data.get("announcements") or []
        for a in anns:
            code = str(a.get("secCode") or "").strip()
            if not re.fullmatch(r"\d{6}", code):   # 仅 A 股 6 位代码
                continue
            out.append({
                "stock_code": code,
                "stock_name": a.get("secName"),
                "event_title": _clean(a.get("announcementTitle")),
                "announcement_id": a.get("announcementId"),
                "adjunct_url": a.get("adjunctUrl"),
                "announce_date": pd.to_datetime(a.get("announcementTime"), unit="ms").strftime("%Y-%m-%d")
                if a.get("announcementTime") else "",
            })
        total_pages = int(data.get("totalpages") or 1)
        if page >= total_pages or not anns:
            break
        page += 1
        time.sleep(0.4)          # 节流，避免被限流
    return out


def _match(title: str, ev: dict) -> bool:
    """按事件规则判断标题是否命中。"""
    if any(x in title for x in ev["exclude"]):
        return False
    if ev["include"] and not any(x in title for x in ev["include"]):
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--positives", type=int, default=30)
    ap.add_argument("--controls-per", type=int, default=2)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    # 1) 读 master 2024：公司宇宙 + 行业/营收/高企状态
    cols = ["stock_code", "year", "short_name", "industry_name",
            "csmar__OperatingRevenue", "is_hightech"]
    m = pd.read_parquet(MASTER, columns=cols)
    m = m[m["year"] == 2024].copy()
    m["stock_code"] = m["stock_code"].astype(str).str.zfill(6)
    m = m.drop_duplicates("stock_code").set_index("stock_code")
    universe = set(m.index)
    print(f"master 2024 公司数：{len(universe)}")

    # 2) 逐事件检索 → 交集 → 候选
    used: set[str] = set()          # 已入选真阳
    event_company_sets: dict[str, set[str]] = {}   # 事件 → 命中公司集合（供对照排除）
    positives: list[dict] = []

    for ev in EVENTS:
        pool: dict[str, dict] = {}
        for kw in ev["keywords"]:
            for a in search(kw):
                if a["stock_code"] not in universe:
                    continue
                if not _match(a["event_title"], ev):
                    continue
                pool.setdefault(a["stock_code"], a)
        event_company_sets[ev["event_type"]] = set(pool.keys())

        # 排除已入选其它事件的公司，避免同一公司重复计数
        cands = [a for a in pool.values() if a["stock_code"] not in used]
        rng.shuffle(cands)
        # 高企类：优先取"2024 尚未是高新"的公司（真正的状态变化），不足再放宽
        if ev["need_not_hightech"]:
            cands.sort(key=lambda a: bool(m.loc[a["stock_code"], "is_hightech"]))
        take = cands[: ev["quota"]]
        for a in take:
            used.add(a["stock_code"])
            positives.append({
                "stock_code": a["stock_code"], "stock_name": a["stock_name"],
                "group": "positive", "event_type": ev["event_type"],
                "mapped_direction": ev["direction"], "mapped_skill": ev["skill"],
                "event_title": a["event_title"], "announce_date": a["announce_date"],
                "source_url": f"https://static.cninfo.com.cn/{a['adjunct_url']}" if a.get("adjunct_url") else "",
                "industry": m.loc[a["stock_code"], "industry_name"],
                "revenue": float(m.loc[a["stock_code"], "csmar__OperatingRevenue"] or 0),
                "already_hightech_2024": bool(m.loc[a["stock_code"], "is_hightech"]),
            })
        print(f"  {ev['event_type']}：候选 {len(cands)} → 取 {len(take)}")

    positives = positives[: args.positives]
    pos_df = pd.DataFrame(positives)
    print(f"\n真阳组：{len(pos_df)} 家")

    # 3) 对照组：同行业 + 营收规模相近，且未发生任何主标签事件
    all_event_codes = set().union(*event_company_sets.values()) if event_company_sets else set()
    ctl_rows: list[dict] = []
    used_ctl: set[str] = set()
    for _, row in pos_df.iterrows():
        rev = row["revenue"] or 0
        lo, hi = rev * 0.5, rev * 2.0
        pool = m[(m["industry_name"] == row["industry"])].copy()
        pool = pool[~pool.index.isin(used | all_event_codes | used_ctl)]
        if rev > 0:
            pool = pool[(pool["csmar__OperatingRevenue"] >= lo) &
                        (pool["csmar__OperatingRevenue"] <= hi)]
        if pool.empty:      # 放宽：仅同行业
            pool = m[m["industry_name"] == row["industry"]]
            pool = pool[~pool.index.isin(used | all_event_codes | used_ctl)]
        if pool.empty:      # 再放宽：任意未命中公司
            pool = m[~m.index.isin(used | all_event_codes | used_ctl)]
        idx = list(pool.index)
        rng.shuffle(idx)
        for c in idx[: args.controls_per]:
            used_ctl.add(c)
            ctl_rows.append({
                "stock_code": c, "stock_name": m.loc[c, "short_name"],
                "group": "control", "event_type": row["event_type"],
                "mapped_direction": row["mapped_direction"], "mapped_skill": row["mapped_skill"],
                "event_title": "", "announce_date": "",
                "source_url": "", "industry": m.loc[c, "industry_name"],
                "revenue": float(m.loc[c, "csmar__OperatingRevenue"] or 0),
                "already_hightech_2024": bool(m.loc[c, "is_hightech"]),
            })
    ctl_df = pd.DataFrame(ctl_rows)
    print(f"对照组：{len(ctl_df)} 家（每真阳 {args.controls_per} 家）")

    OUT_POS.parent.mkdir(parents=True, exist_ok=True)
    pos_df.to_csv(OUT_POS, index=False, encoding="utf-8-sig")
    ctl_df.to_csv(OUT_CTL, index=False, encoding="utf-8-sig")
    print(f"\n已写出：\n  {OUT_POS}\n  {OUT_CTL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
