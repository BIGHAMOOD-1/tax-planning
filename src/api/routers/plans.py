"""方案路由：按「企业/年度/方向」读取方案、导出证据链与税务意见。

每个方向产出一份 7 节点证据链方案；导出统一采用
`Content-Disposition: filename*=UTF-8''` 编码中文文件名以兼容浏览器。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from src.api import adapters

router = APIRouter(tags=["plans"])


@router.get("/analyses/{code}/{year}/plans/{direction}")
def get_plan(code: str, year: int, direction: str):
    """读取指定方向的方案详情；不存在返回 404。"""
    d = adapters.load_plan(code, year, direction)
    if not d:
        raise HTTPException(404, f"未找到方向 {direction} 的方案")
    return d


@router.get("/analyses/{code}/{year}/plans/{direction}/export")
def export_plan(code: str, year: int, direction: str):
    """导出单个方向的 7 节点证据链报告（Markdown）。"""
    d = adapters.load_plan(code, year, direction)
    if not d:
        raise HTTPException(404, f"未找到方向 {direction} 的方案")
    from src.report.export import render_chain_markdown
    md = render_chain_markdown(d)
    # 文件名前缀补零到 6 位，保证股票代码排序稳定；中文名做 URL 编码
    fname = f"{str(code).zfill(6)}_{year}_{direction}_证据链.md"
    return Response(
        content=md, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/analyses/{code}/{year}/plans/{direction}/export.pdf")
def export_plan_pdf(code: str, year: int, direction: str):
    """导出单个方向的 7 节点证据链报告（PDF）。"""
    d = adapters.load_plan(code, year, direction)
    if not d:
        raise HTTPException(404, f"未找到方向 {direction} 的方案")
    from src.report.pdf import pdf_from_chain
    data = pdf_from_chain(d)
    fname = f"{str(code).zfill(6)}_{year}_{direction}_证据链.pdf"
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.post("/analyses/{code}/{year}/plans/{direction}/opinion")
def plan_opinion(code: str, year: int, direction: str, refresh: bool = False):
    """合规税务意见：生成/获取（refresh=true 强制重生成）。"""
    op = adapters.plan_opinion(code, year, direction, refresh=refresh)
    if op is None:
        raise HTTPException(404, f"未找到方向 {direction} 的方案")
    return op


@router.get("/analyses/{code}/{year}/plans/{direction}/opinion.md")
def export_opinion(code: str, year: int, direction: str):
    """下载该方向的合规税务意见书（Markdown）。"""
    d = adapters.load_plan(code, year, direction)
    if not d:
        raise HTTPException(404, f"未找到方向 {direction} 的方案")
    # 优先复用方案内已生成的意见，缺失时才即时生成，避免重复调用 LLM
    op = d.get("opinion") or adapters.plan_opinion(code, year, direction)
    if not op:
        raise HTTPException(404, f"暂无法生成 {direction} 的意见")
    from src.report.export import render_opinion_markdown
    md = render_opinion_markdown(d, op)
    fname = f"{str(code).zfill(6)}_{year}_{direction}_税务意见.md"
    return Response(
        content=md, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/analyses/{code}/{year}/plans/{direction}/opinion.pdf")
def export_opinion_pdf(code: str, year: int, direction: str):
    """下载该方向的合规税务意见书（PDF）。"""
    d = adapters.load_plan(code, year, direction)
    if not d:
        raise HTTPException(404, f"未找到方向 {direction} 的方案")
    # 同 Markdown 导出口径：已有意见直接复用，否则即时生成
    op = d.get("opinion") or adapters.plan_opinion(code, year, direction)
    if not op:
        raise HTTPException(404, f"暂无法生成 {direction} 的意见")
    from src.report.pdf import pdf_from_opinion
    data = pdf_from_opinion(d, op)
    fname = f"{str(code).zfill(6)}_{year}_{direction}_税务意见.pdf"
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )
