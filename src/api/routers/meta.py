"""元信息路由：运行统计、版本指纹与字段标签。

版本指纹用于复现性核对（同一输入应得到同一指纹），字段标签供前端展示中文列名。
"""
from __future__ import annotations

from fastapi import APIRouter

from src.api import adapters, schemas

router = APIRouter(tags=["meta"])


@router.get("/meta/stats", response_model=schemas.MetaStats)
def meta_stats():
    """返回企业数、分析数、证据数等汇总统计。"""
    return adapters.meta_stats()


@router.get("/meta/version")
def meta_version():
    """返回构建指纹（配置/数据/Skill 等摘要），用于结果可复现性校验。"""
    from src.versioning import build_fingerprint
    return build_fingerprint()


@router.get("/field-labels")
def field_labels():
    """返回字段英文名到中文标签的映射。"""
    return adapters.field_labels()
