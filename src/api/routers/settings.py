"""设置（Update 6.1）：前端可配置对话模型 / 向量服务，并触发知识库重建。

安全约定：
- `GET` **不回显明文密钥**，只给 `source`（settings/env/none）与 masked；
- `POST` **只写用户显式填写的字段**；空 api_key 不改动（**绝不把环境变量里的 Key 落盘**）。
"""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter

from src.api import schemas
from src.common import CONFIG, get_logger, load_settings, save_settings

log = get_logger()
router = APIRouter(tags=["settings"])

# 对话模型 Provider 预设（**全部 OpenAI 兼容**）
PROVIDERS: dict[str, dict] = {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "models": ["deepseek-chat", "deepseek-reasoner"],
                 "note": "chat=快速版(V3)，reasoner=增强版(R1)"},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
               "models": ["gpt-4o-mini", "gpt-4o"]},
    "qwen": {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "models": ["qwen-plus", "qwen-max"]},
    "claude": {"label": "Claude（OpenAI 兼容网关）", "base_url": "",
               "models": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
               "note": "Anthropic 官方非 OpenAI 兼容；请填兼容网关 base_url"},
    "siliconflow": {"label": "SiliconFlow", "base_url": "https://api.siliconflow.cn/v1",
                    "models": ["deepseek-ai/DeepSeek-V3"]},
}

# 向量 / 重排 Provider 预设
EMB_PROVIDERS: dict[str, dict] = {
    "siliconflow": {"label": "SiliconFlow（bge-m3）", "base_url": "https://api.siliconflow.cn/v1",
                    "models": ["BAAI/bge-m3"], "rerank_models": ["BAAI/bge-reranker-v2-m3"]},
    "dashscope": {"label": "通义 DashScope", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "models": ["text-embedding-v3"], "rerank_models": ["gte-rerank"]},
}


def _mask(v: str) -> str:
    """对密钥做脱敏展示：足够长则保留首尾各 4 位，否则只提示「已配置」。

    绝不返回明文，避免 GET 接口泄露密钥。
    """
    v = str(v or "")
    if not v:
        return ""
    # 短密钥首尾重叠会泄露全部字符，故只给存在性提示
    return v[:4] + "…" + v[-4:] if len(v) > 12 else "已配置"


def _key_source(st_key: str | None, env_names: list[str]) -> str:
    """判断密钥来源优先级：settings 显式配置 > 环境变量 > 未配置。"""
    if st_key:
        return "settings"
    for n in env_names:
        if os.environ.get(n):
            return "env"
    return "none"


def _embed_key_env() -> str:
    """返回向量服务密钥所用的环境变量名（可在配置中自定义）。"""
    return (CONFIG.get("embedding", {}) or {}).get("api_key_env", "EMBEDDING_API_KEY")


@router.get("/settings")
def get_settings():
    """读取当前对话/向量设置，密钥仅返回来源与掩码，不回显明文。"""
    from src.llm.client import get_llm
    st = load_settings()
    st_chat = st.get("chat") or {}
    st_emb = st.get("embedding") or {}
    llm = get_llm()
    return {
        "chat": {
            "provider": st_chat.get("provider") or CONFIG.get("llm", {}).get("provider", ""),
            "base_url": llm.base_url,
            "model": llm.model,
            "api_key": {"source": _key_source(st_chat.get("api_key"), ["OPENAI_API_KEY"]),
                        "masked": _mask(llm.api_key)},
        },
        "embedding": {
            "base_url": llm.embed_base_url,
            "model": llm.embedding_model,
            "rerank_model": llm.rerank_model,
            "api_key": {"source": _key_source(st_emb.get("api_key"),
                                              ["OPENAI_EMBEDDING_API_KEY", _embed_key_env()]),
                        "masked": _mask(llm.embed_api_key)},
        },
        "llm": {
            "cache": bool((st.get("llm", {}) or {}).get("cache")
                          or CONFIG.get("llm", {}).get("cache", False)),
        },
        "providers": PROVIDERS,
        "embedding_providers": EMB_PROVIDERS,
    }


@router.post("/settings")
def update_settings(payload: schemas.SettingsUpdate):
    """保存用户显式填写的设置，并热重载 LLM 客户端使配置即时生效。"""
    data = payload.model_dump(exclude_none=True)
    st = load_settings()
    # 逐字段合并：只覆盖非空字段，未填字段保留原值
    for section in ("chat", "embedding"):
        inc = data.get(section) or {}
        cur = st.setdefault(section, {})
        for k in ("provider", "base_url", "model", "rerank_model"):
            v = inc.get(k)
            if v is not None and str(v).strip() != "":
                cur[k] = str(v).strip()
        # api_key：仅当显式提供非空才写入；空串表示"保持不变"（不落盘 env 值）
        if inc.get("api_key"):
            cur["api_key"] = str(inc["api_key"]).strip()
    llm_inc = data.get("llm") or {}
    if "cache" in llm_inc:
        st.setdefault("llm", {})["cache"] = bool(llm_inc["cache"])
    st["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_settings(st)
    # 重载客户端，让新 provider/model/key 无需重启进程即可生效
    from src.llm.client import reload_llm
    llm = reload_llm()
    log.info("设置已保存：chat=%s / model=%s / embedding=%s",
             llm.provider, llm.model, llm.embedding_model)
    return {"ok": True, "chat_model": llm.model, "embedding_model": llm.embedding_model}


@router.post("/settings/test")
def test_settings():
    """连通性自检：分别探测对话、向量、重排服务是否可用。"""
    from src.llm.client import get_llm
    llm = get_llm()
    out: dict = {}
    try:
        out["chat"] = bool(llm.probe())
    except Exception as exc:  # noqa: BLE001
        # 单点失败不影响其他探测，记录截断后的错误信息供前端展示
        out["chat"] = False
        out["chat_error"] = str(exc)[:160]
    try:
        out["embedding"] = bool(llm.try_embed(["ping"]))
    except Exception as exc:  # noqa: BLE001
        out["embedding"] = False
        out["embedding_error"] = str(exc)[:160]
    try:
        out["rerank"] = bool(llm.try_rerank("税务", ["研发费用加计扣除", "高新技术企业"], top_n=1))
    except Exception as exc:  # noqa: BLE001
        out["rerank"] = False
        out["rerank_error"] = str(exc)[:160]
    return out
