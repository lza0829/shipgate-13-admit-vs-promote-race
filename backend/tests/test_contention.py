"""入闸/晋升时序争用夹具。

覆盖：入闸抢先、晋升抢先、半截、双回执拧花；
对齐面：待入闸锁、晋升事件序号、当前环境跃迁、名册版本、总览在途、审计争用戳、对账。
"""
from __future__ import annotations
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from tests.conftest import count_events, get_release, make_green_release

TIME_FMT = "%Y-%m-%d %H:%M:%S"
ACTIVE_STATES = ("已入闸", "晋升中", "已放行", "回滚中")


def assert_surfaces_coherent(client, rid):
    """发版单详情 / 总览 / 对账 / 审计 / 名册 / 事件序号 认同一个结局。"""
    r = get_release(client, rid)

    overview = client.get("/api/总览").json()
    reconcile = client.get("/api/对账").json()
    audit_rows = client.get("/api/审计").json()
    events = client.get(f"/api/发版单/{rid}/晋升事件").json()

    # 总览在途口径
    overview_ids = {row["编号"] for row in overview["在途"]}
    if r["状态"] in ACTIVE_STATES:
        assert rid in overview_ids
    else:
        assert rid not in overview_ids

    # 待入闸绝不能挂晋升事件
    if r["状态"] == "待入闸":
        assert count_events(client, rid) == 0
        assert r["晋升事件数"] == 0

    # 事件序号从 1 连续不重号
    seqs = [e["序号"] for e in events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert r["最大事件序号"] == max(seqs or [0])

    # 当前环境跃迁与末事件落点对齐
    if events:
        last = events[-1]
        assert r["当前环境"] == last["目标环境"]

    # 名册版本跟齐当前环境
    if r["当前环境"]:
        roster = [
            v for v in overview["环境版本"]
            if v["应用"] == r["应用"] and v["环境"] == r["当前环境"]
        ]
        assert roster and roster[0]["版本"] == r["目标版本"]

    # 对账一致性全绿
    assert reconcile["全部通过"] is True
    failed = [c["项"] for c in reconcile["一致性检查"] if not c["通过"]]
    assert failed == []

    # 审计争用戳格式
    for row in audit_rows:
        if row["类别"] == "争用" and row["关联编号"] == rid:
            assert row["备注"].startswith("CT-")
    return r


def test_promote_before_admit_is_whole_rejection(client):
    """晋升抢先：未到已入闸，晋升整笔失败，任何落点不动。"""
    rid = make_green_release(client)
    before = get_release(client, rid)
    assert before["状态"] == "待入闸"

    res = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"})
    assert res.status_code == 409
    body = res.json()["detail"]
    assert body["结局"] == "拒绝"
    assert body["口径"] == "入闸完成前拒绝晋升"
    stamp = body["争用戳"]
    assert stamp.startswith("CT-")

    # 环境没动、事件没写、名册没碰、状态没变
    after = get_release(client, rid)
    assert after["状态"] == "待入闸"
    assert after["当前环境"] is None
    assert count_events(client, rid) == 0
    assert after["最近争用戳"] == stamp

    # 争用戳进审计，且可重试：随后入闸成功，再晋升成功（同一结局线收口）
    audit_rows = client.get("/api/审计?类别=争用").json()
    assert any(row["备注"] == stamp for row in audit_rows)

    admitted = client.post(f"/api/发版单/{rid}/入闸")
    assert admitted.status_code == 200
    assert get_release(client, rid)["状态"] == "已入闸"

    promoted = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"})
    assert promoted.status_code == 200
    r = assert_surfaces_coherent(client, rid)
    assert r["当前环境"] == "预"
    assert count_events(client, rid) == 1


def test_admit_then_promote_wins(client):
    """入闸抢先：入闸先落账，晋升随后正常跃迁。"""
    rid = make_green_release(client)
    res = client.post(f"/api/发版单/{rid}/入闸")
    assert res.status_code == 200
    r = get_release(client, rid)
    assert r["状态"] == "已入闸"
    assert r["当前环境"] == "测"
    assert r["入闸时间"]

    res = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"})
    assert res.status_code == 200
    r = assert_surfaces_coherent(client, rid)
    assert r["状态"] == "晋升中"
    assert r["当前环境"] == "预"


def test_full_promotion_chain_sequence_and_roster(client):
    """测→预→产：序号连续、逐次跃迁、名册跟齐、放行落账。"""
    rid = make_green_release(client)
    client.post(f"/api/发版单/{rid}/入闸")
    for idx, env in enumerate(("预", "产"), start=2):
        res = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": env})
        assert res.status_code == 200, res.text
        events = client.get(f"/api/发版单/{rid}/晋升事件").json()
        assert events[-1]["序号"] == idx - 1
        assert events[-1]["目标环境"] == env

    r = get_release(client, rid)
    assert r["状态"] == "已放行"
    assert r["当前环境"] == "产"
    assert r["放行时间"]
    assert_surfaces_coherent(client, rid)


def test_concurrent_admit_and_promote_single_outcome(client):
    """入闸入口与晋升入口同时打开对撞：只允许一个结局，无待入闸挂事件。"""
    rid = make_green_release(client)
    barrier = threading.Barrier(2)
    outcomes = {}

    def admit():
        barrier.wait()
        outcomes["入闸"] = client.post(f"/api/发版单/{rid}/入闸")

    def promote():
        barrier.wait()
        outcomes["晋升"] = client.post(
            f"/api/发版单/{rid}/晋升", json={"目标环境": "预"}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(admit), pool.submit(promote)]
        for f in futs:
            f.result()

    admit_res, promote_res = outcomes["入闸"], outcomes["晋升"]
    r = get_release(client, rid)

    if promote_res.status_code == 409:
        # 晋升抢先被拒：入闸成或排队后成，绝无事件
        assert admit_res.status_code == 200
        assert r["状态"] in ("已入闸", "晋升中")
        assert count_events(client, rid) == 0 or r["状态"] == "晋升中"
        if r["状态"] == "已入闸":
            assert count_events(client, rid) == 0
    else:
        # 入闸先完成，晋升落账
        assert promote_res.status_code == 200
        assert admit_res.status_code == 200
        assert r["状态"] == "晋升中"
        assert r["当前环境"] == "预"
        assert count_events(client, rid) == 1

    # 收口硬不变量：待入闸永远不挂晋升事件
    assert not (r["状态"] == "待入闸" and count_events(client, rid) > 0)
    assert_surfaces_coherent(client, rid)


def test_half_write_failure_leaves_nothing(client):
    """半截：晋升业务校验失败时整笔回滚，无事件、无环境跃迁、无僵锁。"""
    rid = make_green_release(client)
    client.post(f"/api/发版单/{rid}/入闸")

    # 关掉预发放行窗 → 晋升在写事件前失败
    win = client.post("/api/放行窗/切换", json={"环境": "预", "状态": "窗关"})
    assert win.status_code == 200

    res = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"})
    assert res.status_code == 400
    r = get_release(client, rid)
    assert r["状态"] == "已入闸"
    assert r["当前环境"] == "测"
    assert count_events(client, rid) == 0
    assert r["在途锁阶段"] is None

    # 重开窗后可原样重试成功（失败未占任何化石）
    client.post("/api/放行窗/切换", json={"环境": "预", "状态": "窗开"})
    res = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"})
    assert res.status_code == 200
    assert_surfaces_coherent(client, rid)


def test_double_receipt_same_token_replays_single_outcome(client):
    """双回执拧花：同令牌同请求只落一次账，重放同一结局。"""
    rid = make_green_release(client)
    token = uuid.uuid4().hex

    r1 = client.post(f"/api/发版单/{rid}/入闸", headers={"Idempotency-Key": token})
    r2 = client.post(f"/api/发版单/{rid}/入闸", headers={"Idempotency-Key": token})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()

    # 第二次没有重复入闸审计之外的副作用：仍只一次入闸、状态已入闸
    r = get_release(client, rid)
    assert r["状态"] == "已入闸"
    admit_audits = [
        a for a in client.get("/api/审计").json()
        if a["关联编号"] == rid and a["动作"] == "入闸"
    ]
    assert len(admit_audits) == 1

    # 同令牌拧到另一请求体 → 拒绝，不改账
    bad = client.post(
        f"/api/发版单/{rid}/晋升",
        json={"目标环境": "预"},
        headers={"Idempotency-Key": token},
    )
    assert bad.status_code == 409
    assert get_release(client, rid)["状态"] == "已入闸"
    assert count_events(client, rid) == 0


def test_concurrent_double_receipt_promotion(client):
    """同令牌双回执并发打晋升：只落一笔事件，序号不重不跳。"""
    rid = make_green_release(client)
    client.post(f"/api/发版单/{rid}/入闸")
    token = uuid.uuid4().hex
    barrier = threading.Barrier(2)

    def fire():
        barrier.wait()
        return client.post(
            f"/api/发版单/{rid}/晋升",
            json={"目标环境": "预"},
            headers={"Idempotency-Key": token},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: fire(), range(2)))

    assert all(r.status_code == 200 for r in results)
    assert results[0].json() == results[1].json()
    assert count_events(client, rid) == 1
    assert_surfaces_coherent(client, rid)


