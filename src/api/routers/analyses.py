"""分析结果路由：结果读取、报告/指标导出。

结果以「企业代码 + 年度」为主键；报告支持 Markdown 与 PDF 两种交付形态，
其中 PDF 为竞赛要求的结构化交付物。
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from src.api import adapters, schemas

router = APIRouter(tags=["analyses"])


@router.get("/analyses", response_model=schemas.AnalysisList)
def list_analyses():
    """列出已有分析结果（企业/年度）。"""
    return {"analyses": adapters.list_analyses()}


@router.get("/analyses/{code}/{year}", response_model=schemas.ResultView)
def get_result(code: str, year: int):
    """读取指定企业/年度的分析结果；不存在返回 404。"""
    d = adapters.load_result(code, year)
    if not d:
        raise HTTPException(404, f"未找到分析结果 {code}/{year}")
    return d


@router.get("/analyses/{code}/{year}/report")
def get_report(code: str, year: int):
    """下载完整报告（Markdown）。"""
    from src.report.export import load_report_markdown
    md = load_report_markdown(code, year)
    if md is None:
        raise HTTPException(404, f"未找到报告 {code}/{year}")
    # 中文文件名按 RFC 5987 编码，避免部分浏览器下载乱码
    fname = f"{str(code).zfill(6)}_{year}_税务筹划报告.md"
    return Response(
        content=md, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/analyses/{code}/{year}/report.pdf")
def get_report_pdf(code: str, year: int):
    """下载完整报告（PDF，竞赛要求的结构化交付物）。"""
    from src.report.pdf import pdf_from_report
    data = pdf_from_report(code, year)
    if data is None:
        raise HTTPException(404, f"未找到报告 {code}/{year}")
    fname = f"{str(code).zfill(6)}_{year}_税务筹划报告.pdf"
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/analyses/{code}/{year}/metrics")
def get_metrics(code: str, year: int):
    """运行指标（Token / 耗时 / 阶段）。"""
    m = adapters.analysis_metrics(code, year)
    if m is None:
        raise HTTPException(404, f"暂无运行指标 {code}/{year}")
    return m
