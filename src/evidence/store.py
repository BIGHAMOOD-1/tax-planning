"""Evidence 与 Historical Solution 的 SQLite 存储（后端，与界面解耦）。

SQLite = source of truth；JSONL 仅作导入导出/日志。
支持从 2.1/2.2 旧 schema 自动迁移（新增列 + 旧列数据回填）。

设计要点：
    - 表结构由 Evidence dataclass 字段驱动，新增字段可自动 ALTER 补列；
    - 旧列通过 LEGACY_COLUMN_MAP 回填到新列（COALESCE，不覆盖已有新值）；
    - 证据与方案分表：evidence 记录原始事实，solutions 记录审定后的历史方案。
不变量：
    - evidence_id 全局唯一（缺失/冲突时自动生成）；
    - solutions 以 solution_id 唯一，重复保存执行 INSERT OR REPLACE。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from src.common import CONFIG, get_logger
from src.evidence.contract import Evidence

log = get_logger()

DEFAULT_DB = Path(CONFIG["paths"]["data_processed"]) / "evidence" / "evidence.db"

# 证据表列 = Evidence dataclass 字段（字段增删会自动驱动建表/迁移）
EVIDENCE_FIELDS = list(Evidence.__dataclass_fields__.keys())

# 旧列 -> 新列（迁移时把旧数据搬到新列）
# 只在目标新列为空时回填（COALESCE），避免覆盖迁移后已写入的新值
LEGACY_COLUMN_MAP = {
    "fact_value": "value_num",
    "fact_text": "value_text",
    "extract_method": "extraction_method",
    "confidence": "extraction_confidence",
    "review_status": "verification_status",
}
# verification_status 的旧值归一：冲突/已合并统一归为候选，等待人工处理
_VERIF_REMAP = {"conflict": "candidate", "merged": "candidate"}

SOLUTION_FIELDS = [
    # 历史方案表列；复杂结构（条件/政策/证据链等）以 JSON 文本存储
    "solution_id", "stock_code", "company_name", "year", "direction", "skill_id",
    "fact_conditions", "policies", "ai_proposal", "external_evidence",
    "human_review", "final_decision", "review_status", "evidence_chain", "created_at",
]


class EvidenceStore:
    """Evidence 与 Historical Solution 的 SQLite 仓储。

    构造时自动建表/迁移；:memory: 路径用于测试（不落盘）。
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DEFAULT_DB)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        """建立连接并设置 Row 工厂，使查询结果可按列名访问。"""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _existing_columns(self, c, table: str) -> set[str]:
        """读取表当前列名集合（用于迁移时判断缺列/旧列）。"""
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}

    def _init_schema(self):
        """建表 + 从旧 schema 迁移（补列、旧列回填、状态值归一）。"""
        with self._conn() as c:
            c.execute(f"""CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                {', '.join(f + ' TEXT' for f in EVIDENCE_FIELDS)}
            )""")
            # 迁移：补齐缺失列
            have = self._existing_columns(c, "evidence")
            for f in EVIDENCE_FIELDS:
                if f not in have:
                    c.execute(f"ALTER TABLE evidence ADD COLUMN {f} TEXT")
            # 迁移：旧列数据回填到新列
            for old, new in LEGACY_COLUMN_MAP.items():
                if old in have and new in EVIDENCE_FIELDS:
                    c.execute(f"UPDATE evidence SET {new}=COALESCE({new}, {old}) WHERE {new} IS NULL")
            if "review_status" in have:
                for old_v, new_v in _VERIF_REMAP.items():
                    c.execute("UPDATE evidence SET verification_status=? "
                              "WHERE verification_status=?", (new_v, old_v))
            if "year" in have:
                # 旧 year 列迁移到 report_year（报告年），保留新列已写值
                c.execute("UPDATE evidence SET report_year=COALESCE(report_year, year) "
                          "WHERE report_year IS NULL")

            c.execute("""CREATE TABLE IF NOT EXISTS solutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                solution_id TEXT UNIQUE, stock_code TEXT, company_name TEXT, year TEXT,
                direction TEXT, skill_id TEXT,
                fact_conditions TEXT, policies TEXT, ai_proposal TEXT,
                external_evidence TEXT, human_review TEXT, final_decision TEXT,
                review_status TEXT, evidence_chain TEXT, created_at TEXT
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ev_key ON evidence(stock_code, year, fact_type)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ev_status ON evidence(verification_status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ev_doc ON evidence(document_id)")

    # ---------------- Evidence ----------------

    def add(self, ev: Evidence) -> str:
        """写入一条证据；evidence_id 为空或已存在时自动生成新编号，返回该编号。"""
        if not ev.evidence_id or self.get(ev.evidence_id):
            ev.evidence_id = self._next_id()
        cols = EVIDENCE_FIELDS
        vals = [None if ev.to_dict()[k] is None else str(ev.to_dict()[k]) for k in cols]
        with self._conn() as c:
            c.execute(f"INSERT INTO evidence ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        return ev.evidence_id

    def _next_id(self) -> str:
        """生成证据编号 EV<日期><自增序号>；序号取当前最大 id+1（单机单写场景足够）。"""
        with self._conn() as c:
            n = c.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM evidence").fetchone()[0]
        return f"EV{datetime.now().strftime('%Y%m%d')}{n:04d}"

    def add_many(self, evs: list[Evidence]) -> int:
        """批量写入证据，返回写入条数。"""
        # 逐条 add 以复用编号生成与去重逻辑
        for e in evs:
            self.add(e)
        return len(evs)

    def get(self, evidence_id: str) -> dict | None:
        """按证据编号取单条，不存在返回 None。"""
        # 用参数化查询避免注入；fetchone 无结果返回 None
        with self._conn() as c:
            r = c.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        return dict(r) if r else None

    def query(self, stock_code: str | None = None, year: int | None = None,
              fact_type: str | None = None, verification_status: str | None = None,
              source_type: str | None = None, doc_type: str | None = None,
              document_id: str | None = None) -> list[dict]:
        """按可选维度过滤证据，按 股票代码/报告年/事实类型 排序返回。

        year 同时匹配 year 与 report_year（兼容新旧列），保证迁移期不漏数据。
        """
        sql, args = "SELECT * FROM evidence WHERE 1=1", []
        # 动态拼接过滤条件（全部参数化，避免 SQL 注入）
        if stock_code:
            sql += " AND stock_code=?"; args.append(str(stock_code).zfill(6))
        if year is not None:
            sql += " AND (year=? OR report_year=?)"; args += [str(year), str(year)]
        if fact_type:
            sql += " AND fact_type=?"; args.append(fact_type)
        if verification_status:
            sql += " AND verification_status=?"; args.append(verification_status)
        if source_type:
            sql += " AND source_type=?"; args.append(source_type)
        if doc_type:
            sql += " AND doc_type=?"; args.append(doc_type)
        if document_id:
            sql += " AND document_id=?"; args.append(document_id)
        with self._conn() as c:
            rows = c.execute(sql + " ORDER BY stock_code, report_year, fact_type", args).fetchall()
        return [dict(r) for r in rows]

    def confirmed(self, stock_code: str, year: int | None = None) -> list[dict]:
        """取已确认（verification_status=confirmed）的证据，供数据池/计算使用。"""
        return self.query(stock_code, year, verification_status="confirmed")

    def pending(self) -> list[dict]:
        """取待人工处理证据：候选状态或存在冲突的解析结果。"""
        # 候选（未确认）与冲突（resolution_status=conflict）都需人工裁决
        with self._conn() as c:
            rows = c.execute("SELECT * FROM evidence "
                             "WHERE verification_status='candidate' OR resolution_status='conflict' "
                             "ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def update_status(self, evidence_id: str, status: str, action: str = "",
                      reviewer: str = "", new_value=None) -> None:
        """更新审核状态；传入 new_value 时按数值/文本分流写入并标记 manual_edit。"""
        sets = ["verification_status=?", "reviewer_action=?", "reviewer=?", "reviewed_at=?"]
        args = [status, action, reviewer, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        if new_value is not None:
            # 人工修正值：数值写入 value_num，非数值写入 value_text，并标记 manual_edit
            sets += ["value_num=?", "value_text=?", "extraction_method=?"]
            try:
                args += [str(float(new_value)), "", "manual_edit"]
            except (TypeError, ValueError):
                args += ["", str(new_value), "manual_edit"]
        args.append(evidence_id)
        with self._conn() as c:
            c.execute(f"UPDATE evidence SET {', '.join(sets)} WHERE evidence_id=?", args)

    def mark_resolution(self, evidence_id: str, status: str,
                        supersedes_evidence_id: str = "") -> None:
        """记录解析结论（如冲突的采用/覆盖），可指向被其取代的旧证据。"""
        with self._conn() as c:
            c.execute("UPDATE evidence SET resolution_status=?, supersedes_evidence_id=? "
                      "WHERE evidence_id=?", (status, supersedes_evidence_id, evidence_id))

    def set_import_mode(self, evidence_id: str, mode: str) -> None:
        """设置采用模式（authoritative 允许覆盖画像）。"""
        with self._conn() as c:
            c.execute("UPDATE evidence SET import_mode=? WHERE evidence_id=?", (mode, evidence_id))

    def export_jsonl(self, path: str | Path) -> int:
        """把全部证据导出为 JSONL（每行一条），返回导出条数。"""
        rows = self.query()
        path = Path(path)
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                # 每行一条 JSON，ensure_ascii=False 保留中文可读性
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return len(rows)

    # ---------------- Historical Solution ----------------

    def save_solution(self, sol: dict) -> str:
        """写入/覆盖一条历史方案（按 solution_id 唯一）；复杂字段序列化为 JSON 文本。"""
        # 复制入参，避免修改调用方传入的 dict
        sol = {**sol}
        sol.setdefault("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if not sol.get("solution_id"):
            sol["solution_id"] = f"SOL{datetime.now().strftime('%Y%m%d%H%M%S')}"
        for k in ("fact_conditions", "policies", "ai_proposal", "external_evidence",
                  "human_review", "evidence_chain"):
            if not isinstance(sol.get(k), str):
                # 复杂结构统一 JSON 序列化，保证列类型为 TEXT
                sol[k] = json.dumps(sol.get(k), ensure_ascii=False, default=str)
        cols = [c for c in SOLUTION_FIELDS]
        vals = [sol.get(c) for c in cols]
        with self._conn() as c:
            c.execute(f"INSERT OR REPLACE INTO solutions ({', '.join(cols)}) "
                      f"VALUES ({', '.join('?' * len(cols))})", vals)
        return sol["solution_id"]

    def list_solutions(self, stock_code: str | None = None, review_status: str | None = None) -> list[dict]:
        """按股票代码/审核状态过滤列出历史方案。"""
        sql, args = "SELECT * FROM solutions WHERE 1=1", []
        if stock_code:
            sql += " AND stock_code=?"; args.append(str(stock_code).zfill(6))
        if review_status:
            sql += " AND review_status=?"; args.append(review_status)
        with self._conn() as c:
            rows = c.execute(sql + " ORDER BY id", args).fetchall()
        return [dict(r) for r in rows]

    def update_solution_review(self, solution_id: str, review_status: str,
                               human_review: dict | str) -> None:
        """更新方案的审核状态与人工审核记录（dict 自动序列化）。"""
        hr = human_review if isinstance(human_review, str) else json.dumps(human_review, ensure_ascii=False)
        with self._conn() as c:
            c.execute("UPDATE solutions SET review_status=?, human_review=? WHERE solution_id=?",
                      (review_status, hr, solution_id))

    def delete_solution(self, solution_id: str) -> int:
        """删除指定方案，返回受影响行数（0 表示不存在）。"""
        with self._conn() as c:
            cur = c.execute("DELETE FROM solutions WHERE solution_id=?", (solution_id,))
            return cur.rowcount


def get_store(path: str | Path | None = None) -> EvidenceStore:
    """工厂函数：按路径获取 EvidenceStore 实例（默认库）。"""
    return EvidenceStore(path)
