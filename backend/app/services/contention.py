"""入闸/晋升/回滚写口的争用收口层。

敲定口径（全站唯一结局）：**以入闸完成前拒绝晋升为准**。
- 发版单未到「已入闸」时，晋升整笔失败：不改环境、不写晋升事件、不动名册，
  总览不得假装跃迁；失败带「争用戳」并落审计，且可安全重试（拒绝本身不占幂等令牌）。
- 每个写口的业务落点在单个 BEGIN IMMEDIATE 事务内完成「读状态 → 条件流转 →
  写全部落点」，同成败、可重放，失败整笔撤到争用前，结构上杜绝半截。
- 进程内每单发版单一把串行锁 + 持久化在途登记（release_locks，带 TTL）。
  登记在业务事务之前独立提交，故进程被硬杀时僵锁真实残留，由后来者在 TTL 到期后
  接管并留审计；普通业务异常则在 except 中主动释放登记。
- 幂等令牌（Idempotency-Key）把双回执收口到单一结局：同令牌同请求只执行一次，
  重放返回同一落账结果；同令牌不同请求体直接拒绝。
"""
from __future__ import annotations
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from fastapi import HTTPException

from app.db import db, write_tx, row_to_dict
from app.services.common import audit, get_release, get_setting, now_str

_TIME_FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_TTL_SECONDS = 60

# 进程内串行锁：同一发版单的写口在本进程内严格排队，SQLite 的 BEGIN IMMEDIATE
# 再兜底跨进程/跨连接的单写者串行化。
_in_process_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


class ConflictOutcome(Exception):
    """争用收口结局：携带争用戳与可直接回给客户端的载荷。"""

    def __init__(self, stamp: str, payload: dict[str, Any]):
        super().__init__(stamp)
        self.stamp = stamp
        self.payload = payload


def _process_lock(release_id: int) -> threading.Lock:
    with _locks_guard:
        lock = _in_process_locks.get(release_id)
        if lock is None:
            lock = threading.Lock()
            _in_process_locks[release_id] = lock
        return lock


def _stamp(release_id: int, stage: str) -> str:
    return f"CT-{release_id}-{stage}-{uuid.uuid4().hex[:10]}"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, _TIME_FMT)


def _ttl_seconds(conn) -> int:
    try:
        return max(0, int(get_setting(conn, "在途锁TTL秒", str(DEFAULT_TTL_SECONDS))))
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS


def _record_contention(release_id: int, stage: str, stamp: str) -> None:
    """争用拒绝的审计单独落账：业务事务已整笔回滚，戳仍须可核。"""
    with db() as conn:
        audit(conn, "争用", release_id, f"{stage}争用拒绝", stamp)


def _release_lock(release_id: int) -> None:
    """业务异常（非进程硬杀）后主动释放在途登记。"""
    with db() as conn:
        conn.execute("DELETE FROM release_locks WHERE release_id=?", (release_id,))


