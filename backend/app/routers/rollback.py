from __future__ import annotations
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from app.db import row_to_dict
from app.services.common import (
    active_rollback_window,
    audit,
    get_release,
    now_str,
    set_app_env_version,
    upsert_instance,
)
from app.services.contention import guarded_write, make_fingerprint

router = APIRouter(tags=["回滚"])


class RollbackIn(BaseModel):
    目标版本: str
    备注: str = ""


def _rollback_work(conn, release_id: int, body: "RollbackIn") -> dict:
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if not r:
        raise HTTPException(404, "发版单不存在")
    if r["status"] not in ("已放行", "晋升中"):
        raise HTTPException(400, "当前状态不可回滚")
    if not r["current_env_id"]:
        raise HTTPException(400, "无当前环境，无法回滚")
    win = active_rollback_window(conn, r["current_env_id"])
    if not win:
        raise HTTPException(400, "回滚窗已关，禁止回滚")
    now = now_str()
    cur = conn.execute(
        """
        UPDATE releases SET status='已回滚', rolled_back_at=?, target_version=?
        WHERE id=? AND status IN ('已放行','晋升中')
        """,
        (now, body.目标版本, release_id),
    )
    if cur.rowcount != 1:
        raise HTTPException(409, "回滚争用收口：发版单状态已变更，整笔失败")
    set_app_env_version(conn, r["app_id"], r["current_env_id"], body.目标版本)
    upsert_instance(conn, r["app_id"], r["current_env_id"], body.目标版本, "探针绿", release_id)
    # 旧版本实例标红，便于对账发现残留
    conn.execute(
        """
        UPDATE instances SET health='探针红', updated_at=?
        WHERE app_id=? AND env_id=? AND version!=? AND release_id=?
        """,
        (now, r["app_id"], r["current_env_id"], body.目标版本, release_id),
    )
    audit(conn, "回滚", release_id, f"回滚到{body.目标版本}", body.备注)
    return row_to_dict(get_release(conn, release_id))


@router.post("/发版单/{release_id}/回滚")
def rollback(
    release_id: int,
    body: RollbackIn,
    idempotency_key: str | None = Header(default=None),
):
    return guarded_write(
        release_id,
        stage="回滚",
        token=idempotency_key,
        fingerprint=make_fingerprint(release_id, "回滚", body.model_dump()),
        work=lambda conn: _rollback_work(conn, release_id, body),
    )
