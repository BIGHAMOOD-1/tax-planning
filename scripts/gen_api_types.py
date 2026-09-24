"""OpenAPI → TypeScript 类型生成（Update 5.1 · 工程护栏）。

从 FastAPI 的 OpenAPI schema 生成前端类型，保证 DTO 与后端一致。

用法:
    python scripts/gen_api_types.py                 # 写入 frontend/src/api.gen.ts
    python scripts/gen_api_types.py --out path.ts   # 指定输出
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HEADER = """// 自动生成，请勿手改：python scripts/gen_api_types.py
// 来源：FastAPI OpenAPI schema（src/api/app.py）
/* eslint-disable */

"""


def _ts_type(schema: dict) -> str:
    if not isinstance(schema, dict):
        return "unknown"
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "anyOf" in schema or "oneOf" in schema:
        parts = [_ts_type(s) for s in (schema.get("anyOf") or schema.get("oneOf"))]
        uniq = list(dict.fromkeys(parts))
        return " | ".join(uniq) if uniq else "unknown"
    if "enum" in schema:
        return " | ".join(f"'{v}'" for v in schema["enum"])
    t = schema.get("type")
    if t == "array":
        return f"{_ts_type(schema.get('items', {}))}[]"
    if t == "object" or "properties" in schema:
        if "properties" in schema:
            props = "".join(
                f"\n  {k}{'?' if k not in (schema.get('required') or []) else ''}: {_ts_type(v)};"
                for k, v in schema["properties"].items()
            )
            return "{" + props + "\n}"
        return "Record<string, unknown>"
    return {"string": "string", "integer": "number", "number": "number",
            "boolean": "boolean", "null": "null"}.get(t, "unknown")


def generate(out: Path) -> int:
    from src.api.app import app
    spec = app.openapi()
    schemas = (spec.get("components") or {}).get("schemas") or {}
    lines = [HEADER]
    for name in sorted(schemas):
        sch = schemas[name]
        ts = _ts_type(sch)
        if ts.startswith("{"):
            lines.append(f"export interface {name} {ts}\n")
        else:
            lines.append(f"export type {name} = {ts}\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(schemas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1]
                                        / "frontend" / "src" / "api.gen.ts"))
    args = ap.parse_args()
    n = generate(Path(args.out))
    print(f"[OK] 已生成 {n} 个类型 -> {args.out}")


if __name__ == "__main__":
    main()
