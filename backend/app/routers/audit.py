from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(类别: str | None = None):
    with db() as conn:
        sql = """
            SELECT id AS 编号, kind AS 类别, ref_id AS 关联编号,
                   action AS 动作, created_at AS 时间, note AS 备注
            FROM audit_events
        """
        params: tuple = ()
        if 类别:
            sql += " WHERE kind=?"
            params = (类别,)
        sql += " ORDER BY id DESC LIMIT 200"
        return rows_to_list(conn.execute(sql, params).fetchall())
