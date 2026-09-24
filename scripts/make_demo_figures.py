"""生成演示用「大白话」三张图：架构图 / 流程图 / 更新迭代图。

输出到 assets/：
    demo_架构图.png
    demo_流程图.png
    demo_迭代图.png
用法：python scripts/make_demo_figures.py
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager, rcParams
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets"
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

BLUE, PURPLE, TEAL, GREEN, AMBER, ORANGE = "#2f6bff", "#6d5efc", "#12b5a5", "#2fae6a", "#c9a227", "#d97706"


def _canvas(w, h):
    fig, ax = plt.subplots(figsize=(w, h), dpi=170)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    return fig, ax


# ────────────── ① 架构图（大白话）──────────────
def arch():
    layers = [
        ("数据", "公司财报里的一堆数字", "收入 · 研发 · 税 · 资产", BLUE),
        ("知识", "一柜子税法政策", "5,593 条政策随时查", PURPLE),
        ("证据", "每条信息都记「从哪来」", "来源 · 可信度 · 是否采用", TEAL),
        ("技能", "24 个「税务小专家」", "每个专管一类税", GREEN),
        ("助手", "4 个 AI 助手一起讨论", "找机会 · 查合规 · 下结论 · 写建议", AMBER),
        ("结论", "给出「能查证」的答案", "结论 + 证据链 + 报告", ORANGE),
    ]
    fig, ax = _canvas(9.6, 7.6)
    y, h, gap = 9.05, 1.18, 0.28
    for i, (t, d, s, c) in enumerate(layers, 1):
        ax.add_patch(FancyBboxPatch((0.7, y - h), 8.6, h,
                     boxstyle="round,pad=0.02,rounding_size=0.12",
                     linewidth=2, edgecolor=c, facecolor=c + "1c"))
        ax.add_patch(plt.Circle((1.35, y - h / 2), 0.34, color=c, zorder=3))
        ax.text(1.35, y - h / 2, str(i), color="white", ha="center", va="center",
                fontsize=15, fontweight="bold", zorder=4)
        ax.text(2.05, y - h / 2 + 0.22, t, fontsize=14, color=c, va="center", fontweight="bold")
        ax.text(2.05, y - h / 2 - 0.26, d, fontsize=10.5, color="#333333", va="center")
        ax.text(9.1, y - h / 2, s, fontsize=9.5, color="#777777", va="center", ha="right")
        if i < len(layers):
            ax.add_patch(FancyArrowPatch((5.0, y - h - 0.02), (5.0, y - h - gap + 0.02),
                         arrowstyle="-|>", mutation_scale=16, color="#9aa4b2", linewidth=1.6))
        y -= (h + gap)
    ax.text(5.0, 9.65, "系统像一条「流水线」：从财报出发，最后给出能查证的答案",
            fontsize=15, ha="center", fontweight="bold", color="#16233d")
    ax.text(5.0, 0.28, "一句话：数字进来，政策配上，证据留痕，结论可查。",
            fontsize=11.5, ha="center", color="#565f6e")
    fig.tight_layout()
    fig.savefig(OUT / "demo_架构图.png", bbox_inches="tight")
    plt.close(fig)


# ────────────── ② 流程图（大白话）──────────────
def flow():
    steps = [
        ("①", "选一家公司", "输入股票代码"),
        ("②", "读它的财报", "自动读 2024 年数据"),
        ("③", "找出省税点", "比如研发、折旧、补助"),
        ("④", "查对应税法", "只查「还在生效」的"),
        ("⑤", "算一算", "能省多少税（确定性计算）"),
        ("⑥", "给出结论", "结论 + 证据链，可逐条核对"),
    ]
    fig, ax = _canvas(9.8, 7.2)
    cols = 3
    bw, bh = 2.7, 1.9
    xs = [0.55, 3.65, 6.75]
    ys = [5.7, 2.6]
    centers = []
    for idx, (num, t, d) in enumerate(steps):
        r, c = divmod(idx, cols)
        x, y = xs[c], ys[r]
        col = [BLUE, PURPLE, TEAL, GREEN, AMBER, ORANGE][idx]
        ax.add_patch(FancyBboxPatch((x, y), bw, bh,
                     boxstyle="round,pad=0.03,rounding_size=0.14",
                     linewidth=2, edgecolor=col, facecolor=col + "1a"))
        ax.text(x + bw / 2, y + bh - 0.42, num, fontsize=20, color=col,
                ha="center", va="center", fontweight="bold")
        ax.text(x + bw / 2, y + bh - 0.92, t, fontsize=13, color="#16233d",
                ha="center", va="center", fontweight="bold")
        ax.text(x + bw / 2, y + bh - 1.42, d, fontsize=9.5, color="#555555",
                ha="center", va="center")
        centers.append((x + bw / 2, y, x, y + bh))
    # 箭头：①→②→③ 向右，③→④ 换行，④→⑤→⑥ 向右
    def arrow(x1, y1, x2, y2):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                     mutation_scale=16, color="#9aa4b2", linewidth=1.6))
    arrow(xs[0] + bw, ys[0] + bh / 2, xs[1], ys[0] + bh / 2)
    arrow(xs[1] + bw, ys[0] + bh / 2, xs[2], ys[0] + bh / 2)
    arrow(xs[2] + bw / 2, ys[0], xs[2] + bw / 2, ys[1] + bh)
    arrow(xs[2], ys[1] + bh / 2, xs[1] + bw, ys[1] + bh / 2)
    arrow(xs[1], ys[1] + bh / 2, xs[0] + bw, ys[1] + bh / 2)
    ax.text(5.0, 9.4, "系统怎么干活？六步走", fontsize=17, ha="center",
            fontweight="bold", color="#16233d")
    ax.text(5.0, 1.35, "重点：每一步都留下出处 —— 结论不是「拍脑袋」，而是「能查证」。",
            fontsize=12, ha="center", color=TEAL, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "demo_流程图.png", bbox_inches="tight")
    plt.close(fig)


# ────────────── ③ 更新迭代图 ──────────────
def timeline():
    items = [
        ("v1", "数据 + 架构", "88 张表并入，搭好骨架", BLUE),
        ("v2", "证据层", "每条信息都记来源", PURPLE),
        ("v3", "审查与验证", "147 个用例，字段级口径", TEAL),
        ("v4", "界面", "三栏工作台，看得见", GREEN),
        ("v5", "可信度工程", "金额语义 + 政策时效门禁", AMBER),
        ("v6", "意见与设置", "税务顾问意见书", ORANGE),
        ("v7", "评审整改", "Git / 测试 / 金额语义加固", BLUE),
        ("v8", "合规与评估", "AI 声明 · PDF · 效果评估", PURPLE),
        ("v9", "演示交付", "讲清「为什么可信」", TEAL),
    ]
    fig, ax = _canvas(11.0, 6.0)
    ax.plot([0.6, 9.4], [5.0, 5.0], color="#c9cfda", linewidth=3, zorder=1)
    n = len(items)
    xs = [0.9 + i * (8.4 / (n - 1)) for i in range(n)]
    for i, ((v, t, d, c), x) in enumerate(zip(items, xs)):
        up = (i % 2 == 0)
        ax.plot([x], [5.0], marker="o", markersize=13, color=c, zorder=3)
        ax.text(x, 5.0, v[1:], color="white", ha="center", va="center",
                fontsize=8.5, fontweight="bold", zorder=4)
        ty = 6.15 if up else 3.85
        ax.plot([x, x], [5.0, ty], color="#c9cfda", linewidth=1.2, zorder=1)
        ax.text(x, ty + 0.42, v, color=c, ha="center", va="center", fontsize=11, fontweight="bold")
        ax.text(x, ty, t, color="#16233d", ha="center", va="center", fontsize=9.5, fontweight="bold")
        for j, ln in enumerate(textwrap.wrap(d, 11)):
            ax.text(x, ty - 0.34 - j * 0.30, ln, color="#666666", ha="center", va="center", fontsize=7.8)
    ax.text(5.0, 8.4, "一路升级：从「能跑」到「可信」", fontsize=17, ha="center",
            fontweight="bold", color="#16233d")
    ax.text(5.0, 1.55, "越往后，越强调两件事：① 每一步都有出处　② 结论要保守、不夸大",
            fontsize=11.5, ha="center", color="#565f6e")
    fig.tight_layout()
    fig.savefig(OUT / "demo_迭代图.png", bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    arch()
    flow()
    timeline()
    print("已生成：", [p.name for p in sorted(OUT.glob("demo_*.png"))])
