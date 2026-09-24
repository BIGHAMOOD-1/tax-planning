"""下载并保存国家税务总局政策法规库语料（HuggingFace: salpt/chinatax-policy-corpus）。

该数据集为从国家税务总局政策法规库爬取的税收法律法规、政策解读、总局公告、
规范性文件全文，共 5,593 条。

字段：title, channel, content, document_number, effect_level,
      tax_type, aging, labels, issuing_department, written_date, url

用法：
    python src/rag/ingest_policy_corpus.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.common import CONFIG, get_logger

log = get_logger()

REPO = "salpt/chinatax-policy-corpus"
# 统一为知识库内部字段
COLUMN_MAP = {
    "title": "title",
    "channel": "channel",
    "content": "content",
    "document_number": "doc_no",
    "effect_level": "effect_level",
    "tax_type": "tax_type",
    "aging": "aging",
    "labels": "labels",
    "issuing_department": "issuing_department",
    "written_date": "written_date",
    "url": "url",
}


def ingest(out_dir: str | Path | None = None) -> Path:
    import pandas as pd
    from datasets import load_dataset

    out_dir = Path(out_dir or Path(CONFIG["paths"]["knowledge"]) / "policy_corpus")
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("加载 HuggingFace 数据集 %s ...", REPO)
    ds = load_dataset(REPO)
    split = ds["train"]
    df = split.to_pandas()
    log.info("原始 %s 行，字段: %s", len(df), list(df.columns))

    df = df.rename(columns=COLUMN_MAP)
    df["source"] = "国家税务总局政策法规库"
    df["corpus"] = "policy"

    parquet_path = out_dir / "policy_corpus.parquet"
    jsonl_path = out_dir / "policy_corpus.jsonl"
    df.to_parquet(parquet_path, index=False)
    df.to_json(jsonl_path, orient="records", lines=True, force_ascii=False)
    log.info("已保存 %s", parquet_path)
    log.info("已保存 %s", jsonl_path)
    return parquet_path


if __name__ == "__main__":
    ingest()
