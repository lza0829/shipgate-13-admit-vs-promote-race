from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.common import now_str

router = APIRouter(tags=["对账"])

ACTIVE = ("已入闸", "晋升中", "已放行", "回滚中")


@router.get("/对账")
def reconcile():
    with db() as conn:
        now = now_str()
        release_stats = {
            "申请数": conn.execute("SELECT COUNT(*) AS c FROM releases").fetchone()["c"],
            "已放行": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已放行'"
            ).fetchone()["c"],
            "已回滚": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已回滚'"
            ).fetchone()["c"],
            "在途": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status IN (?,?,?,?)",
                ACTIVE,
            ).fetchone()["c"],
        }
        version_vs_instance = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境,
                       v.version AS 名册版本,
                       GROUP_CONCAT(i.version, ',') AS 实例版本们,
                       SUM(CASE WHEN i.health='探针红' THEN 1 ELSE 0 END) AS 红灯数
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                LEFT JOIN instances i ON i.app_id = v.app_id AND i.env_id = v.env_id
                GROUP BY v.app_id, v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        promote_count = conn.execute("SELECT COUNT(*) AS c FROM promote_events").fetchone()["c"]

        # ---- 争用一致性核查：所有口径必须认同一个结局 ----
        checks: list[dict] = []

        # 1. 不得待入闸却挂着晋升事件
        bad_pending = rows_to_list(
            conn.execute(
                """
                SELECT r.id AS 编号, r.serial AS 发版单号, r.status AS 状态,
                       (SELECT COUNT(*) FROM promote_events p WHERE p.release_id=r.id) AS 事件数
                FROM releases r
                WHERE r.status='待入闸'
                  AND EXISTS (SELECT 1 FROM promote_events p WHERE p.release_id=r.id)
                """
            ).fetchall()
        )
        checks.append({
            "项": "待入闸不得挂晋升事件",
            "通过": not bad_pending,
            "明细": bad_pending or "无",
        })

        # 2. 晋升事件序号必须从 1 连续、不重号（唯一索引已兜底重号，这里查断号）
        gap_rows = rows_to_list(
            conn.execute(
                """
                SELECT p.release_id AS 编号, r.serial AS 发版单号,
                       GROUP_CONCAT(p.seq, ',') AS 序号列
                FROM promote_events p JOIN releases r ON r.id = p.release_id
                GROUP BY p.release_id
                HAVING COUNT(*) != MAX(p.seq) OR MIN(p.seq) != 1
                """
            ).fetchall()
        )
        checks.append({
            "项": "晋升事件序号连续",
            "通过": not gap_rows,
            "明细": gap_rows or "无",
        })

        # 3. 在途/已放行单的当前环境必须等于末条晋升事件落点（入闸单落测）
        drift = rows_to_list(
            conn.execute(
                """
                SELECT r.id AS 编号, r.serial AS 发版单号, r.status AS 状态,
                       r.current_env_id AS 当前环境编号,
                       (SELECT p.to_env_id FROM promote_events p
                         WHERE p.release_id=r.id ORDER BY p.seq DESC LIMIT 1) AS 末事件环境
                FROM releases r
                WHERE r.status IN ('已入闸','晋升中','已放行','回滚中')
                """
            ).fetchall()
        )
        drift_bad = []
        for row in drift:
            if row["末事件环境"] is None:
                # 已入闸且尚无晋升事件：当前环境应为测
                env = conn.execute(
                    "SELECT code FROM environments WHERE id=?", (row["当前环境编号"],)
                ).fetchone()
                if not env or env["code"] != "测":
                    drift_bad.append(row)
            elif row["末事件环境"] != row["当前环境编号"]:
                drift_bad.append(row)
        checks.append({
            "项": "当前环境与末晋升事件对齐",
            "通过": not drift_bad,
            "明细": drift_bad or "无",
        })

        # 4. 名册版本须与在途单当前环境落点一致
        roster_bad = rows_to_list(
            conn.execute(
                """
                SELECT r.id AS 编号, r.serial AS 发版单号, e.code AS 环境,
                       r.target_version AS 目标版本, v.version AS 名册版本
                FROM releases r
                JOIN environments e ON e.id = r.current_env_id
                LEFT JOIN app_env_versions v
                  ON v.app_id=r.app_id AND v.env_id=r.current_env_id
                WHERE r.status IN ('已入闸','晋升中','已放行','回滚中')
                  AND (v.version IS NULL OR v.version != r.target_version)
                """
            ).fetchall()
        )
        checks.append({
            "项": "名册版本跟齐当前环境",
            "通过": not roster_bad,
            "明细": roster_bad or "无",
        })

        # 5. 超时在途锁（进程硬杀残留，应由后来者 TTL 接管）
        stale_locks = rows_to_list(
            conn.execute(
                """
                SELECT l.release_id AS 编号, r.serial AS 发版单号, l.stage AS 阶段,
                       l.held_at AS 持锁时间, l.expires_at AS 过期时间
                FROM release_locks l JOIN releases r ON r.id=l.release_id
                WHERE l.expires_at < ?
                ORDER BY l.expires_at
                """,
                (now,),
            ).fetchall()
        )
        checks.append({
            "项": "无超时僵锁",
            "通过": not stale_locks,
            "明细": stale_locks or "无",
        })

        # 6. 总览在途口径：挂着晋升事件的单必须处于在途/终态集合，绝不能停在待入闸。
        #    注意：在途登记（release_locks）是合法瞬态——入闸进行中的待入闸单本就允许持锁，
        #    其残留由 #5「无超时僵锁」接管，不在此判幽灵。
        ghost = rows_to_list(
            conn.execute(
                """
                SELECT r.id AS 编号, r.serial AS 发版单号, r.status AS 状态,
                       (SELECT COUNT(*) FROM promote_events p WHERE p.release_id=r.id) AS 事件数
                FROM releases r
                WHERE r.status NOT IN ('已入闸','晋升中','已放行','回滚中')
                  AND EXISTS (SELECT 1 FROM promote_events p WHERE p.release_id=r.id)
                """
            ).fetchall()
        )
        checks.append({
            "项": "总览在途口径一致",
            "通过": not ghost,
            "明细": ghost or "无",
        })

        locks = rows_to_list(
            conn.execute(
                """
                SELECT l.release_id AS 编号, r.serial AS 发版单号, l.stage AS 阶段,
                       l.token AS 令牌, l.held_at AS 持锁时间, l.expires_at AS 过期时间,
                       CASE WHEN l.expires_at < ? THEN 1 ELSE 0 END AS 已超时
                FROM release_locks l JOIN releases r ON r.id=l.release_id
                ORDER BY l.held_at
                """,
                (now,),
            ).fetchall()
        )
        contentions = rows_to_list(
            conn.execute(
                """
                SELECT id AS 编号, ref_id AS 关联编号, action AS 动作,
                       created_at AS 时间, note AS 争用戳
                FROM audit_events
                WHERE kind='争用' ORDER BY id DESC LIMIT 100
                """
            ).fetchall()
        )

        return {
            "发版统计": release_stats,
            "晋升事件数": promote_count,
            "名册与实例": version_vs_instance,
            "在途登记": locks,
            "争用记录": contentions,
            "一致性检查": checks,
            "全部通过": all(c["通过"] for c in checks),
        }
