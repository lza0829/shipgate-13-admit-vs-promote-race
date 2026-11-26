from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.common import RELEASE_SELECT

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        counts = {}
        for name in ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭"):
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status=?", (name,)
            ).fetchone()["c"]
        active = rows_to_list(
            conn.execute(
                RELEASE_SELECT
                + " WHERE r.status IN ('已入闸','晋升中','已放行','回滚中') ORDER BY r.id DESC"
            ).fetchall()
        )
        versions = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境, e.name AS 环境名,
                       v.version AS 版本, v.updated_at AS 更新时间
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        health = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境, i.version AS 版本,
                       i.health AS 健康, i.release_id AS 发版单编号, i.updated_at AS 更新时间
                FROM instances i
                JOIN apps a ON a.id = i.app_id
                JOIN environments e ON e.id = i.env_id
                ORDER BY a.id, e.sort_order, i.id
                """
            ).fetchall()
        )
        in_flight_locks = rows_to_list(
            conn.execute(
                """
                SELECT l.release_id AS 编号, r.serial AS 发版单号, r.status AS 状态,
                       l.stage AS 阶段, l.held_at AS 持锁时间, l.expires_at AS 过期时间
                FROM release_locks l JOIN releases r ON r.id = l.release_id
                ORDER BY l.held_at
                """
            ).fetchall()
        )
        contentions = rows_to_list(
            conn.execute(
                """
                SELECT id AS 编号, ref_id AS 关联编号, action AS 动作,
                       created_at AS 时间, note AS 争用戳
                FROM audit_events WHERE kind='争用' ORDER BY id DESC LIMIT 20
                """
            ).fetchall()
        )
        return {
            "分布": counts,
            "在途": active,
            "环境版本": versions,
            "实例": health,
            "在途登记": in_flight_locks,
            "争用记录": contentions,
        }
