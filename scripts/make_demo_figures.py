"""生成演示用「大白话」图：架构图 / 检查引擎图 / 辩论流程图 / 流程图 / 迭代图。

输出到 assets/：
    demo_架构图.png  demo_引擎图.png  demo_辩论图.png  demo_流程图.png  demo_迭代图.png
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
RED, BLUE2, GREY = "#d9534f", "#2f6bff", "#8a93a3"


def _canvas(w, h):
    fig, ax = plt.subplots(figsize=(w, h), dpi=170)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    return fig, ax


def _box(ax, x, y, w, h, color, title, sub="", tsize=13, ssize=9.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                 boxstyle="round,pad=0.03,rounding_size=0.14",
                 linewidth=2, edgecolor=color, facecolor=color + "1a"))
    ax.text(x + w / 2, y + h / 2 + (0.28 if sub else 0), title, fontsize=tsize,
            color=color, ha="center", va="center", fontweight="bold")
    if sub:
        ax.text(x + w / 2, y + h / 2 - 0.34, sub, fontsize=ssize, color="#555555",
                ha="center", va="center")


def _arrow(ax, p1, p2, color=GREY, style="-|>", lw=1.6, ls="-"):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=16,
                 color=color, linewidth=lw, linestyle=ls,
                 connectionstyle="arc3,rad=0"))


# ────────────── ① 架构图 ──────────────
def arch():
    layers = [
        ("数据", "公司财报里的一堆数字", "收入 · 研发 · 税 · 资产", BLUE),
        ("知识", "一柜子税法政策", "5,593 条政策随时查", PURPLE),
        ("证据", "每条信息都记「从哪来」", "来源 · 可信度 · 是否采用", TEAL),
        ("技能 + 检查引擎", "24 个「税务小专家」逐个过三关", "先看有没有 → 再看异不异常 → 再看政策对不对", GREEN),
        ("AI 助手（辩论）", "4 个助手开会对质", "找机会 · 查合规 · 下结论 · 写建议", AMBER),
        ("结论", "给出「能查证」的答案", "结论 + 证据链 + 报告", ORANGE),
    ]
    fig, ax = _canvas(9.8, 8.0)
    y, h, gap = 9.05, 1.20, 0.26
    for i, (t, d, s, c) in enumerate(layers, 1):
        ax.add_patch(FancyBboxPatch((0.7, y - h), 8.6, h,
                     boxstyle="round,pad=0.02,rounding_size=0.12",
                     linewidth=2, edgecolor=c, facecolor=c + "1c"))
        ax.add_patch(plt.Circle((1.35, y - h / 2), 0.34, color=c, zorder=3))
        ax.text(1.35, y - h / 2, str(i), color="white", ha="center", va="center",
                fontsize=15, fontweight="bold", zorder=4)
        ax.text(2.05, y - h / 2 + 0.24, t, fontsize=13.5, color=c, va="center", fontweight="bold")
        ax.text(2.05, y - h / 2 - 0.26, d, fontsize=10.5, color="#333333", va="center")
        ax.text(9.1, y - h / 2, s, fontsize=8.8, color="#777777", va="center", ha="right")
        if i < len(layers):
            ax.add_patch(FancyArrowPatch((5.0, y - h - 0.02), (5.0, y - h - gap + 0.02),
                         arrowstyle="-|>", mutation_scale=16, color="#9aa4b2", linewidth=1.6))
        y -= (h + gap)
    ax.text(5.0, 9.65, "系统像一条「流水线」：从财报出发，最后给出能查证的答案",
            fontsize=15, ha="center", fontweight="bold", color="#16233d")
    ax.text(5.0, 0.22, "一句话：数字进来，政策配上，证据留痕，结论可查。",
            fontsize=11.5, ha="center", color="#565f6e")
    fig.tight_layout()
    fig.savefig(OUT / "demo_架构图.png", bbox_inches="tight")
    plt.close(fig)


# ────────────── ② 检查引擎图（三级门禁）──────────────
def engine():
    gates = [
        ("输入", "一家公司的全部数据", 8.4, BLUE, ""),
        ("第 1 关 · L0 事实存在", "这条线「有没有」？", 7.4, PURPLE, "有 → 候选池"),
        ("第 2 关 · L1 异常信号", "是不是「不正常」？", 6.4, TEAL, "异常 → 值得查"),
        ("第 3 关 · L2 政策匹配", "政策对不对、条件够不够？", 5.4, GREEN, "匹配 → 深算"),
        ("证据门禁", "关键数据「齐不齐」？", 4.4, AMBER, "齐 → PASS"),
    ]
    fig, ax = _canvas(10.2, 7.8)
    y, h, gap = 8.9, 1.05, 0.42
    for t, q, w, c, note in gates:
        x = (10 - w) / 2
        ax.add_patch(FancyBboxPatch((x, y - h), w, h,
                     boxstyle="round,pad=0.02,rounding_size=0.12",
                     linewidth=2, edgecolor=c, facecolor=c + "1c"))
        ax.text(x + w / 2, y - h / 2 + 0.22, t, fontsize=12.5, color=c, ha="center",
                va="center", fontweight="bold")
        ax.text(x + w / 2, y - h / 2 - 0.24, q, fontsize=10, color="#333333", ha="center", va="center")
        ax.text(9.85, y - h / 2, note, fontsize=8.6, color="#888888", ha="right", va="center")
        ax.add_patch(FancyArrowPatch((5.0, y - h - 0.02), (5.0, y - h - gap + 0.06),
                     arrowstyle="-|>", mutation_scale=15, color="#9aa4b2", linewidth=1.5))
        y -= (h + gap)
    # 出口
    ax.add_patch(FancyBboxPatch((2.6, y - 0.95), 4.8, 0.95,
                 boxstyle="round,pad=0.03,rounding_size=0.14",
                 linewidth=2.4, edgecolor=ORANGE, facecolor=ORANGE + "22"))
    ax.text(5.0, y - 0.48, "出口：只把「最值得查」的方向送去深度分析", fontsize=11.5,
            color=ORANGE, ha="center", va="center", fontweight="bold")
    ax.text(5.0, 9.55, "检查引擎：24 个技能，每个都要过「三关」", fontsize=15.5, ha="center",
            fontweight="bold", color="#16233d")
    ax.text(5.0, 0.30, "把「可能的省税点」筛成「值得认真算的方向」——不放过、也不乱报。",
            fontsize=11, ha="center", color="#565f6e")
    fig.tight_layout()
    fig.savefig(OUT / "demo_引擎图.png", bbox_inches="tight")
    plt.close(fig)


# ────────────── ③ 辩论流程图 ──────────────
def debate():
    fig, ax = _canvas(9.8, 8.0)
    _box(ax, 0.7, 7.1, 3.5, 1.7, RED, "Red · 找机会", "从数据里提出「可能的省税点」", 13, 9.5)
    _box(ax, 5.8, 7.1, 3.5, 1.7, BLUE2, "Blue · 查合规", "逐条质疑：合不合规？有没有风险？", 13, 9.5)
    ax.add_patch(FancyArrowPatch((4.25, 8.15), (5.75, 8.15), arrowstyle="-|>",
                 mutation_scale=16, color=GREY, linewidth=1.8))
    ax.add_patch(FancyArrowPatch((5.75, 7.75), (4.25, 7.75), arrowstyle="-|>",
                 mutation_scale=16, color=GREY, linewidth=1.8))
    ax.text(5.0, 8.55, "多轮对质", fontsize=11, color="#333333", ha="center", fontweight="bold")
    ax.text(5.0, 6.82, "有新证据就继续，没有就停", fontsize=9.5, color="#777777", ha="center")

    _box(ax, 3.25, 4.4, 3.5, 1.6, GREEN, "Green · 综合决策", "由「规则」下结论，AI 只补充理由", 13, 9)
    _arrow(ax, (2.45, 7.1), (4.2, 6.0), GREY)
    _arrow(ax, (7.55, 7.1), (5.8, 6.0), GREY)

    _box(ax, 3.0, 2.2, 4.0, 1.5, ORANGE,
         "结论四态", "已确认 / 情景测算 / 证据不足 / 不推荐", 12.5, 9)
    _arrow(ax, (5.0, 4.4), (5.0, 3.7), GREY)

    _box(ax, 7.15, 4.4, 2.15, 1.6, PURPLE, "Advisor", "税务顾问\n意见书", 11.5, 9)
    ax.add_patch(FancyArrowPatch((6.75, 5.2), (7.15, 5.2), arrowstyle="-|>",
                 mutation_scale=14, color=PURPLE, linewidth=1.4, linestyle="--"))

    ax.text(5.0, 9.5, "辩论流程：红蓝对质，绿方拍板", fontsize=15.5, ha="center",
            fontweight="bold", color="#16233d")
    ax.text(5.0, 1.35, "重点：无论怎么辩论，「金额」和「政策是否有效」都由规则说了算，AI 改不了。",
            fontsize=11, ha="center", color=RED, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "demo_辩论图.png", bbox_inches="tight")
    plt.close(fig)


# ────────────── ④ 流程图 ──────────────
def flow():
    steps = [
        ("①", "选一家公司", "输入股票代码"),
        ("②", "读它的财报", "自动读 2024 年数据"),
        ("③", "过检查引擎", "24 个技能过三关，筛方向"),
        ("④", "红蓝辩论", "找机会 vs 查合规，多轮对质"),
        ("⑤", "算一算", "能省多少税（确定性计算）"),
        ("⑥", "给出结论", "结论 + 证据链，可逐条核对"),
    ]
    fig, ax = _canvas(9.8, 7.2)
    cols, bw, bh = 3, 2.7, 1.9
    xs, ys = [0.55, 3.65, 6.75], [5.7, 2.6]
    for idx, (num, t, d) in enumerate(steps):
        r, c = divmod(idx, cols)
        if r == 1:                    # 第二行反向排列（蛇形），使流程为 1→2→3→4→5→6
            c = cols - 1 - c
        x, y = xs[c], ys[r]
        col = [BLUE, PURPLE, TEAL, AMBER, GREEN, ORANGE][idx]
        ax.add_patch(FancyBboxPatch((x, y), bw, bh,
                     boxstyle="round,pad=0.03,rounding_size=0.14",
                     linewidth=2, edgecolor=col, facecolor=col + "1a"))
        ax.text(x + bw / 2, y + bh - 0.42, num, fontsize=20, color=col,
                ha="center", va="center", fontweight="bold")
        ax.text(x + bw / 2, y + bh - 0.92, t, fontsize=13, color="#16233d",
                ha="center", va="center", fontweight="bold")
        ax.text(x + bw / 2, y + bh - 1.42, d, fontsize=9, color="#555555",
                ha="center", va="center")
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


# ────────────── ⑤ 迭代图 ──────────────
def timeline():
    items = [
        ("v1", "数据 + 架构", "88 张表并入，搭好骨架", BLUE),
        ("v2", "证据层", "每条信息都记来源", PURPLE),
        ("v3", "检查引擎", "24 技能 + 三级门禁 + 147 用例", TEAL),
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
    engine()
    debate()
    flow()
    timeline()
    print("已生成：", [p.name for p in sorted(OUT.glob("demo_*.png"))])
