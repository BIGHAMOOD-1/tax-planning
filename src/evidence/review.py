"""人工评审（后端逻辑，与 CLI/UI 解耦）。

核心：verification_status（是否可信）+ resolution_status（是否当前采用）。
CLI 与未来 UI 共用同一状态机。
"""
from __future__ import annotations

from src.common import get_logger

log = get_logger()

ACTIONS = {
    "a": ("confirmed", "accept"),
    "accept": ("confirmed", "accept"),
    "r": ("rejected", "reject"),
    "reject": ("rejected", "reject"),
    "e": ("confirmed", "edit"),
    "edit": ("confirmed", "edit"),
}

CONFLICT_ACTIONS = {"c", "conflict"}


def pending(store) -> list[dict]:
    return store.pending()


def act(store, evidence_id: str, action: str, reviewer: str = "human", new_value=None) -> str:
    key = str(action).strip().lower()
    if key in CONFLICT_ACTIONS:
        store.mark_resolution(evidence_id, "conflict")
        log.info("Evidence %s -> 冲突标记（by %s）", evidence_id, reviewer)
        return "conflict"
    if key not in ACTIONS:
        raise ValueError(f"未知动作: {action}（可选 a/r/e/c）")
    status, action_name = ACTIONS[key]
    if key in ("e", "edit") and new_value is None:
        raise ValueError("编辑动作需要 new_value")
    store.update_status(evidence_id, status, action=action_name, reviewer=reviewer, new_value=new_value)
    log.info("Evidence %s -> %s（%s by %s）", evidence_id, status, action_name, reviewer)
    return status


def resolve_conflict(store, evidence_id: str, choice: str, reviewer: str = "human") -> str:
    """冲突解决（UI）：choice ∈ {evidence, profile}。

    - evidence（以证据为准）→ 标记 `import_mode=authoritative` 并确认；
      下次采用时，该证据可覆盖画像（manual/权威结构化）。
    - profile（保留画像）→ 驳回该证据。
    """
    ev = store.get(evidence_id)
    if not ev:
        raise ValueError(f"未找到证据 {evidence_id}")
    key = str(choice).strip().lower()
    if key in ("evidence", "e", "accept"):
        store.set_import_mode(evidence_id, "authoritative")
        store.update_status(evidence_id, "confirmed", action="resolve_accept", reviewer=reviewer)
        log.info("冲突解决 %s -> 以证据为准（authoritative，by %s）", evidence_id, reviewer)
        return "evidence"
    if key in ("profile", "p", "reject"):
        store.update_status(evidence_id, "rejected", action="resolve_keep_profile", reviewer=reviewer)
        log.info("冲突解决 %s -> 保留画像（驳回证据，by %s）", evidence_id, reviewer)
        return "profile"
    raise ValueError(f"未知选择: {choice}（可选 evidence/profile）")
