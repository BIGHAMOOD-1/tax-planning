"""异步任务路由：启动分析任务与查询任务状态。

分析任务可能耗时较长，故采用「提交即返回 job_id + 轮询状态」的模式，
任务实体与状态机由 `services.jobs` 统一管理。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api import schemas
from src.api.services.jobs import get_jobs

router = APIRouter(tags=["jobs"])


@router.post("/analyses/run", response_model=schemas.JobStatus)
def run_analysis(req: schemas.RunRequest):
    """提交一次分析任务，立即返回任务状态（含 job_id 供后续轮询）。"""
    job = get_jobs().start(req.stock_code, req.year, req.use_llm, req.top_k, req.rounds, req.force)
    return job.to_dict()


@router.get("/jobs/{job_id}", response_model=schemas.JobStatus)
def job_status(job_id: str):
    """按 job_id 查询任务状态；不存在返回 404。"""
    j = get_jobs().get(job_id)
    # 任务可能因进程重启而丢失，故显式区分「不存在」与「未完成」
    if not j:
        raise HTTPException(404, f"未找到任务 {job_id}")
    return j.to_dict()
