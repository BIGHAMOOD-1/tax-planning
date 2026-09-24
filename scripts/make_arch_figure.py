"""生成项目方案书用「系统架构图」（分层，文本自动换行不出框）。

输出：assets/架构图.png
用法：python scripts/make_arch_figure.py
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
OUT = ROOT / "assets" / "架构图.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

for cand in (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyh.ttc"):
    if Path(cand).exists():
        try:
            font_manager.fontManager.addfont(cand)
            rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=cand).get_name()]
            break
        except Exception:  # noqa: BLE001
            continue
rcParams["axes.unicode_minus"] = False

LAYERS = [
    ("数据层", "88 张 CSMAR 数据表 → 主表 714 字段 → 企业画像 845 字段（5,070 家公司 / 23,284 公司·年）", "#2f6bff"),
    ("知识层", "政策库 5,593 条 + 案例库 1,021 篇 → 向量 + BM25 混合召回 + Reranker 重排", "#6d5efc"),
    ("证据层", "证据契约 · 来源采用 · 冲突复核 · 血缘（事实三维：来源 / 校验 / 采用）", "#12b5a5"),
    ("技能层", "24 个 Tax Skill（规则 + 条件 + 计算器 + 政策 + 证据要求）", "#2fae6a"),
    ("代理层", "机会分析 Red · 合规审查 Blue · 综合决策 Green · 税务顾问 Advisor", "#c9a227"),
    ("产出层", "结论四态（已确认 / 情景 / 证据不足 / 不推荐）+ 7 节点证据链 + 报告导出（Markdown / PDF）", "#d97706"),
]

X0, X1 = 0.55, 9.45          # 框左右边界
WRAP = 46                    # 每行字符数
LINE_H = 0.42                # 描述行高
TITLE_H = 0.52               # 标题行高
PAD = 0.22


def main() -> None:
    # 先计算每层高度
    blocks = []
    for title, desc, color in LAYERS:
        lines = textwrap.wrap(desc, width=WRAP) or [""]
        h = PAD + TITLE_H + len(lines) * LINE_H + PAD
        blocks.append((title, lines, color, h))

    total = sum(b[3] for b in blocks) + 0.34 * (len(blocks) - 1)
    fig_h = 1.75 + total
    fig, ax = plt.subplots(figsize=(9.6, fig_h), dpi=170)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, fig_h)
    ax.axis("off")

    y = fig_h - 0.95
    centers = []
    for title, lines, color, h in blocks:
        ax.add_patch(FancyBboxPatch((X0, y - h), X1 - X0, h,
                                    boxstyle="round,pad=0.02,rounding_size=0.10",
                                    linewidth=1.6, edgecolor=color, facecolor=color + "18"))
        ax.text(X0 + 0.28, y - PAD - TITLE_H / 2 + 0.02, title, fontsize=12.5, color=color,
                va="center", ha="left", fontweight="bold")
        ty = y - PAD - TITLE_H - LINE_H / 2 + 0.02
        for ln in lines:
            ax.text(X0 + 0.30, ty, ln, fontsize=9.6, color="#333333", va="center", ha="left")
            ty -= LINE_H
        centers.append(y - h / 2)
        y -= (h + 0.34)

    for i in range(len(blocks) - 1):
        y0 = centers[i] - blocks[i][3] / 2
        y1 = centers[i + 1] + blocks[i + 1][3] / 2
        ax.add_patch(FancyArrowPatch((5.0, y0 - 0.02), (5.0, y1 + 0.02),
                                     arrowstyle="-|>", mutation_scale=14,
                                     color="#9aa4b2", linewidth=1.4))

    ax.text(5.0, fig_h - 0.42, "智能税务筹划系统 · 分层架构", fontsize=15.5,
            ha="center", fontweight="bold", color="#16233d")
    ax.text(5.0, 0.28, "设计原则：确定性优先 —— 金额与结论由规则与门禁产出；大模型只负责「发现」与「解释」",
            fontsize=9.6, ha="center", color="#565f6e")
    fig.tight_layout()
    fig.savefig(OUT, bbox_inches="tight")
    plt.close(fig)
    print("已生成：", OUT)


if __name__ == "__main__":
    main()
