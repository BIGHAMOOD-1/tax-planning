"""用户文档存储：把文本数据（年报/演讲稿/股东会记录…）落盘，供 Document RAG 使用。

不强制转 Profile：文档先入语料库，按需经 LLM 抽取为候选 Evidence。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.common import CONFIG, get_logger

log = get_logger()

INDEX_NAME = "documents_index.parquet"


def documents_dir() -> Path:
    """返回文档目录（默认 index_dir 同级 documents），不存在则创建。"""
    base = CONFIG["rag"].get("documents_dir") or (Path(CONFIG["rag"]["index_dir"]).parent / "documents")
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _index_path() -> Path:
    """文档索引 parquet 路径。"""
    return documents_dir() / INDEX_NAME


def list_documents(stock_code: str | None = None, doc_type: str | None = None) -> list[dict]:
    """列出文档元数据，可按股票代码与文档类型过滤。"""
    p = _index_path()
    if not p.exists():
        return []
    df = pd.read_parquet(p)
    # 代码统一补零到 6 位后比较，避免 "1" 与 "000001" 不匹配
    if stock_code:
        df = df[df["stock_code"].astype(str).str.zfill(6) == str(stock_code).zfill(6)]
    if doc_type:
        df = df[df["doc_type"] == doc_type]
    return df.to_dict("records")


def save_document(text: str, stock_code: str, doc_type: str = "other", title: str = "",
                  document_date: str = "", report_year: int | None = None,
                  document_id: str | None = None) -> dict:
    """保存一篇文档（正文 + 元数据），返回元数据记录。"""
    text = str(text or "")
    if not text.strip():
        raise ValueError("文档正文为空")
    if not document_id:
        # 以"代码 + 正文前 200 字"的 MD5 前 8 位 + 时间戳生成可读且近唯一的 ID
        h = hashlib.md5((stock_code + text[:200]).encode("utf-8")).hexdigest()[:8]
        document_id = f"DOC{datetime.now().strftime('%Y%m%d%H%M%S')}{h}"
    base = documents_dir()
    # 正文单独落 .txt，元数据入索引，正文与索引分离便于增量更新
    (base / f"{document_id}.txt").write_text(text, encoding="utf-8")
    rec = {
        "document_id": document_id,
        "stock_code": str(stock_code).zfill(6) if stock_code else "",
        "doc_type": doc_type,
        "title": title,
        "document_date": str(document_date or ""),
        "report_year": int(report_year) if report_year is not None else None,
        "chars": len(text),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    idx = pd.read_parquet(_index_path()) if _index_path().exists() else pd.DataFrame()
    idx = pd.concat([idx, pd.DataFrame([rec])], ignore_index=True)
    idx.to_parquet(_index_path(), index=False)
    log.info("文档已保存：%s（%s/%s, %s 字）", document_id, rec["stock_code"], doc_type, rec["chars"])
    return rec
