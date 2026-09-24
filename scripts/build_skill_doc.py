"""生成 skill.md：列出系统内全部 Tax Skill 及其 8 要素。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import CONFIG, get_logger  # noqa: E402
from src.skills.schema import load_all_skills  # noqa: E402

log = get_logger()


def render() -> str:
    """渲染 skill.md 文本：先总览表，再逐 Skill 展开 8 要素。"""
    skills = load_all_skills()
    # 总览表：一行一个 Skill，快速浏览版本/生效期/计算方法
    lines = ["# Tax Skill 清单", "",
             f"共 **{len(skills)}** 个 Skill。每个 Skill 含 8 要素："
             "筹划目标 / 适用政策 / 所需企业事实 / 数据来源 / 判断条件 / 计算方法 / 风险条件 / 输出格式。",
             "",
             "| Skill ID | 名称 | 方向 | 版本 | 生效期 | 计算方法 | 需证据定金额 |",
             "|---|---|---|---|---|---|---|"]
    for s in skills.values():
        eff = f"{s.effective_from or '—'} ~ {s.effective_to or '—'}"
        lines.append(f"| `{s.id}` | {s.name} | {s.direction} | {s.version} | {eff} | "
                     f"{s.calculation or '（无，仅审查）'} | "
                     f"{'是' if s.amount_requires_evidence else '否'} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 明细：逐 Skill 展开全部要素
    for s in skills.values():
        # 每个 Skill 一节：标题 + 8 要素条目
        lines.append(f"## {s.name}（`{s.id}`）")
        lines.append("")
        lines.append(f"- **筹划方向**：{s.direction}")
        lines.append(f"- **筹划目标**：{s.goal}")
        lines.append(f"- **适用政策**：")
        for p in s.policies:
            lines.append(f"  - {p}")
        lines.append(f"- **所需企业事实**：{', '.join(f'`{f}`' for f in s.required_facts) or '—'}")
        lines.append(f"- **数据来源**：{'；'.join(s.data_sources) or '—'}")
        lines.append("- **判断条件（Eligibility）**：")
        # 仅列出准入阶段的判断条件
        for c in s.conditions:
            if c.stage == "eligibility":
                lines.append(f"  - `{c.field}` {c.op} {c.value}　{c.desc}")
        lines.append(f"- **计算方法**：`{s.calculation or '（无）'}`")
        # 可选要素：有内容才输出对应小节，保持文档紧凑
        if s.calculation_adjustments:
            lines.append("- **口径调整**：")
            for a in s.calculation_adjustments:
                lines.append(f"  - {a}")
        if s.evidence_required:
            lines.append("- **所需证据**：")
            for e in s.evidence_required:
                lines.append(f"  - {e}")
        lines.append("- **风险条件**：")
        for r in s.risks:
            lines.append(f"  - {r}")
        lines.append("- **检索查询**：")
        for q in s.rag_queries:
            lines.append(f"  - {q}")
        # 节间空行，保证 Markdown 渲染正常
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    out = Path(CONFIG["paths"]["workspace"]) / "skill.md"
    out.write_text(render(), encoding="utf-8")
    log.info("已生成 %s", out)
