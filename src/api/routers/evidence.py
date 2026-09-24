"""证据路由：待审证据、冲突检测与人工复核/冲突解决。

证据存在「机器抽取 -> 人工确认 -> 冲突消解」的生命周期，
本模块暴露各阶段的读写入口，冲突解决遵循显式选择（证据优先/画像优先）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api import adapters, schemas

router = APIRouter(tags=["evidence"])


@router.get("/evidence/pending", response_model=schemas.EvidenceList)
def pending():
    """列出待人工审核的证据条目。"""
    return {"items": adapters.evidence_pending()}


@router.get("/evidence/conflicts")
def conflicts(code: str, year: int):
    """列出指定企业/年度的证据与画像冲突项。"""
    return {"items": adapters.evidence_conflicts(code, year)}


@router.post("/evidence/{evidence_id}/review")
def review(evidence_id: str, action: str, value: str | None = None):
    """对单条证据执行审核动作（如采纳/修正/驳回）。"""
    return {"status": adapters.evidence_review(evidence_id, action, value)}


@router.post("/evidence/{evidence_id}/resolve")
def resolve(evidence_id: str, choice: str):
    """冲突解决：choice=evidence（以证据为准）| profile（保留画像）。"""
    try:
        return {"result": adapters.evidence_resolve(evidence_id, choice)}
    except ValueError as exc:
        # choice 取值非法属于客户端参数错误，转为 400
        raise HTTPException(400, str(exc)) from exc
