"""数据录入路由：文本/文件候选事实预览与批量提交入库。

流程约定为「先预览后提交」：预览接口解析出候选事实（不落库），
由用户在前端勾选确认后再调用 commit 写入，保证人工可干预、可追溯。
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.api import adapters, schemas

router = APIRouter(tags=["ingest"])


@router.get("/fact-types")
def fact_types():
    """返回系统支持的财务事实类型清单（供前端下拉选择）。"""
    return {"fact_types": adapters.fact_types()}


@router.post("/ingest/text")
def ingest_text(req: schemas.TextIngest):
    """预览粘贴文本解析出的候选事实（不写库）。"""
    return adapters.preview_text(req.stock_code, req.year, req.text, req.importance,
                                 req.fact_type, req.unit, req.caliber)


@router.post("/ingest/file")
async def ingest_file(file: UploadFile = File(...), stock_code: str = Form(...),
                      year: int = Form(...), importance: str = Form("reference")):
    """上传文件并预览候选事实；解析失败统一转 400 并回传原因。"""
    try:
        content = await file.read()
        return adapters.preview_file(file.filename or "upload", content, stock_code, year, importance)
    except Exception as exc:  # noqa: BLE001
        # 文件格式/编码/解析等异常对客户端都是「请求不可处理」，统一按 400 返回
        raise HTTPException(400, f"解析失败：{exc}")


@router.post("/ingest/commit")
def ingest_commit(req: schemas.IngestCommit):
    """提交用户确认后的候选事实，返回新写入的记录 ID 列表。"""
    ids = adapters.commit_candidates(req.items)
    return {"ids": ids, "count": len(ids)}
