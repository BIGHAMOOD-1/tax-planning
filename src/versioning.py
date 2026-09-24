"""版本指纹（Update 5.1 · 工程护栏）。

把**数据 / 模型 / Skill / 配置**的版本信息固化为可追溯指纹，
写入每次运行的 `run_meta.json`，并可通过 `/api/meta/version` 查询。

目的：任何一次分析结果都能回答"当时用的是哪份数据、哪个模型、哪版 Skill、哪份配置"。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from src.common import CONFIG, ROOT, get_logger

log = get_logger()


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _file_fp(p: Path) -> dict | None:
    if not p.exists():
        return None
    st = p.stat()
    return {
        "path": _rel(p),
        "size": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def data_fingerprint() -> dict:
    """数据层指纹：主表 / 画像 / 衍生指标的落盘信息。"""
    proc = Path(CONFIG["paths"]["data_processed"])
    out = {}
    for key, rel in (("master", "master.parquet"),
                     ("profile_features", "features/profile_features.parquet"),
                     ("derived_metrics", "derived/derived_metrics.parquet")):
        fp = _file_fp(proc / rel)
        if fp:
            out[key] = fp
    return out


def skills_fingerprint() -> dict:
    """Skill 集合指纹：数量 + 各 Skill 版本 + 汇总哈希。"""
    try:
        from src.skills.engine import get_skills
        skills = get_skills()
    except Exception as exc:  # noqa: BLE001
        log.warning("Skill 指纹失败：%s", str(exc)[:120])
        return {"count": 0, "hash": None, "items": []}
    skill_list = list(skills.values()) if isinstance(skills, dict) else list(skills)
    items = []
    for s in skill_list:
        items.append({
            "id": getattr(s, "id", None),
            "version": getattr(s, "version", None),
            "effective_from": getattr(s, "effective_from", None),
            "effective_to": getattr(s, "effective_to", None),
        })
    items.sort(key=lambda x: str(x.get("id")))
    h = hashlib.md5(
        "|".join(f"{i['id']}@{i['version']}" for i in items).encode("utf-8")
    ).hexdigest()[:12]
    return {"count": len(items), "hash": h, "items": items}


def model_fingerprint() -> dict:
    """模型指纹：对话模型 + Embedding/Rerank 指纹。"""
    out: dict = {"chat_model": CONFIG.get("llm", {}).get("model")}
    try:
        from src.llm.client import get_llm
        llm = get_llm()
        out["chat_model"] = llm.model
        out["embedding"] = {
            "model": getattr(llm, "embedding_model", ""),
            "base_url": getattr(llm, "embed_base_url", ""),
            "rerank_model": getattr(llm, "rerank_model", ""),
        }
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:120]
    return out


def config_fingerprint() -> dict:
    """配置指纹：config.yaml / thresholds.yaml 的内容哈希。"""
    out = {}
    for name in ("config.yaml", "thresholds.yaml", "fact_resolution.yaml"):
        p = ROOT / "config" / name
        if not p.exists():
            continue
        out[name] = {
            "hash": hashlib.md5(p.read_bytes()).hexdigest()[:12],
            **_file_fp(p),
        }
    return out


def build_fingerprint() -> dict:
    """完整版本指纹。"""
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": data_fingerprint(),
        "skills": skills_fingerprint(),
        "models": model_fingerprint(),
        "config": config_fingerprint(),
    }
