"""审核与解决方案路由：Skill 配置、阈值调参、解决方案复核。

覆盖「配置读取 -> 在线调参 -> 方案审核」闭环，所有写操作捕获异常并转 400，
保证非法配置不会污染磁盘上的 Skill/阈值文件。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api import adapters, schemas

router = APIRouter(tags=["review"])


@router.get("/review/skills")
def review_skills():
    """列出全部 Skill 及其摘要状态。"""
    return {"skills": adapters.review_skills()}


@router.get("/review/skills/{skill_id}")
def review_skill(skill_id: str):
    """查看单个 Skill 的完整定义；不存在返回 404。"""
    d = adapters.review_skill(skill_id)
    if not d:
        raise HTTPException(404, f"未找到 Skill {skill_id}")
    return d


@router.get("/review/engine")
def review_engine():
    """返回规则引擎的当前参数与可用能力概览。"""
    return adapters.review_engine()


@router.put("/review/thresholds")
def update_thresholds(req: schemas.ThresholdCore):
    """在线更新核心阈值；校验失败转 400 并回传原因。"""
    try:
        return adapters.update_thresholds_core(req.model_dump(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        # 阈值非法（越界/类型错）不应落盘，统一按请求错误返回
        raise HTTPException(400, f"阈值更新失败：{exc}")


@router.put("/review/skills/{skill_id}")
def update_skill(skill_id: str, req: schemas.SkillUpdate):
    """保存 Skill 的 YAML 配置（仅更新显式传入字段）；失败转 400。"""
    try:
        return adapters.save_skill_yaml(skill_id, req.model_dump(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        # YAML 语法/Schema 校验失败时保持原文件不变
        raise HTTPException(400, f"Skill 更新失败：{exc}")


@router.get("/solutions")
def solutions(status: str | None = None):
    """列出解决方案库条目，可按审核状态过滤。"""
    return {"solutions": adapters.list_solutions(status)}


@router.post("/solutions/{solution_id}/review")
def review_solution(solution_id: str, status: str, note: str = ""):
    """对解决方案执行审核（通过/驳回）并记录备注。"""
    return adapters.review_solution(solution_id, status, note)


@router.delete("/solutions/{solution_id}")
def delete_solution(solution_id: str):
    """删除指定解决方案。"""
    return adapters.delete_solution(solution_id)
