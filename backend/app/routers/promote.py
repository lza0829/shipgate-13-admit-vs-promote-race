from __future__ import annotations
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from app.db import db, row_to_dict, rows_to_list
from app.services.common import (
    active_release_window,
    audit,
    env_by_code,
    get_release,
    get_setting,
    ordered_envs,
    set_app_env_version,
    upsert_instance,
    now_str,
)
from app.services.contention import gate_pending_release, guarded_write, make_fingerprint

router = APIRouter(tags=["晋升"])


class PromoteIn(BaseModel):
    目标环境: str
    备注: str = ""


def _promote_work(conn, release_id: int, body: "PromoteIn") -> dict:
    # 权威闸门：读发生在即时写事务内。待入闸 → 整笔拒绝（带争用戳）
    r = gate_pending_release(conn, release_id, "晋升")
    to_env = env_by_code(conn, body.目标环境)
    envs = ordered_envs(conn)
    codes = [e["code"] for e in envs]
    if body.目标环境 not in codes:
        raise HTTPException(400, "目标环境非法")
    cur_env = None
    if r["current_env_id"]:
        cur_env = conn.execute(
            "SELECT * FROM environments WHERE id=?", (r["current_env_id"],)
        ).fetchone()
    allow_skip = get_setting(conn, "允许跳级", "否") == "是"
    if cur_env:
        cur_idx = codes.index(cur_env["code"])
        to_idx = codes.index(body.目标环境)
        if to_idx <= cur_idx:
            raise HTTPException(400, "只能向更高环境晋升")
        if not allow_skip and to_idx != cur_idx + 1:
            raise HTTPException(400, "禁止跳级，须逐级晋升")
    else:
        if body.目标环境 != "测":
            raise HTTPException(400, "未落地时只能先入测")

    win = active_release_window(conn, to_env["id"])
    hard = get_setting(conn, "维护窗硬拒", "是") == "是"
    if hard and not win:
        raise HTTPException(400, "目标环境放行窗已关，禁止晋升")

    now = now_str()
    # 事件序号在事务内取本单当前最大序号 +1，唯一索引兜底；事务回滚则序号不留号
    next_seq = (
        conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM promote_events WHERE release_id=?",
            (release_id,),
        ).fetchone()["n"]
    )
    conn.execute(
        """
        INSERT INTO promote_events(release_id, seq, from_env_id, to_env_id, decision, created_at, note)
        VALUES (?, ?, ?, ?, '放行', ?, ?)
        """,
        (release_id, next_seq, r["current_env_id"], to_env["id"], now, body.备注),
    )
    new_status = "已放行" if body.目标环境 == "产" else "晋升中"
    released_at = now if body.目标环境 == "产" else r["released_at"]
    cur = conn.execute(
        """
        UPDATE releases SET status=?, current_env_id=?, released_at=?
        WHERE id=? AND status IN ('已入闸','晋升中')
        """,
        (new_status, to_env["id"], released_at, release_id),
    )
    if cur.rowcount != 1:
        # 条件更新落空：状态已被另一写口改走，事件与状态同事务回滚，不留半截
        raise HTTPException(409, "晋升争用收口：发版单状态已变更，整笔失败")
    set_app_env_version(conn, r["app_id"], to_env["id"], r["target_version"])
    upsert_instance(conn, r["app_id"], to_env["id"], r["target_version"], "探针绿", release_id)
    audit(
        conn,
        "晋升",
        release_id,
        f"晋升到{body.目标环境}",
        f"事件序号{next_seq} {body.备注}".strip(),
    )
    return row_to_dict(get_release(conn, release_id))


@router.post("/发版单/{release_id}/晋升")
def promote(
    release_id: int,
    body: PromoteIn,
    idempotency_key: str | None = Header(default=None),
):
    return guarded_write(
        release_id,
        stage="晋升",
        token=idempotency_key,
        fingerprint=make_fingerprint(release_id, "晋升", body.model_dump()),
        work=lambda conn: _promote_work(conn, release_id, body),
    )


@router.get("/发版单/{release_id}/晋升事件")
def list_promote_events(release_id: int):
    with db() as conn:
        return rows_to_list(
            conn.execute(
                """
                SELECT p.id AS 编号, p.seq AS 序号, p.release_id AS 发版单编号,
                       ec.code AS 来源环境, tc.code AS 目标环境,
                       p.decision AS 裁决, p.created_at AS 时间, p.note AS 备注
                FROM promote_events p
                LEFT JOIN environments ec ON ec.id = p.from_env_id
                JOIN environments tc ON tc.id = p.to_env_id
                WHERE p.release_id=?
                ORDER BY p.seq
                """,
                (release_id,),
            ).fetchall()
        )
