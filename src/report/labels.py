"""术语替换（Update 6.3）：把工程代号 Red / Blue / Green 替换为业务名。

用于**存量产物**的展示与报告（无需重跑）：UI 外壳已改，但历史生成的文本里
仍可能出现 "Red 方案 / Blue 质疑 / Green 结论" 等。
字段键（red/blue/green/responses_to_blue/red_lines 等）为代码用，**不替换**（小写/含下划线，正则 `\\b` 不匹配）。
"""
from __future__ import annotations

import re

_ROLE_MAP = {"Red": "机会分析", "Blue": "合规审查", "Green": "综合决策"}
_ROLE_RE = re.compile(r"\b(Red|Blue|Green)\b")


def relabel_roles(text):
    """字符串：替换独立词 Red/Blue/Green；非字符串原样返回。"""
    if not isinstance(text, str):
        return text
    return _ROLE_RE.sub(lambda m: _ROLE_MAP[m.group(1)], text)


def relabel_roles_deep(obj):
    """递归：对 dict/list/str 结构做替换（用于产物 JSON）。"""
    if isinstance(obj, str):
        return relabel_roles(obj)
    if isinstance(obj, list):
        return [relabel_roles_deep(x) for x in obj]
    if isinstance(obj, dict):
        return {k: relabel_roles_deep(v) for k, v in obj.items()}
    return obj
