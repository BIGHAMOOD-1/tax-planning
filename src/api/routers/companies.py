"""企业信息查询路由：列表检索与企业详情。

本模块仅做 HTTP 参数/响应的编排，具体数据读取与装配全部委托给 `adapters`，
以保持路由层薄、便于测试与替换数据源。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api import adapters, schemas

router = APIRouter(tags=["companies"])


@router.get("/companies", response_model=schemas.CompanyList)
def list_companies(q: str | None = None, limit: int = 50):
    """企业列表查询。

    - q：可选关键字（代码/名称模糊匹配），为空则返回全部；
    - limit：返回条数上限，默认 50，避免一次拉取过多。
    """
    return {"companies": adapters.list_companies(q, limit)}


@router.get("/companies/{code}", response_model=schemas.CompanyDetail)
def company_detail(code: str):
    """按股票代码查询企业详情；不存在时返回 404。"""
    d = adapters.company_detail(code)
    # 数据源未命中：显式转成 404，避免前端拿到空对象误判为成功
    if not d:
        raise HTTPException(404, f"未找到企业 {code}")
    return d
