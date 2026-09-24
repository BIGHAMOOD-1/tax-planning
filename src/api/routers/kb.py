"""知识库路由：检索、索引概况与后台重建。

知识库分为政策库与案例库两套索引；本模块提供统一检索入口，
并暴露索引后端/规模/Embedding 指纹等诊断信息，便于排查检索异常。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from src.api import adapters, schemas
from src.common import CONFIG

router = APIRouter(tags=["kb"])


@router.get("/kb/search", response_model=schemas.KBResult)
def kb_search(q: str, corpus: str = "policy", top_k: int = 5):
    """在指定语料库（policy/case）中检索，返回 top_k 命中。"""
    return {"corpus": corpus, "hits": adapters.kb_search(q, corpus, top_k)}


@router.get("/knowledge/index")
def knowledge_index():
    """当前索引概况（后端 / 规模 / Embedding 指纹）。"""
    from src.rag.store import get_kb
    out: dict = {}
    try:
        kb = get_kb()
        # 分别统计政策库与案例库：后端类型、分块数、Embedding 指纹是否失配
        out["policy"] = {"backend": kb.policy.backend, "chunks": int(len(kb.policy.chunks)),
                         "mismatch": kb.policy.fingerprint_mismatch}
        out["case"] = {"backend": kb.case.backend, "chunks": int(len(kb.case.chunks))}
    except Exception as exc:  # noqa: BLE001
        # 索引未构建/加载失败时不让整个接口 500，只回传截断后的错误摘要
        out["error"] = str(exc)[:160]
    try:
        # meta.json 记录构建时的 Embedding 指纹，用于判断是否需要重建索引
        meta = json.loads((Path(CONFIG["rag"]["index_dir"]) / "policy" / "meta.json")
                          .read_text(encoding="utf-8"))
        out["embedding"] = meta.get("embedding")
    except Exception:  # noqa: BLE001
        out["embedding"] = None
    return out


@router.post("/knowledge/rebuild")
def knowledge_rebuild(backend: str = "auto"):
    """重建知识库索引（后台任务）。backend ∈ auto/local/embedding。"""
    from src.api.services.jobs import get_jobs
    # 重建耗时较长，交给后台任务异步执行，立即返回 job 供轮询
    job = get_jobs().start_knowledge_rebuild(backend)
    return job.to_dict()
