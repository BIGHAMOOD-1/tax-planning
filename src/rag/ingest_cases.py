"""解包案例知识库（案例1.0.zip）：税屋 markdown + 实例讲解书籍。

用法：
    python src/rag/ingest_cases.py
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common import CONFIG, get_logger

log = get_logger()
ZIP = Path(CONFIG["paths"]["raw_cases"]) / "案例1.0.zip"
OUT = Path(CONFIG["paths"]["knowledge"]) / "cases"

META_RE = {
    "source": re.compile(r"-\s*来源:\s*(.+)"),
    "date": re.compile(r"-\s*日期:\s*(.+)"),
    "category": re.compile(r"-\s*栏目:\s*(.+)"),
    "title": re.compile(r"^#\s+(.+)"),
}


def parse_meta(text: str) -> dict:
    """从 markdown 头部（前 15 行）解析来源/日期/栏目/标题等元信息。

    同一字段只取首次命中，避免正文中出现相似行造成覆盖。
    """
    meta = {"title": "", "source": "", "date": "", "category": ""}
    for line in text.splitlines()[:15]:
        for key, rx in META_RE.items():
            m = rx.search(line.strip())
            if m and not meta[key]:
                meta[key] = m.group(1).strip()
    return meta


def ingest() -> Path:
    """解包案例压缩包到知识库目录，并为 markdown 生成索引 CSV，返回索引路径。"""
    import pandas as pd

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    with zipfile.ZipFile(ZIP) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            target = OUT / name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = z.read(name)
            target.write_bytes(data)
            # 仅对 markdown 建索引（书籍/其它格式不入索引）
            if name.lower().endswith(".md"):
                text = data.decode("utf-8", "ignore")
                meta = parse_meta(text)
                rows.append({
                    "path": name,
                    "file_name": Path(name).name,
                    "title": meta["title"],
                    "source": meta["source"],
                    "date": meta["date"],
                    "category": meta["category"],
                    "chars": len(text),
                })

    idx = pd.DataFrame(rows)
    out_csv = OUT / "cases_index.csv"
    idx.to_csv(out_csv, index=False, encoding="utf-8-sig")
    log.info("解包 %s 个文件，其中 markdown %s 篇", len(list(OUT.rglob("*.*"))), len(idx))
    log.info("案例索引 -> %s", out_csv)
    if not idx.empty:
        log.info("栏目分布: %s", idx["category"].value_counts().to_dict())
    return out_csv


if __name__ == "__main__":
    ingest()
