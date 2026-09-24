"""异步任务管理（UI「运行中」进度）+ 磁盘持久化。

run_plan 是分钟级 LLM 任务，必须异步：POST /api/analyses/run 立即返回 job_id，
前端轮询 GET /api/jobs/{id} 获取阶段与进度。

持久化（Update 5.1）：任务状态写入 `outputs/_jobs/<job_id>.json`，
进程重启后可恢复历史任务；中断的 RUNNING/PENDING 任务标记为 FAILED（进程重启）。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from src.common import CONFIG, get_logger

log = get_logger()

_JOBS_DIR = Path(CONFIG["paths"]["outputs"]) / "_jobs"


def _now() -> str:
    """当前时分秒字符串，用于阶段时间戳（展示用）。"""
    return datetime.now().strftime("%H:%M:%S")


class Job:
    """单个异步任务的状态载体，可序列化为 dict 落盘。

    状态机：PENDING → RUNNING → COMPLETED / COMPLETED_WITH_WARNINGS / FAILED。
    """

    def __init__(self, stock_code: str, year: int, use_llm: bool):
        # 取 uuid 前 12 位作为短 job_id，兼顾唯一性与可读性
        self.id = uuid.uuid4().hex[:12]
        self.stock_code = str(stock_code).zfill(6)
        self.year = int(year)
        self.use_llm = bool(use_llm)
        self.status = "PENDING"          # PENDING / RUNNING / COMPLETED / FAILED
        self.pct = 0
        self.stages: list[dict] = []     # [{name, pct, at}]
        self.error: str | None = None
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.updated_at: str | None = None
        self.warnings: list[str] = []      # 非致命失败（部分成功）

    def progress(self, name: str, pct: int):
        """记录一个阶段并更新总进度，随后持久化。"""
        self.pct = int(pct)
        self.stages.append({"name": name, "pct": int(pct), "at": _now()})
        self.touch()

    def touch(self):
        """刷新更新时间并落盘（每次进度变化都会调用）。"""
        self.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _persist(self)

    def to_dict(self) -> dict:
        """转为对外响应字典（字段名与 JobStatus schema 对齐）。"""
        return {
            "job_id": self.id, "status": self.status, "pct": self.pct,
            "stock_code": self.stock_code, "year": self.year, "use_llm": self.use_llm,
            "stages": self.stages, "error": self.error,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "updated_at": self.updated_at,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        """从落盘字典恢复任务；缺失字段使用安全默认值。"""
        j = cls(d.get("stock_code", "000000"), d.get("year", 0), d.get("use_llm", True))
        j.id = d.get("job_id") or j.id
        j.status = d.get("status", "PENDING")
        j.pct = int(d.get("pct") or 0)
        j.stages = list(d.get("stages") or [])
        j.error = d.get("error")
        j.started_at = d.get("started_at")
        j.finished_at = d.get("finished_at")
        j.updated_at = d.get("updated_at")
        j.warnings = list(d.get("warnings") or [])
        return j


def _job_file(job_id: str) -> Path:
    """任务状态文件路径约定。"""
    return _JOBS_DIR / f"{job_id}.json"


def _persist(job: Job) -> None:
    """原子写任务状态：先写临时文件再 os.replace，避免读到半截 JSON。"""
    try:
        _JOBS_DIR.mkdir(parents=True, exist_ok=True)
        fp = _job_file(job.id)
        tmp = fp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, fp)
    except Exception as exc:  # noqa: BLE001
        # 持久化失败不应影响任务执行，仅告警
        log.warning("任务持久化失败 %s：%s", job.id, str(exc)[:120])


def _load_all() -> dict[str, Job]:
    """启动时加载磁盘上的历史任务，并把中断的运行中任务标记为失败。"""
    out: dict[str, Job] = {}
    if not _JOBS_DIR.exists():
        return out
    for fp in _JOBS_DIR.glob("*.json"):
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
            j = Job.from_dict(d)
        except Exception:  # noqa: BLE001
            continue
        # 上次进程中断：PENDING/RUNNING → FAILED
        if j.status in ("PENDING", "RUNNING"):
            j.status = "FAILED"
            j.error = "进程重启，任务中断"
            j.finished_at = j.finished_at or _now()
            _persist(j)
        out[j.id] = j
    return out


class JobManager:
    """任务管理器：内存字典 + 磁盘持久化，线程安全。"""

    def __init__(self):
        self._jobs: dict[str, Job] = _load_all()
        # 后台线程会并发写 _jobs，用锁保护字典更新
        self._lock = threading.Lock()

    def start(self, stock_code: str, year: int, use_llm: bool = True,
              top_k: int | None = None, rounds: int | None = None, force: bool = False) -> Job:
        """创建并异步启动一次分析任务，立即返回 Job（不阻塞请求）。"""
        job = Job(stock_code, year, use_llm)
        with self._lock:
            self._jobs[job.id] = job
        _persist(job)
        # daemon 线程：进程退出时不阻塞；进度通过 job 对象回传
        threading.Thread(target=self._run, args=(job, top_k, rounds, force), daemon=True).start()
        log.info("job %s 启动：%s %s（llm=%s force=%s）", job.id, job.stock_code, job.year, use_llm, force)
        return job

    def start_knowledge_rebuild(self, backend: str = "auto") -> Job:
        """重建知识库索引（删旧索引 → 重建向量+BM25 → 重置 KB 单例）。"""
        job = Job("KB", 0, False)
        with self._lock:
            self._jobs[job.id] = job
        _persist(job)
        threading.Thread(target=self._run_kb_rebuild, args=(job, backend), daemon=True).start()
        log.info("知识库重建任务 %s 启动（backend=%s）", job.id, backend)
        return job

    def _run_kb_rebuild(self, job: Job, backend: str):
        """后台线程体：执行知识库重建，任何异常都转为 FAILED 并记录。"""
        import shutil
        from pathlib import Path

        from src.common import CONFIG
        job.status = "RUNNING"
        job.started_at = _now()
        job.touch()
        try:
            out_dir = Path(CONFIG["rag"]["index_dir"])
            job.progress("删除旧索引", 5)
            # 先删旧目录，避免新旧索引文件混杂导致检索到过期分块
            if out_dir.exists():
                shutil.rmtree(out_dir)
            job.progress("重建向量 + BM25（可能较久）", 20)
            from src.rag.store import KnowledgeBase
            kb = KnowledgeBase.build(backend=backend)
            import src.rag.store as _rs
            _rs._KB = None                     # 重置单例，下次检索用新索引
            job.progress(f"完成：policy={kb.policy.backend} case={kb.case.backend}", 100)
            job.status = "COMPLETED"
            job.pct = 100
        except Exception as exc:  # noqa: BLE001
            job.status = "FAILED"
            job.error = str(exc)[:400]
            log.warning("知识库重建失败：%s", job.error)
        finally:
            # 无论成功失败都要收尾，保证前端轮询能拿到终态
            job.finished_at = _now()
            job.touch()

    def _run(self, job: Job, top_k, rounds, force: bool = False):
        """后台线程体：执行 run_plan，捕获异常并写入终态与告警。"""
        job.status = "RUNNING"
        job.started_at = _now()
        job.touch()
        try:
            from src.pipeline.plan_pipeline import run_plan
            res = run_plan(job.stock_code, job.year, use_llm=job.use_llm,
                           top_k=top_k, rounds=rounds, save=True, progress=job.progress, force=force)
            job.warnings = list((res or {}).get("warnings") or [])
            # 有非致命告警时用 COMPLETED_WITH_WARNINGS，前端可提示"部分成功"
            job.status = "COMPLETED_WITH_WARNINGS" if job.warnings else "COMPLETED"
            job.pct = 100
        except Exception as exc:  # noqa: BLE001
            job.status = "FAILED"
            job.error = str(exc)[:400]
            log.warning("job %s 失败：%s", job.id, job.error)
        finally:
            job.finished_at = _now()
            job.touch()

    def get(self, job_id: str) -> Job | None:
        """按 id 取任务：内存未命中则回退读磁盘并回填缓存。"""
        j = self._jobs.get(job_id)
        if j is not None:
            return j
        fp = _job_file(job_id)
        if fp.exists():
            try:
                j = Job.from_dict(json.loads(fp.read_text(encoding="utf-8")))
                with self._lock:
                    self._jobs[j.id] = j
                return j
            except Exception:  # noqa: BLE001
                return None
        return None

    def recent(self, limit: int = 30) -> list[dict]:
        """按更新时间倒序返回最近任务（默认 30 条）。"""
        jobs = sorted(self._jobs.values(),
                      key=lambda x: (x.updated_at or x.started_at or ""), reverse=True)
        return [j.to_dict() for j in jobs[:limit]]


_JM: JobManager | None = None


def get_jobs() -> JobManager:
    """返回进程级单例任务管理器（懒加载）。"""
    global _JM
    if _JM is None:
        _JM = JobManager()
    return _JM
