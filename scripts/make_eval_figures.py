"""生成效果评估配图（交付用）。

输出到 validation/eval_v3/：
    fig_compare.png      本系统 vs 大模型+政策检索（同数据）：可核验性 / 证据 / 适用性
    fig_effectiveness.png 独立有效性：系统仅用 2024 数据，对 2025 真实事件方向的覆盖
用法：python scripts/make_eval_figures.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager, rcParams
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "validation" / "eval_v3"
OUT.mkdir(parents=True, exist_ok=True)

for cand in (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyh.ttc"):
    if Path(cand).exists():
        try:
            font_manager.fontManager.addfont(cand)
            rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=cand).get_name()]
            break
        except Exception:  # noqa: BLE001
            continue
rcParams["axes.unicode_minus"] = False

OURS = "#2f6bff"
BASE = "#c9a227"
ACCENT = "#12b5a5"
GREY = "#8a93a3"


def fig_compare():
    """横向分组条形：本系统 vs 大模型+政策检索（同数据）。"""
    metrics = ["证据接地率", "政策可核验率", "政策现行有效率", "建议适用率", "引用具体文号率"]
    ours = [100.0, 100.0, 100.0, 100.0, 91.2]
    base = [0.0, 72.0, 72.0, 98.1, 79.6]
    y = list(range(len(metrics)))
    h = 0.36
    fig, ax = plt.subplots(figsize=(8.4, 4.6), dpi=160)
    b1 = ax.barh([i + h / 2 for i in y], ours, h, label="本系统（完整版）", color=OURS)
    b2 = ax.barh([i - h / 2 for i in y], base, h, label="大模型 + 政策检索（同数据）", color=BASE)
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_width() + 1.5, b.get_y() + b.get_height() / 2,
                    f"{b.get_width():.0f}%", va="center", fontsize=9)
    ax.set_yticks(y)
    ax.set_yticklabels(metrics, fontsize=10)
    ax.set_xlim(0, 116)
    ax.set_xlabel("比例（%）")
    ax.set_title("同数据下：可核验性、证据支撑与适用性对比", fontsize=12)
    ax.legend(fontsize=9, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "fig_compare.png")
    plt.close(fig)


def fig_effectiveness():
    """独立有效性：系统仅用 2024 数据，对 2025 真实事件方向的覆盖。"""
    labels = ["股权转让 / 处置\n（投资收益）", "高新技术企业认定\n（研发强度）",
              "股权激励 / 员工持股\n（股份支付）", "重组 / 划转 / 合并\n（组织架构）"]
    vals = [85.7, 75.0, 25.0, 14.3]
    colors = [OURS, OURS, GREY, GREY]
    fig, ax = plt.subplots(figsize=(8.0, 4.1), dpi=160)
    bars = ax.barh(labels, vals, color=colors)
    for b, v in zip(bars, vals):
        ax.text(v + 1.5, b.get_y() + b.get_height() / 2, f"{v:.1f}%", va="center", fontsize=10)
    ax.axvline(50.0, color=ACCENT, ls="--", lw=1.2)
    ax.text(50.8, -0.7, "总体覆盖 50%", color=ACCENT, fontsize=9.5)
    ax.set_xlim(0, 100)
    ax.set_xlabel("方向覆盖率（%）")
    ax.set_title("独立有效性：仅用 2024 数据，命中了哪些 2025 年真实发生的方向", fontsize=11.5)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "fig_effectiveness.png")
    plt.close(fig)


if __name__ == "__main__":
    for p in OUT.glob("fig_*.png"):
        p.unlink()
    fig_compare()
    fig_effectiveness()
    print("已生成：", [p.name for p in sorted(OUT.glob("fig_*.png"))])
