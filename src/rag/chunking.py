"""知识库切分：把政策语料与案例语料切成带元数据的 chunk。

三类语料：
- 政策库：按段落累积切分，保留标题/文号/税种/效力等元数据；
- 案例库：仅切正文（剔除头部元信息），元数据来自 cases_index.csv；
- 文档库：版面感知切分（表格块整体保留），并携带企业/年份等元数据。
所有 chunk 统一带 corpus/chunk_id/text 三列，供检索器按语料库加载。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.common import CONFIG, get_logger

log = get_logger()


def split_text(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    """按段落边界累积切分，尽量保持语义完整。"""
    if not text:
        return []
    # 折叠多余空行，减少切分碎片
    text = re.sub(r"\n{3,}", "\n\n", str(text)).strip()
    if len(text) <= size:
        return [text]
    paras = [p for p in text.split("\n") if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        # 段落能塞进当前缓冲就累积，否则先落盘再处理超长段
        if len(buf) + len(p) + 1 <= size:
            buf = f"{buf}\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) > size:
                # 单段超长：按 size-overlap 步长硬切
                for i in range(0, len(p), size - overlap):
                    chunks.append(p[i:i + size])
                buf = ""
            else:
                buf = p
    if buf:
        chunks.append(buf)
    # 相邻 chunk 加一点重叠，避免边界信息丢失
    if overlap > 0 and len(chunks) > 1:
        merged = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:]):
            merged.append(prev[-overlap:] + "\n" + cur)
        chunks = merged
    return chunks


def build_policy_chunks() -> pd.DataFrame:
    """把政策语料切分为 chunk，并保留标题/文号/税种/效力等检索元数据。"""
    cfg = CONFIG["rag"]
    src = Path(cfg["policy_dir"]) / "policy_corpus.parquet"
    df = pd.read_parquet(src)
    rows = []
    for i, r in df.iterrows():
        for j, ch in enumerate(split_text(r["content"], cfg["chunk_size"], cfg["chunk_overlap"])):
            rows.append({
                "corpus": "policy",
                "chunk_id": f"policy-{i}-{j}",
                "text": ch,
                "title": r.get("title", ""),
                "doc_no": r.get("doc_no", ""),
                "channel": r.get("channel", ""),
                "tax_type": r.get("tax_type", ""),
                "effect_level": r.get("effect_level", ""),
                "department": r.get("issuing_department", ""),
                "date": str(r.get("written_date", "")),
                "url": r.get("url", ""),
                "source": r.get("source", "国家税务总局政策法规库"),
            })
    out = pd.DataFrame(rows)
    log.info("政策库切分: %s 条文档 -> %s 个 chunk", len(df), len(out))
    return out


def build_case_chunks() -> pd.DataFrame:
    """把案例 markdown 切分为 chunk（仅正文，剔除头部元信息）。"""
    cfg = CONFIG["rag"]
    base = Path(cfg["cases_dir"])
    idx_path = base / "cases_index.csv"
    idx = pd.read_csv(idx_path)
    rows = []
    for _, meta in idx.iterrows():
        p = base / meta["path"]
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        # 去掉头部元信息，只切正文
        body = text.split("## 正文", 1)[-1] if "## 正文" in text else text
        for j, ch in enumerate(split_text(body, cfg["chunk_size"], cfg["chunk_overlap"])):
            rows.append({
                "corpus": "case",
                "chunk_id": f"case-{meta['file_name']}-{j}",
                "text": ch,
                "title": meta.get("title", ""),
                "doc_no": "",
                "channel": meta.get("category", ""),
                "tax_type": "",
                "effect_level": "",
                "department": meta.get("source", ""),
                "date": str(meta.get("date", "")),
                "url": "",
                "source": "税屋案例库",
            })
    out = pd.DataFrame(rows)
    log.info("案例库切分: %s 篇 -> %s 个 chunk", len(idx), len(out))
    return out


def _is_table_block(lines: list[str]) -> bool:
    """粗判表格块：多列对齐（多空格/制表符/竖线）或大量数字。"""
    if not lines:
        return False
    joined = "\n".join(lines)
    multi_col = sum(1 for ln in lines if re.search(r"\S\s{2,}\S", ln) or "|" in ln or "\t" in ln)
    numeric = len(re.findall(r"\d", joined))
    return multi_col >= max(1, len(lines) // 2) or (len(lines) >= 2 and numeric >= 8 * len(lines))


def chunk_document(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    """版面感知切分：表格块整体保留，正文按段落累积。

    避免把表头与表体切开（跨页表格仍可能被拆，属已知局限）。
    """
    if not text:
        return []
    raw_lines = [ln.rstrip() for ln in str(text).splitlines()]
    # 归并为 blocks：连续表格行 -> 一个 block；其余按段落
    blocks: list[str] = []
    buf: list[str] = []

    def flush():
        nonlocal buf
        if buf:
            blocks.append("\n".join(buf).strip())
            buf = []

    for ln in raw_lines:
        # 判断该行是否像表格行（多列对齐/竖线/制表符）
        table_like = bool(re.search(r"\S\s{2,}\S", ln) or "|" in ln or "\t" in ln)
        if table_like:
            buf.append(ln)
            continue
        # 非表格行到来：先结算缓冲的表格块，再把本行作为独立段落
        if _is_table_block(buf):
            flush()
        else:
            flush()
        if ln.strip():
            blocks.append(ln.strip())
    flush()

    chunks, cur = [], ""
    # 按 block 累积到 size；单个 block 超长时硬切
    for b in blocks:
        if not b:
            continue
        if len(cur) + len(b) + 1 <= size:
            cur = f"{cur}\n{b}" if cur else b
        else:
            if cur:
                chunks.append(cur)
            if len(b) > size:
                for i in range(0, len(b), size - overlap):
                    chunks.append(b[i:i + size])
                cur = ""
            else:
                cur = b
    if cur:
        chunks.append(cur)
    return chunks


def build_document_chunks() -> pd.DataFrame:
    """从 documents_index 构建文档语料 chunk（含企业/年份/文档元数据）。"""
    cfg = CONFIG["rag"]
    # documents_dir 未配置时，默认取 index_dir 同级的 documents 目录
    base = Path(cfg.get("documents_dir", Path(cfg["index_dir"]).parent / "documents"))
    idx_path = base / "documents_index.parquet"
    # 无用户文档时返回带完整列的空表，保证下游 concat/写入不报错
    if not idx_path.exists():
        return pd.DataFrame(columns=["corpus", "chunk_id", "text", "stock_code", "report_year",
                                     "doc_type", "document_id", "document_date", "title",
                                     "channel", "date", "source"])
    idx = pd.read_parquet(idx_path)
    rows = []
    for _, d in idx.iterrows():
        p = base / f"{d['document_id']}.txt"
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for j, ch in enumerate(chunk_document(text, cfg["chunk_size"], cfg["chunk_overlap"])):
            rows.append({
                "corpus": "document",
                "chunk_id": f"doc-{d['document_id']}-{j}",
                "text": ch,
                # 企业代码统一补零到 6 位，保证与画像主键可关联
                "stock_code": str(d.get("stock_code", "")).zfill(6),
                "report_year": d.get("report_year"),
                "doc_type": d.get("doc_type", ""),
                "document_id": d.get("document_id", ""),
                "document_date": str(d.get("document_date", "")),
                "title": d.get("title", ""),
                "channel": d.get("doc_type", ""),
                "date": str(d.get("document_date", "")),
                "source": "用户文档",
            })
    out = pd.DataFrame(rows)
    log.info("文档库切分: %s 篇 -> %s 个 chunk", len(idx), len(out))
    return out


def build_all() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """构建政策/案例/文档三套 chunk 并落盘，返回三元组。"""
    policy = build_policy_chunks()
    case = build_case_chunks()
    doc = build_document_chunks()
    out_dir = Path(CONFIG["rag"]["index_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    # 三套索引分别落盘，供 KB 按语料库加载
    policy.to_parquet(out_dir / "policy_chunks.parquet", index=False)
    case.to_parquet(out_dir / "case_chunks.parquet", index=False)
    doc.to_parquet(out_dir / "document_chunks.parquet", index=False)
    log.info("chunk 落盘 -> %s", out_dir)
    return policy, case, doc


if __name__ == "__main__":
    build_all()
