"""知识库检索。

检索策略（Hybrid）：
    用户问题
      ├─ 向量检索 (bge-m3)        -> Top-N 候选
      └─ 关键词检索 (BM25/jieba)   -> Top-N 候选
                 ↓ 合并去重
              Reranker (bge-reranker-v2-m3)
                 ↓
              Top-K 结果
Reranker 不可用时回退 RRF 融合；向量不可用时回退本地 TF-IDF。

向量索引采用「台账」设计，支持断点续跑与增量更新：
    knowledge/index/<corpus>/embeddings.npy       向量矩阵（按台账顺序）
    knowledge/index/<corpus>/embed_ledger.parquet chunk_id + content_hash
    knowledge/index/<corpus>/bm25.joblib          BM25 稀疏索引
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import CONFIG, get_logger

log = get_logger()

# 检索结果缓存（进程内 LRU）
_SEARCH_CACHE: dict = {}
_SEARCH_CACHE_MAX = 256

try:
    import jieba
    import joblib
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _HAS_SK = True
except Exception:  # noqa: BLE001
    _HAS_SK = False

try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except Exception:  # noqa: BLE001
    _HAS_BM25 = False


def _tokenize(text: str) -> list[str]:
    """jieba 中文分词，去除空白 token，供 BM25/TF-IDF 使用。"""
    return [t for t in jieba.cut(str(text)) if t.strip()]


def _hash(text: str) -> str:
    """内容哈希（MD5），用于向量台账的增量复用判断。"""
    return hashlib.md5(str(text).encode("utf-8")).hexdigest()


def _fingerprint(llm=None) -> dict:
    """当前 Embedding/Rerank 指纹（防换模型静默复用向量）。"""
    if llm is None:
        from src.llm.client import get_llm
        llm = get_llm()
    return {
        "embedding_model": getattr(llm, "embedding_model", "") or "",
        "embed_base_url": getattr(llm, "embed_base_url", "") or "",
        "rerank_model": getattr(llm, "rerank_model", "") or "",
    }


def _fingerprint_mismatch(stored: dict | None, current: dict, dim: int | None = None) -> str | None:
    """返回不一致原因（None 表示一致；旧索引缺指纹视为可采纳）。"""
    if not stored:
        return None
    if stored.get("embedding_model") != current.get("embedding_model"):
        return f"embedding_model：索引={stored.get('embedding_model')!r} 当前={current.get('embedding_model')!r}"
    if stored.get("embed_base_url") != current.get("embed_base_url"):
        return f"embed_base_url：索引={stored.get('embed_base_url')!r} 当前={current.get('embed_base_url')!r}"
    if dim is not None and stored.get("dim") not in (None, dim):
        return f"向量维度：索引={stored.get('dim')} 当前={dim}"
    return None


def _adopt_fingerprint(meta_path: Path, meta: dict, dim: int | None) -> None:
    """旧索引缺指纹：采纳当前指纹并补写 meta（不强制重建）。"""
    cur = _fingerprint()
    if dim is not None:
        cur["dim"] = dim
    meta["embedding"] = cur
    try:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        log.info("已为旧索引补写 Embedding 指纹：%s", cur)
    except Exception as exc:  # noqa: BLE001
        log.warning("补写指纹失败：%s", str(exc)[:120])


class Retriever:
    """单一语料（政策 / 案例）的检索器。"""

    def __init__(self, chunks: pd.DataFrame, backend: str = "local"):
        self.chunks = chunks.reset_index(drop=True)
        self.backend = backend
        self.vectorizer = None
        self.matrix = None
        self.embeddings = None
        self.bm25 = None
        self.checkpoint_dir: Path | None = None
        self.stats: dict = {}
        self.fingerprint_mismatch: str | None = None

    # -------------------------------------------------- 文件

    def _emb_file(self) -> Path:
        """向量矩阵文件路径。"""
        return self.checkpoint_dir / "embeddings.npy"

    def _ledger_file(self) -> Path:
        """向量台账文件路径（chunk_id + 内容哈希）。"""
        return self.checkpoint_dir / "embed_ledger.parquet"

    def _bm25_file(self) -> Path:
        """BM25 稀疏索引文件路径。"""
        return self.checkpoint_dir / "bm25.joblib"

    def _load_existing(self):
        """加载已有向量与台账，做指纹与行数校验；不满足则返回全 None（触发重建）。"""
        if not self.checkpoint_dir:
            return None, None, None
        if not (self._emb_file().exists() and self._ledger_file().exists()):
            return None, None, None
        # 指纹校验：换模型/维度 → 拒绝复用（强制全量重建）；旧索引缺指纹 → 采纳补写
        try:
            meta = json.loads((self.checkpoint_dir / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        emb = np.load(self._emb_file())
        if not meta.get("embedding"):
            _adopt_fingerprint(self.checkpoint_dir / "meta.json", meta, int(emb.shape[1]))
        reason = _fingerprint_mismatch(meta.get("embedding"), _fingerprint(), dim=int(emb.shape[1]))
        if reason:
            log.error("Embedding 指纹不一致（%s），忽略旧索引并全量重建", reason)
            self.fingerprint_mismatch = reason
            return None, None, None
        led = pd.read_parquet(self._ledger_file())
        # 台账与向量必须一一对应，否则索引不可信
        if len(led) != emb.shape[0]:
            log.warning("台账与向量行数不一致，忽略旧索引")
            return None, None, None
        return emb, led["chunk_id"].astype(str).tolist(), led["content_hash"].astype(str).tolist()

    def _save_ledger(self, mat: np.ndarray, ids: list[str], hashes: list[str], only_finite: bool = True):
        """保存向量与台账（断点续跑用）：默认只保留已成功向量化的行。"""
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        mask = np.isfinite(mat).all(axis=1) if only_finite else np.ones(len(ids), dtype=bool)
        np.save(self._emb_file(), mat[mask])
        pd.DataFrame({
            "chunk_id": [i for i, m in zip(ids, mask) if m],
            "content_hash": [h for h, m in zip(hashes, mask) if m],
        }).to_parquet(self._ledger_file(), index=False)

    # -------------------------------------------------- 构建

    def build(self, limit: int | None = None) -> "Retriever":
        """构建检索索引：优先向量，未完成/不可用时回退本地 TF-IDF。"""
        if self.backend == "embedding":
            from src.llm.client import get_llm
            llm = get_llm()
            if not llm.embedding_available:
                log.warning("Embedding 未启用/未配置，回退本地 TF-IDF")
                self.backend = "local"
            else:
                mat, complete = self._build_embeddings(llm, limit=limit)
                if complete:
                    self.embeddings = mat
                    self.backend = "embedding"
                else:
                    # 向量未补全时本次先用本地检索，已完成的进度保留供下次续跑
                    log.warning("向量未完成 %s/%s，本次回退本地检索（下次可继续补全）",
                                int(np.isfinite(mat).all(axis=1).sum()), len(mat))
                    self.backend = "local"
        if self.backend != "embedding" and _HAS_SK:
            # 本地回退：TF-IDF 稀疏向量 + 余弦相似度
            self.vectorizer = TfidfVectorizer(tokenizer=_tokenize, token_pattern=None,
                                              lowercase=False, max_features=200000)
            self.matrix = self.vectorizer.fit_transform(self.chunks["text"].tolist())
        return self

    def build_bm25(self) -> "Retriever":
        """构建/加载 BM25 稀疏索引（已存在则直接复用）。"""
        if not _HAS_BM25:
            log.warning("未安装 rank_bm25，跳过 BM25")
            return self
        bm = self._bm25_file()
        if bm.exists():
            try:
                self.bm25 = joblib.load(bm)
                return self
            except Exception:  # noqa: BLE001
                pass
        log.info("构建 BM25 索引（%s 篇）...", len(self.chunks))
        tokens = [_tokenize(t) for t in self.chunks["text"].tolist()]
        self.bm25 = BM25Okapi(tokens)
        if self.checkpoint_dir:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            joblib.dump(self.bm25, bm)
        return self

    def _build_embeddings(self, llm, limit: int | None = None):
        """按台账增量构建向量：复用未变 chunk，仅对新增/变更 chunk 调 Embedding。

        返回 (向量矩阵, 是否完整)；未完整时矩阵中对应行为 NaN，可断点续跑。
        """
        import time

        ids = [str(c) for c in self.chunks["chunk_id"].tolist()]
        texts = self.chunks["text"].tolist()
        hashes = [_hash(t) for t in texts]
        n = len(ids)

        old_emb, old_ids, old_hashes = self._load_existing()
        reuse: dict[str, np.ndarray] = {}
        cur_hash = dict(zip(ids, hashes))
        old_id_set = set(old_ids or [])
        # 复用条件：chunk_id 存在且内容哈希未变
        if old_emb is not None:
            old_hash = dict(zip(old_ids, old_hashes))
            for i, cid in enumerate(old_ids):
                if cid in cur_hash and old_hash.get(cid) == cur_hash[cid]:
                    reuse[cid] = old_emb[i]

        pending = [(i, ids[i], texts[i], hashes[i]) for i in range(n) if ids[i] not in reuse]
        n_reuse = n - len(pending)
        n_new = sum(1 for cid in ids if cid not in old_id_set)
        n_changed = len(pending) - n_new
        n_removed = sum(1 for cid in old_id_set if cid not in cur_hash)
        log.info("增量分析: 总数 %s | 复用 %s | 新增 %s | 变更 %s | 移除 %s",
                 n, n_reuse, n_new, n_changed, n_removed)
        if limit:
            pending = pending[:limit]

        # 先探测一次维度；已有复用向量则直接取其维度
        dim = next((v.shape[0] for v in reuse.values()), None)
        if dim is None:
            if pending:
                try:
                    first = llm.embed([pending[0][2]])
                except Exception as exc:  # noqa: BLE001
                    log.error("Embedding 不可用，跳过向量构建: %s", str(exc)[:160])
                    return np.full((n, 1), np.nan, dtype="float32"), False
                dim = len(first[0])
                reuse[pending[0][1]] = np.asarray(first[0], dtype="float32")
                pending = pending[1:]
            else:
                dim = 1

        mat = np.full((n, dim), np.nan, dtype="float32")
        id_pos = {cid: i for i, cid in enumerate(ids)}
        for cid, vec in reuse.items():
            j = id_pos.get(cid)
            if j is not None:
                mat[j] = vec

        batch = int(CONFIG.get("embedding", {}).get("batch_size", 32))
        interval = float(CONFIG.get("embedding", {}).get("batch_interval", 0.0))
        for k in range(0, len(pending), batch):
            group = pending[k:k + batch]
            vecs = None
            # 退避重试：频率限制指数等待，普通异常固定 2s，额度用尽则立即停止
            for attempt in range(8):
                try:
                    vecs = llm.embed([g[2] for g in group])
                    break
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc)
                    if any(s in msg for s in ("FreeTierOnly", "Free quota", "AllocationQuota")):
                        log.error("Embedding 免费额度已用尽，保存进度后可后续继续：%s", msg[:140])
                        break
                    if "429" in msg or "rate limit" in msg.lower() or "TPM" in msg:
                        wait = min(90, 15 * (attempt + 1))
                        log.warning("触发频率限制，等待 %ss 后重试 (第%s次)", wait, attempt + 1)
                        time.sleep(wait)
                        continue
                    log.warning("批次异常，2s 后重试: %s", msg[:140])
                    time.sleep(2)
            if vecs is None:
                log.error("停止本轮向量化，已处理批次保留在检查点")
                break
            for (i, _cid, _t, _h), v in zip(group, vecs):
                mat[i] = np.asarray(v, dtype="float32")
            # 每 25 批落一次台账，降低中断损失
            if self.checkpoint_dir and (k // batch) % 25 == 0:
                self._save_ledger(mat, ids, hashes)
            if (k // batch) % 20 == 0:
                log.info("  进度 %s/%s", int(np.isfinite(mat).all(axis=1).sum()), n)
            if interval:
                time.sleep(interval)

        # L2 归一化，使内积等价于余弦相似度
        norms = np.linalg.norm(np.nan_to_num(mat), axis=1, keepdims=True) + 1e-9
        mat = mat / norms
        complete = bool(np.isfinite(mat).all())
        if self.checkpoint_dir:
            self._save_ledger(mat, ids, hashes)
        self.stats = {"total": n, "reuse": n_reuse, "new": n_new, "changed": n_changed,
                      "removed": n_removed, "embedded": len(pending), "complete": complete}
        return mat, complete

    # -------------------------------------------------- 打分

    def _dense_scores(self, query: str) -> np.ndarray | None:
        """稠密打分：向量内积或 TF-IDF 余弦；不可用返回 None。"""
        if self.backend == "embedding" and self.embeddings is not None:
            from src.llm.client import get_llm
            qv = get_llm().try_embed([query])
            if qv is None:
                return None
            q = np.asarray(qv[0], dtype="float32")
            # 查询向量归一化后与已归一化矩阵做内积即余弦相似度
            q /= (np.linalg.norm(q) + 1e-9)
            return self.embeddings @ q
        if self.vectorizer is not None and self.matrix is not None:
            q = self.vectorizer.transform([query])
            return cosine_similarity(q, self.matrix)[0]
        return None

    def _sparse_scores(self, query: str) -> np.ndarray | None:
        """BM25 稀疏打分；未构建 BM25 时返回 None。"""
        if self.bm25 is None:
            return None
        return np.asarray(self.bm25.get_scores(_tokenize(query)))

    def _rows(self, idx_scores: list[tuple[int, float]], retriever: str) -> list[dict]:
        """把 (行号, 分数) 转为结果字典，过滤非正分并标注检索器来源。"""
        out = []
        for i, s in idx_scores:
            if s <= 0:
                continue
            row = self.chunks.iloc[int(i)].to_dict()
            row["score"] = float(s)
            row["retriever"] = retriever
            out.append(row)
        return out

    def _filter_mask(self, filters: dict | None):
        """元数据过滤：返回布尔掩码（None 表示不过滤）。

        - 普通字段：等值；值为列表时 isin。
        - tax_type：政策语料为逗号复合值（如"税收政策-增值税,税收政策-企业所得税"），
          故用"包含任一"匹配，且允许空值（未标注税种不排除）。
        """
        if not filters:
            return None
        mask = np.ones(len(self.chunks), dtype=bool)
        for k, v in filters.items():
            if v is None or k not in self.chunks.columns:
                continue
            col = self.chunks[k].astype(str)
            if k == "tax_type":
                allowed = [str(x) for x in (v if isinstance(v, (list, tuple, set)) else [v]) if str(x)]
                if not allowed:
                    continue
                pat = "|".join(re.escape(a) for a in allowed)
                mask &= (col.eq("") | col.str.contains(pat, regex=True, na=False)).to_numpy()
            elif isinstance(v, (list, tuple, set)):
                mask &= col.isin([str(x) for x in v]).to_numpy()
            else:
                mask &= col.eq(str(v)).to_numpy()
        return mask

    def retrieve(self, query: str, top_k: int = 5, filters: dict | None = None) -> list[dict]:
        """单路稠密检索：按分数取 top_k，支持元数据过滤。"""
        if self.chunks.empty:
            return []
        scores = self._dense_scores(query)
        if scores is None:
            return []
        mask = self._filter_mask(filters)
        if mask is not None:
            # 过滤不命中置 -inf，保证不会进入 top_k
            scores = np.where(mask, scores, -np.inf)
        idx = np.argsort(-scores)[:top_k]
        return self._rows([(int(i), float(scores[i])) for i in idx], self.backend)

    def retrieve_hybrid(self, query: str, top_k: int = 5, candidates: int = 20,
                        filters: dict | None = None) -> list[dict]:
        """向量 + BM25 -> 合并 -> Reranker；Reranker 不可用则 RRF。"""
        if self.chunks.empty:
            return []
        dense = self._dense_scores(query)
        sparse = self._sparse_scores(query)
        if dense is None and sparse is None:
            return []
        mask = self._filter_mask(filters)
        if mask is not None:
            if dense is not None:
                dense = np.where(mask, dense, -np.inf)
            if sparse is not None:
                sparse = np.where(mask, sparse, -np.inf)

        cand: set[int] = set()
        # 两路各取 candidates 个候选后取并集，交由重排精排
        if dense is not None:
            cand |= set(np.argsort(-dense)[:candidates].tolist())
        if sparse is not None:
            cand |= set(np.argsort(-sparse)[:candidates].tolist())
        if mask is not None:
            cand = {i for i in cand if mask[i]}
        cand_list = sorted(cand)
        if not cand_list:
            return []

        # 1) Reranker
        use_hybrid = CONFIG.get("rag", {}).get("hybrid", True)
        if use_hybrid:
            from src.llm.client import get_llm
            # 截断到 2000 字控制重排开销
            docs = [str(self.chunks.iloc[i]["text"])[:2000] for i in cand_list]
            rer = get_llm().try_rerank(query, docs, top_n=top_k)
            if rer:
                out = []
                for r in rer:
                    i = cand_list[int(r["index"])]
                    row = self.chunks.iloc[i].to_dict()
                    row["score"] = float(r.get("relevance_score", 0.0))
                    row["retriever"] = "hybrid+rerank"
                    out.append(row)
                return out

        # 2) RRF 融合兜底
        # 倒数排名融合（k=60）：无需两路分数量纲一致，稳健合并排名
        rank_dense = {int(i): r for r, i in enumerate(np.argsort(-dense))} if dense is not None else {}
        rank_sparse = {int(i): r for r, i in enumerate(np.argsort(-sparse))} if sparse is not None else {}
        fused = []
        for i in cand_list:
            s = 0.0
            if i in rank_dense:
                s += 1.0 / (60 + rank_dense[i])
            if i in rank_sparse:
                s += 1.0 / (60 + rank_sparse[i])
            fused.append((i, s))
        fused.sort(key=lambda x: -x[1])
        return self._rows(fused[:top_k], "hybrid+rrf")

    # -------------------------------------------------- 持久化

    def save(self, path: Path) -> None:
        """持久化 chunks/元信息/各检索器到指定目录。"""
        path.mkdir(parents=True, exist_ok=True)
        self.chunks.to_parquet(path / "chunks.parquet", index=False)
        meta: dict = {"backend": self.backend}
        # 仅向量后端写入 Embedding 指纹，供加载时校验一致性
        if self.backend == "embedding" and self.embeddings is not None:
            fp = _fingerprint()
            fp["dim"] = int(self.embeddings.shape[1])
            meta["embedding"] = fp
        (path / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        if self.vectorizer is not None:
            joblib.dump(self.vectorizer, path / "vectorizer.joblib")
        if self.matrix is not None:
            joblib.dump(self.matrix, path / "matrix.joblib")
        if self.backend == "embedding" and self.embeddings is not None:
            np.save(path / "embeddings.npy", self.embeddings)
        if self.bm25 is not None:
            joblib.dump(self.bm25, path / "bm25.joblib")

    @classmethod
    def load(cls, path: Path) -> "Retriever":
        """从目录加载检索器；向量指纹不一致时回退本地 TF-IDF 并标记。"""
        chunks = pd.read_parquet(path / "chunks.parquet")
        try:
            meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
        backend = meta.get("backend", "local")
        r = cls(chunks, backend=backend)
        if backend == "embedding" and (path / "embeddings.npy").exists():
            emb = np.load(path / "embeddings.npy")
            if not meta.get("embedding"):
                _adopt_fingerprint(path / "meta.json", meta, int(emb.shape[1]))
            reason = _fingerprint_mismatch(meta.get("embedding"), _fingerprint(), dim=int(emb.shape[1]))
            if reason:
                # 换模型后旧向量不可比：拒绝复用并回退本地检索，提示重建
                log.error("Embedding 指纹不一致（%s）→ 拒绝复用向量并回退本地检索；"
                          "请运行 scripts/build_knowledge.py --rebuild 重建", reason)
                r.fingerprint_mismatch = reason
                r.backend = "local"
                r.embeddings = None
                if _HAS_SK:
                    r.vectorizer = TfidfVectorizer(tokenizer=_tokenize, token_pattern=None,
                                                   lowercase=False, max_features=200000)
                    r.matrix = r.vectorizer.fit_transform(chunks["text"].tolist())
            else:
                r.embeddings = emb
        else:
            r.backend = "local"
            if (path / "vectorizer.joblib").exists():
                r.vectorizer = joblib.load(path / "vectorizer.joblib")
                r.matrix = joblib.load(path / "matrix.joblib")
        if (path / "bm25.joblib").exists():
            try:
                r.bm25 = joblib.load(path / "bm25.joblib")
            except Exception:  # noqa: BLE001
                r.bm25 = None
        return r


class KnowledgeBase:
    """政策库 + 案例库 + 文档库统一入口。"""

    def __init__(self, policy: Retriever, case: Retriever, document: Retriever | None = None):
        self.policy = policy
        self.case = case
        self.document = document if document is not None else Retriever(pd.DataFrame())

    def search(self, query: str, corpus: str = "policy", top_k: int = 5,
               channel_weights: dict | None = None, filters: dict | None = None) -> list[dict]:
        # 检索结果缓存（同 query/参数复用，进程内 LRU）
        def _hkey(v):
            if isinstance(v, dict):
                return tuple(sorted((k, _hkey(x)) for k, x in v.items()))
            if isinstance(v, (list, tuple, set)):
                return tuple(_hkey(x) for x in v)
            return v

        try:
            key = (corpus, query, int(top_k), _hkey(filters), _hkey(channel_weights))
            hash(key)  # 校验可哈希
        except Exception:  # noqa: BLE001
            key = None
        if key is not None and key in _SEARCH_CACHE:
            return _SEARCH_CACHE[key]
        res = self._search(query, corpus, top_k, channel_weights, filters)
        if key is not None:
            _SEARCH_CACHE[key] = res
            if len(_SEARCH_CACHE) > _SEARCH_CACHE_MAX:
                _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
        return res

    def _search(self, query: str, corpus: str, top_k: int,
                channel_weights: dict | None, filters: dict | None) -> list[dict]:
        if corpus == "document":
            return self.document.retrieve_hybrid(
                query, top_k=top_k, candidates=int(CONFIG.get("rag", {}).get("candidates", 20)),
                filters=filters)
        r = self.policy if corpus == "policy" else self.case
        candidates = int(CONFIG.get("rag", {}).get("candidates", 20))
        # 频道加权：共享检索，按角色偏好重排（不硬过滤），并做多样性保底
        if channel_weights:
            hits = r.retrieve_hybrid(query, top_k=max(top_k * 3, 9), candidates=candidates,
                                     filters=filters)
            for h in hits:
                h["_w"] = float(channel_weights.get(h.get("channel"), 1.0))
            hits.sort(key=lambda h: -(h.get("score", 0) * h["_w"]))
            top = hits[:top_k]
            preferred = max(channel_weights, key=channel_weights.get)
            if top and all(h.get("channel") == preferred for h in top):
                for h in hits[top_k:]:
                    if h.get("channel") != preferred:
                        top[-1] = h
                        break
            return top
        if r.bm25 is not None or r.backend == "embedding":
            return r.retrieve_hybrid(query, top_k=top_k, candidates=candidates, filters=filters)
        return r.retrieve(query, top_k=top_k, filters=filters)

    def search_both(self, query: str, top_k: int = 3) -> dict:
        """同时在政策库与案例库检索，返回两路结果。"""
        return {"policy": self.search(query, "policy", top_k),
                "case": self.search(query, "case", top_k)}

    @classmethod
    def build(cls, backend: str = "auto", limit: int | None = None) -> "KnowledgeBase":
        """重建全部语料库索引（切分 -> 构建 -> 落盘），返回 KnowledgeBase。"""
        from src.rag.chunking import build_all
        out_dir = Path(CONFIG["rag"]["index_dir"])
        policy_chunks, case_chunks, doc_chunks = build_all()
        backend = _resolve_backend(backend)
        policy = Retriever(policy_chunks, backend=backend)
        case = Retriever(case_chunks, backend=backend)
        # checkpoint_dir 用于增量台账与断点续跑
        policy.checkpoint_dir = out_dir / "policy"
        case.checkpoint_dir = out_dir / "case"
        policy.build(limit=limit)
        case.build(limit=limit)
        policy.build_bm25()
        case.build_bm25()
        policy.save(out_dir / "policy")
        case.save(out_dir / "case")
        # 文档库可能为空（无用户文档），此时留空 Retriever
        document = Retriever(pd.DataFrame())
        if not doc_chunks.empty:
            document = Retriever(doc_chunks, backend=backend)
            document.checkpoint_dir = out_dir / "document"
            document.build(limit=limit)
            document.build_bm25()
            document.save(out_dir / "document")
        return cls(policy, case, document)

    @classmethod
    def load(cls) -> "KnowledgeBase":
        """从磁盘索引加载知识库（政策/案例必选，文档库可选）。"""
        out_dir = Path(CONFIG["rag"]["index_dir"])
        doc_dir = out_dir / "document"
        document = (Retriever.load(doc_dir) if (doc_dir / "chunks.parquet").exists()
                    else Retriever(pd.DataFrame()))
        return cls(Retriever.load(out_dir / "policy"), Retriever.load(out_dir / "case"), document)


def _resolve_backend(backend: str) -> str:
    """解析检索后端：auto 时按 Embedding 是否可用选择 embedding/local。"""
    if backend != "auto":
        return backend
    from src.llm.client import get_llm
    return "embedding" if get_llm().embedding_available else "local"


_KB: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    """返回进程级知识库单例（懒加载，从磁盘索引载入）。"""
    global _KB
    if _KB is None:
        _KB = KnowledgeBase.load()
    return _KB
