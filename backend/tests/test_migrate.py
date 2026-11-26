"""旧库演进回归：老 promote_events 无 seq、无新表时平滑补齐且不重号。"""
from __future__ import annotations
import sqlite3


def test_legacy_schema_migrates_and_backfills_seq(tmp_path, monkeypatch):
    from app import config

    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)

    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE apps(id INTEGER PRIMARY KEY, name TEXT UNIQUE, owner TEXT DEFAULT '', status TEXT DEFAULT '启用');
        CREATE TABLE environments(id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, sort_order INTEGER);
        CREATE TABLE app_env_versions(app_id INTEGER, env_id INTEGER, version TEXT, updated_at TEXT,
          PRIMARY KEY(app_id,env_id));
        CREATE TABLE releases(id INTEGER PRIMARY KEY, serial TEXT UNIQUE, app_id INTEGER, target_version TEXT,
          artifact_digest TEXT DEFAULT '', status TEXT DEFAULT '待入闸', pipeline_status TEXT DEFAULT '未回执',
          current_env_id INTEGER, canary_ratio INTEGER DEFAULT 10, note TEXT DEFAULT '', created_at TEXT,
          admitted_at TEXT, released_at TEXT, rolled_back_at TEXT);
        CREATE TABLE promote_events(id INTEGER PRIMARY KEY, release_id INTEGER, from_env_id INTEGER,
          to_env_id INTEGER, decision TEXT, created_at TEXT, note TEXT DEFAULT '');
        CREATE TABLE release_windows(id INTEGER PRIMARY KEY, env_id INTEGER, open_at TEXT, close_at TEXT, status TEXT);
        CREATE TABLE rollback_windows(id INTEGER PRIMARY KEY, env_id INTEGER, open_at TEXT, close_at TEXT, status TEXT);
        CREATE TABLE instances(id INTEGER PRIMARY KEY, app_id INTEGER, env_id INTEGER, version TEXT,
          health TEXT DEFAULT '探针绿', release_id INTEGER, updated_at TEXT);
        CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE audit_events(id INTEGER PRIMARY KEY, kind TEXT, ref_id INTEGER, action TEXT,
          created_at TEXT, note TEXT DEFAULT '');
        INSERT INTO environments VALUES (1,'测','测试',1),(2,'预','预发',2),(3,'产','生产',3);
        INSERT INTO apps VALUES (1,'老应用','负责人','启用');
        INSERT INTO releases(id,serial,app_id,target_version,status,current_env_id,created_at)
          VALUES (1,'发202601010001',1,'1.0.0','晋升中',2,'2026-01-01 00:00:00');
        INSERT INTO promote_events(release_id,from_env_id,to_env_id,decision,created_at)
          VALUES (1,1,2,'放行','2026-01-01 00:01:00');
        INSERT INTO promote_events(release_id,from_env_id,to_env_id,decision,created_at)
          VALUES (1,2,3,'放行','2026-01-01 00:02:00');
        """
    )
    raw.commit()
    raw.close()

    from app.db import init_db, db

    init_db()
    init_db()  # 幂等：再跑一次不得报错
    with db() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(promote_events)")}
        assert "seq" in cols
        seqs = [r["seq"] for r in conn.execute("SELECT seq FROM promote_events ORDER BY id")]
        assert seqs == [1, 2]
        indexes = {r["name"] for r in conn.execute("PRAGMA index_list('promote_events')")}
        assert "ux_promote_events_release_seq" in indexes
        assert conn.execute("SELECT COUNT(*) FROM release_locks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM idempotency_records").fetchone()[0] == 0

        # 唯一约束在迁移后真正生效：重号插不进
        import pytest

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO promote_events(release_id, seq, to_env_id, decision, created_at) "
                "VALUES (1, 1, 3, '放行', '2026-01-01 00:03:00')"
            )