def _acquire_lock_row(conn, release_id: int, stage: str, token: str | None) -> None:
    """在途登记：活锁跨阶段即收口；僵锁到期可接管。与登记同事务。"""
    row = conn.execute(
        "SELECT * FROM release_locks WHERE release_id=?", (release_id,)
    ).fetchone()
    now = now_str()
    expires = (datetime.now() + timedelta(seconds=_ttl_seconds(conn))).strftime(_TIME_FMT)
    if row is None:
        conn.execute(
            """
            INSERT INTO release_locks(release_id, stage, token, held_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (release_id, stage, token, now, expires),
        )
        return

    if _parse(row["expires_at"]) > datetime.now():
        stamp = _stamp(release_id, stage)
        raise ConflictOutcome(
            stamp,
            {
                "争用戳": stamp,
                "结局": "拒绝",
                "口径": "入闸完成前拒绝晋升",
                "占用阶段": row["stage"],
                "在途": True,
                "说明": f"发版单正处于{row['stage']}在途，{stage}整笔失败：未改环境、未写事件、未动名册",
            },
        )

    # TTL 到期：接管僵锁并留痕
    conn.execute(
        """
        UPDATE release_locks SET stage=?, token=?, held_at=?, expires_at=?
        WHERE release_id=?
        """,
        (stage, token, now, expires, release_id),
    )
    audit(conn, "争用", release_id, f"{stage}接管超时在途锁", row["token"] or "无令牌")


def gate_pending_release(conn, release_id: int, stage: str):
    """晋升前的权威状态闸门（读必须发生在写事务内）。"""
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "发版单不存在")
    if r["status"] == "待入闸":
        stamp = _stamp(release_id, stage)
        raise ConflictOutcome(
            stamp,
            {
                "争用戳": stamp,
                "结局": "拒绝",
                "口径": "入闸完成前拒绝晋升",
                "发版单状态": "待入闸",
                "在途": False,
                "说明": "未到已入闸，晋升整笔失败：不改环境、不写晋升事件、不动名册，总览不跃迁；入闸完成后可重试",
            },
        )
    if r["status"] not in ("已入闸", "晋升中"):
        raise HTTPException(400, "当前状态不可晋升")
    return r


def _lookup_idempotency(token: str):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM idempotency_records WHERE token=?", (token,)
        ).fetchone()


def guarded_write(
    release_id: int,
    stage: str,
    token: str | None,
    fingerprint: str,
    work: Callable[[Any], dict],
) -> dict:
    """串行化 + 在途登记 + 单业务事务 + 幂等重放的统一写口。

    三段式：
      1. 幂等回放（只读）；
      2. 在途登记独立提交（先于业务，崩溃则残留待 TTL 接管）；
      3. 业务即时写事务（全部落点同成败）；
      4. 成功后写幂等记录并释放登记。
    """
    lock = _process_lock(release_id)
    lock.acquire()
    try:
        # 1. 幂等回放
        if token:
            rec = _lookup_idempotency(token)
            if rec is not None:
                if rec["fingerprint"] != fingerprint:
                    stamp = _stamp(release_id, stage)
                    raise HTTPException(
                        409,
                        {
                            "争用戳": stamp,
                            "结局": "拒绝",
                            "口径": "入闸完成前拒绝晋升",
                            "说明": "幂等令牌已绑定另一请求体，禁止拧花复用",
                        },
                    )
                return json.loads(rec["response_json"])

        # 2. 在途登记（独立即时事务，提交后对旁读可见）
        #    先验存在性：对不存在的发版单应回 404，而不是插登记时撞外键成 500
        with db() as pre:
            if pre.execute(
                "SELECT 1 FROM releases WHERE id=?", (release_id,)
            ).fetchone() is None:
                raise HTTPException(404, "发版单不存在")
        try:
            with write_tx() as conn:
                _acquire_lock_row(conn, release_id, stage, token)
        except ConflictOutcome as outcome:
            _record_contention(release_id, stage, outcome.stamp)
            raise HTTPException(409, outcome.payload)

        # 3. 业务写事务：全部落点同成败
        try:
            with write_tx() as conn:
                result = work(conn)
                # 响应快照在业务事务内重取；在途登记在阶段4才释放，
                # 故其瞬态字段在此显式置空（成功写口返回时不再持锁）
                if isinstance(result, dict) and "编号" in result:
                    result = row_to_dict(get_release(conn, release_id))
                    result["在途锁阶段"] = None
        except ConflictOutcome as outcome:
            _release_lock(release_id)
            _record_contention(release_id, stage, outcome.stamp)
            raise HTTPException(409, outcome.payload)
        except Exception:
            # 业务失败整笔回滚到争用前，在途登记同步释放（僵锁仅来自硬杀）
            _release_lock(release_id)
            raise

        # 4. 成功：幂等留痕 + 释放登记
        token_clash = False
        with write_tx() as conn:
            if token:
                try:
                    conn.execute(
                        """
                        INSERT INTO idempotency_records
                          (token, release_id, endpoint, fingerprint, status_code, response_json, created_at)
                        VALUES (?, ?, ?, ?, 200, ?, ?)
                        """,
                        (
                            token,
                            release_id,
                            stage,
                            fingerprint,
                            json.dumps(result, ensure_ascii=False),
                            now_str(),
                        ),
                    )
                except sqlite3.IntegrityError:
                    # 令牌被别的请求体占用（跨单复用），拒绝拧花；登记仍须释放
                    token_clash = True
            conn.execute("DELETE FROM release_locks WHERE release_id=?", (release_id,))
        if token_clash:
            stamp = _stamp(release_id, stage)
            raise HTTPException(
                409,
                {
                    "争用戳": stamp,
                    "结局": "拒绝",
                    "口径": "入闸完成前拒绝晋升",
                    "说明": "幂等令牌已被占用，禁止复用",
                },
            )
        return result
    finally:
        lock.release()


def make_fingerprint(release_id: int, stage: str, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"{stage}:{release_id}:{body}"
