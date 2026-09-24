"""OpenAI 兼容大模型客户端（LLM + Embedding）。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from src.common import CONFIG, get_logger, load_settings

log = get_logger()

_CACHE_DIR = Path(CONFIG["paths"]["outputs"]) / "_llm_cache"


class LLMUnavailable(RuntimeError):
    """未配置 API Key 时抛出，供上层走规则兜底。"""


def _extract_json(text: str) -> Any:
    """从模型输出里抠出 JSON（兼容 ```json 包裹与前后废话）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"无法解析 JSON: {text[:200]}")


class LLMClient:
    """OpenAI 兼容客户端：对话 / 结构化 JSON / Embedding / Rerank。

    配置优先级：settings.json（UI 设置）> 环境变量 > config.yaml；
    所有对外调用都有 try_* 容错版本，失败返回 None 以便上层走规则兜底。
    """

    def __init__(self, config: dict | None = None):
        cfg = (config or CONFIG)
        llm_cfg = cfg.get("llm", {})
        emb_cfg = cfg.get("embedding", {})
        # UI 设置覆盖层（config/settings.json）：仅含用户显式填写项
        st = load_settings()
        st_chat = st.get("chat") or {}
        st_emb = st.get("embedding") or {}

        # 优先级：settings.json > 环境变量 > config.yaml
        self.base_url = (st_chat.get("base_url")
                         or os.environ.get("OPENAI_BASE_URL")
                         or llm_cfg.get("base_url", "")).rstrip("/")
        self.api_key = st_chat.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        self.model = (st_chat.get("model")
                      or os.environ.get("OPENAI_MODEL")
                      or llm_cfg.get("model", ""))
        self.provider = st_chat.get("provider") or llm_cfg.get("provider", "")
        self.temperature = llm_cfg.get("temperature", 0.2)
        self.timeout = llm_cfg.get("timeout", 120)

        # Embedding 可独立于对话模型
        self.embedding_enabled = bool(emb_cfg.get("enabled", False))
        self.embed_base_url = (st_emb.get("base_url")
                               or os.environ.get("OPENAI_EMBEDDING_BASE_URL")
                               or emb_cfg.get("base_url") or self.base_url).rstrip("/")
        key_env = emb_cfg.get("api_key_env", "EMBEDDING_API_KEY")
        self.embed_api_key = (st_emb.get("api_key")
                              or os.environ.get("OPENAI_EMBEDDING_API_KEY")
                              or os.environ.get(key_env, ""))
        self.embedding_model = (st_emb.get("model")
                                or os.environ.get("OPENAI_EMBEDDING_MODEL")
                                or emb_cfg.get("model", ""))
        self.rerank_model = (st_emb.get("rerank_model")
                             or os.environ.get("RERANK_MODEL")
                             or emb_cfg.get("rerank_model", ""))
        # 用量统计（Token / 调用次数）
        self.usage: list[dict] = []

    # ---- 用量统计 ----
    def reset_usage(self) -> None:
        """清空用量统计。"""
        self.usage = []

    def record_usage(self, usage: dict | None, cached: bool = False) -> None:
        """记录一次调用的 token 用量（含命中缓存标记）。"""
        u = usage or {}
        self.usage.append({
            "model": self.model,
            "prompt_tokens": int(u.get("prompt_tokens") or 0),
            "completion_tokens": int(u.get("completion_tokens") or 0),
            "total_tokens": int(u.get("total_tokens") or 0),
            "cached": bool(cached),
        })

    def usage_summary(self) -> dict:
        """汇总用量：调用数、缓存命中数与各 token 合计。"""
        calls = len(self.usage)
        cached = sum(1 for u in self.usage if u.get("cached"))
        return {
            "calls": calls,
            "cached_calls": cached,
            "prompt_tokens": sum(u["prompt_tokens"] for u in self.usage),
            "completion_tokens": sum(u["completion_tokens"] for u in self.usage),
            "total_tokens": sum(u["total_tokens"] for u in self.usage),
        }

    @property
    def available(self) -> bool:
        """对话模型是否可用（需同时具备 api_key 与 base_url）。"""
        return bool(self.api_key and self.base_url)

    @property
    def embedding_available(self) -> bool:
        """向量服务是否可用：需启用 + base_url + 模型 + 密钥。"""
        return bool(self.embedding_enabled and self.embed_base_url and self.embedding_model
                    and (self.embed_api_key or self.api_key))

    def _headers(self) -> dict:
        """对话请求头（Bearer 鉴权）。"""
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def chat(self, messages: list[dict], temperature: float | None = None, json_mode: bool = False) -> str:
        """发起一次对话补全，返回文本内容；未配置时抛 LLMUnavailable。

        json_mode 会要求模型输出 JSON 对象；开启缓存时同参数请求命中磁盘缓存。
        """
        if not self.available:
            raise LLMUnavailable("未配置 OPENAI_API_KEY / base_url")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        # LLM 响应缓存（默认关闭；设置页或 config.llm.cache 开启）
        use_cache = bool((load_settings().get("llm", {}) or {}).get("cache")
                         or (CONFIG.get("llm", {}) or {}).get("cache", False))
        key = None
        fp = None
        if use_cache:
            try:
                # 缓存键由模型 + 消息 + 温度 + 模式决定，保证同输入同输出
                key = hashlib.sha1(json.dumps(
                    [self.model, messages, payload["temperature"], json_mode],
                    ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                fp = _CACHE_DIR / f"{key}.json"
                if fp.exists():
                    d = json.loads(fp.read_text(encoding="utf-8"))
                    self.record_usage(d.get("usage"), cached=True)
                    return d["content"]
            except Exception:  # noqa: BLE001
                fp = None

        resp = requests.post(f"{self.base_url}/chat/completions", headers=self._headers(),
                             json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM 调用失败 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        self.record_usage(usage)
        # 成功结果写缓存；缓存写失败不影响本次返回
        if use_cache and fp is not None:
            try:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                fp.write_text(json.dumps({"content": content, "usage": usage}, ensure_ascii=False),
                              encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
        return content

    def chat_json(self, messages: list[dict], temperature: float | None = None) -> Any:
        """对话并解析 JSON（内部用 json_mode 提升格式稳定性）。"""
        return _extract_json(self.chat(messages, temperature=temperature, json_mode=True))

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本向量化；未配置 Embedding 时抛 LLMUnavailable。"""
        if not self.embedding_available:
            raise LLMUnavailable("未启用或未配置 Embedding 服务")
        headers = {"Authorization": f"Bearer {self.embed_api_key or self.api_key}",
                   "Content-Type": "application/json"}
        resp = requests.post(f"{self.embed_base_url}/embeddings", headers=headers,
                             json={"model": self.embedding_model, "input": texts}, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Embedding 调用失败 {resp.status_code}: {resp.text[:300]}")
        return [d["embedding"] for d in resp.json()["data"]]

    # ---- 容错封装：失败返回 None，让上层走规则兜底 ----

    def try_chat_json(self, messages: list[dict], temperature: float | None = None) -> Any | None:
        """容错版 chat_json：任何异常都返回 None，交由上层走规则兜底。"""
        try:
            return self.chat_json(messages, temperature=temperature)
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM 调用不可用，改用规则兜底: %s", str(exc)[:160])
            return None

    def try_embed(self, texts: list[str]) -> list[list[float]] | None:
        """容错版 embed：失败返回 None，供检索器回退本地索引。"""
        try:
            return self.embed(texts)
        except Exception as exc:  # noqa: BLE001
            log.warning("Embedding 不可用: %s", str(exc)[:160])
            return None

    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[dict]:
        """重排（SiliconFlow /v1/rerank 等 OpenAI 兼容重排接口）。"""
        if not self.embedding_available:
            raise LLMUnavailable("未启用或未配置 Embedding/Rerank 服务")
        headers = {"Authorization": f"Bearer {self.embed_api_key or self.api_key}",
                   "Content-Type": "application/json"}
        resp = requests.post(f"{self.embed_base_url}/rerank", headers=headers,
                             json={"model": self.rerank_model, "query": query,
                                   "documents": documents, "top_n": top_n}, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Rerank 调用失败 {resp.status_code}: {resp.text[:300]}")
        return resp.json().get("results", [])

    def try_rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[dict] | None:
        """带指数退避 + 冷却 + 结果缓存的 Rerank 容错封装。

        429/限流时指数退避重试；仍失败则进入冷却期，期间直接回退 RRF（不重复打接口）。
        """
        cfg = (CONFIG.get("rag", {}) or {}).get("rerank", {}) or {}
        retries = int(cfg.get("retries", 3))
        backoff = float(cfg.get("backoff", 2.0))
        cooldown = float(cfg.get("cooldown", 60.0))
        cache_size = int(cfg.get("cache_size", 512))

        try:
            key = hashlib.md5(json.dumps(
                [self.rerank_model, query, documents, top_n], ensure_ascii=False
            ).encode("utf-8")).hexdigest()
        except Exception:  # noqa: BLE001
            key = None
        cache = getattr(self, "_rerank_cache", None)
        if cache is None:
            cache = self._rerank_cache = {}
        if key and key in cache:
            return cache[key]
        if getattr(self, "_rerank_cooldown_until", 0.0) > time.time():
            return None  # 冷却期内直接回退 RRF

        last: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                res = self.rerank(query, documents, top_n=top_n)
                if key:
                    if len(cache) >= cache_size:
                        cache.clear()
                    cache[key] = res
                return res
            except Exception as exc:  # noqa: BLE001
                last = exc
                msg = str(exc)
                limited = "429" in msg or "rate limit" in msg.lower() or "TPM" in msg
                if limited and attempt < retries - 1:
                    wait = backoff ** (attempt + 1)
                    log.warning("Rerank 触发限流，等待 %.1fs 后重试（%s/%s）", wait, attempt + 1, retries)
                    time.sleep(wait)
                    continue
                break

        msg = str(last or "")
        if "429" in msg or "rate limit" in msg.lower() or "TPM" in msg:
            self._rerank_cooldown_until = time.time() + cooldown
            log.warning("Rerank 进入冷却 %.0fs，期间回退 RRF", cooldown)
        else:
            log.warning("Rerank 不可用: %s", msg[:160])
        return None

    def probe(self) -> bool:
        """探测一次调用是否成功。"""
        try:
            self.chat([{"role": "user", "content": "ping"}], temperature=0)
            return True
        except Exception:  # noqa: BLE001
            return False


_CLIENT: LLMClient | None = None


def get_llm() -> LLMClient:
    """返回进程级 LLM 客户端单例（懒加载）。"""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = LLMClient()
        log.info("LLM 客户端: available=%s model=%s", _CLIENT.available, _CLIENT.model)
    return _CLIENT


def reload_llm() -> LLMClient:
    """设置变更后重建单例（热生效）。"""
    global _CLIENT
    _CLIENT = None
    return get_llm()
