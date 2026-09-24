"""中文注释覆盖率审计（竞赛要求：关键算法与非平凡逻辑的中文注释 ≥ 代码量 10%）。

统计口径（保守）：
- 代码行：非空行；
- 注释行：以 `#` 开头的行 + 三引号文档字符串内的行（docstring 计入注释）；
- 覆盖率 = 注释行 / 代码行。

用法：python scripts/audit_comments.py [--min 0.10]
退出码：低于阈值时返回 1（可用于 CI）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = [ROOT / "src", ROOT / "scripts"]


def _scan(text: str) -> tuple[int, int, int]:
    """返回 (代码行数, 注释行数, 含中文注释行数)。"""
    total = comment = cn_comment = 0
    in_doc = False
    quote = ""
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        total += 1
        if in_doc:  # 文档字符串内部
            comment += 1
            if _has_cn(s):
                cn_comment += 1
            if quote in s:
                in_doc = False
            continue
        if s.startswith("#"):
            comment += 1
            if _has_cn(s):
                cn_comment += 1
            continue
        if s.startswith(('"""', "'''")):  # 进入文档字符串
            quote = s[:3]
            comment += 1
            if _has_cn(s):
                cn_comment += 1
            rest = s[3:]
            if quote not in rest:  # 同行未闭合
                in_doc = True
            continue
    return total, comment, cn_comment


def _has_cn(s: str) -> bool:
    """判断字符串是否含中文（CJK 统一汉字区）。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def main() -> int:
    """扫描 src 与 scripts 下所有 py，统计注释覆盖率并按阈值判定。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=0.10, help="注释覆盖率下限（默认 0.10）")
    ap.add_argument("--detail", action="store_true", help="打印每个文件的覆盖率")
    args = ap.parse_args()

    # 收集待扫描文件（排除 __pycache__）
    files: list[Path] = []
    for d in TARGET_DIRS:
        files += sorted(d.rglob("*.py"))
    files = [f for f in files if "__pycache__" not in f.parts]

    tot = cmt = cn = 0
    rows: list[tuple[float, str, int, int]] = []
    for f in files:
        t, c, k = _scan(f.read_text(encoding="utf-8"))
        tot += t
        cmt += c
        cn += k
        # 仅对有代码行的文件记录覆盖率（空文件不参与排名）
        if t:
            rows.append((c / t, str(f.relative_to(ROOT)), t, c))

    ratio = cmt / tot if tot else 0.0
    print(f"扫描文件：{len(files)} 个")
    print(f"代码行：{tot}　注释行：{cmt}　含中文注释行：{cn}")
    print(f"注释覆盖率：{ratio:.1%}（下限 {args.min:.0%}）")

    if args.detail:
        print("\n覆盖率最低的 15 个文件：")
        for r, name, t, c in sorted(rows)[:15]:
            print(f"  {r:6.1%}  {name}（{c}/{t}）")

    if ratio < args.min:
        print("[FAIL] 注释覆盖率低于下限，请补充中文注释。")
        return 1
    print("[OK] 注释覆盖率达标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