def test_stale_lock_takeover_after_ttl(client, tmp_path):
    """超时挂接：进程硬杀残留的僵锁，TTL 到期后由后来者接管并留痕。"""
    from app import config
    from app.db import db

    rid = make_green_release(client)
    # 伪造一笔已过期的在途登记
    expired = (datetime.now() - timedelta(seconds=5)).strftime(TIME_FMT)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO release_locks(release_id, stage, token, held_at, expires_at)
            VALUES (?, '入闸', 'ghost-token', ?, ?)
            """,
            (rid, expired, expired),
        )

    res = client.post(f"/api/发版单/{rid}/入闸")
    assert res.status_code == 200
    audits = client.get("/api/审计?类别=争用").json()
    assert any("接管超时在途锁" in a["动作"] for a in audits)
    r = get_release(client, rid)
    assert r["状态"] == "已入闸"
    assert r["在途锁阶段"] is None
    assert config.DB_PATH.exists()


def test_same_app_double_admit_second_rejected(client):
    """同应用双单入闸：第一张在途时，第二张整笔拒绝（另立口径，不动主争用）。"""
    first = make_green_release(client, app_id=2, version="7.1.0")
    second = make_green_release(client, app_id=2, version="7.2.0")
    assert client.post(f"/api/发版单/{first}/入闸").status_code == 200

    res = client.post(f"/api/发版单/{second}/入闸")
    assert res.status_code == 400
    assert "禁止并行入闸" in res.json()["detail"]
    assert get_release(client, second)["状态"] == "待入闸"
    assert_surfaces_coherent(client, first)


def test_failed_write_releases_inflight_registration(client):
    """业务失败后在途登记主动释放，不留僵锁（僵锁仅可能来自进程硬杀，走 TTL 接管）。"""
    from app.db import db

    rid = make_green_release(client)
    client.post(f"/api/发版单/{rid}/入闸")
    client.post("/api/放行窗/切换", json={"环境": "预", "状态": "窗关"})

    res = client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"})
    assert res.status_code == 400
    with db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM release_locks WHERE release_id=?", (rid,)
        ).fetchone()["c"] == 0

    # 重开窗后重试可成功（失败未占任何化石）
    client.post("/api/放行窗/切换", json={"环境": "预", "状态": "窗开"})
    assert client.post(f"/api/发版单/{rid}/晋升", json={"目标环境": "预"}).status_code == 200
    assert_surfaces_coherent(client, rid)


def test_nonexistent_release_returns_404_not_500(client):
    """不存在的发版单：写口在拿在途登记前就回 404，不撞外键。"""
    for path, body in (
        ("/api/发版单/99999/入闸", {}),
        ("/api/发版单/99999/晋升", {"目标环境": "预"}),
        ("/api/发版单/99999/回滚", {"目标版本": "1.0.0"}),
    ):
        res = client.post(path, json=body)
        assert res.status_code == 404, (path, res.status_code, res.text)
    # 没有产生任何在途登记
    from app.db import db

    with db() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM release_locks").fetchone()["c"] == 0


def test_live_lock_blocks_cross_stage_and_is_not_deleted(client):
    """硬杀残留但未超时的在途登记：跨阶段写口整笔拒绝，登记保留待 TTL。"""
    from app.db import db

    rid = make_green_release(client)
    held = datetime.now().strftime(TIME_FMT)
    future = (datetime.now() + timedelta(seconds=30)).strftime(TIME_FMT)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO release_locks(release_id, stage, token, held_at, expires_at)
            VALUES (?, '入闸', 'crash-token', ?, ?)
            """,
            (rid, held, future),
        )

    res = client.post(f"/api/发版单/{rid}/入闸")
    assert res.status_code == 409
    body = res.json()["detail"]
    assert body["在途"] is True and body["占用阶段"] == "入闸"
    # 活锁未被误删，仍在等 TTL 接管
    with db() as conn:
        row = conn.execute(
            "SELECT stage, token FROM release_locks WHERE release_id=?", (rid,)
        ).fetchone()
    assert row is not None and row["token"] == "crash-token"
    # 发版单本身未被改动
    assert get_release(client, rid)["状态"] == "待入闸"


def test_fuzz_collisions_always_coherent(client):
    """入闸/晋升入口反复同时对撞：任何调度结局下全站口径都不裂。"""
    for round_no in range(8):
        rid = make_green_release(client, app_id=2, version=f"8.{round_no}.0")
        barrier = threading.Barrier(2)

        def admit():
            barrier.wait()
            return client.post(f"/api/发版单/{rid}/入闸")

        def promote():
            barrier.wait()
            return client.post(
                f"/api/发版单/{rid}/晋升", json={"目标环境": "预"}
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            ar, pr = list(pool.map(lambda fn: fn(), (admit, promote)))

        r = get_release(client, rid)
        # 唯一允许的状态集合
        assert r["状态"] in ("待入闸", "已入闸", "晋升中")
        # 硬不变量
        assert not (r["状态"] == "待入闸" and count_events(client, rid) > 0)
        if pr.status_code == 200:
            assert r["状态"] == "晋升中"
            assert count_events(client, rid) == 1
        assert_surfaces_coherent(client, rid)
