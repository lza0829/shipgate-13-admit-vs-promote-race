from __future__ import annotations
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 每个用例独立库：在导入 app.main 之前把 DB_PATH 指到临时目录
    from app import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "shipgate.db")
    from app.seed import seed_if_empty

    seed_if_empty()
    from app.main import app

    with TestClient(app) as c:
        yield c


def make_green_release(client: TestClient, app_id: int = 2, version: str | None = None) -> int:
    """申请一张可入闸的新单（流水线绿 + 有制品摘要），返回发版单编号。"""
    version = version or f"9.{app_id}.{abs(hash(version or str(app_id))) % 1000}"
    res = client.post(
        "/api/发版单",
        json={"应用编号": app_id, "目标版本": version, "制品摘要": "sha256:fixture"},
    )
    assert res.status_code == 200, res.text
    rid = res.json()["编号"]
    r = client.post(f"/api/发版单/{rid}/流水线", json={"流水线": "绿"})
    assert r.status_code == 200, r.text
    return rid


def get_release(client: TestClient, rid: int) -> dict:
    res = client.get(f"/api/发版单/{rid}")
    assert res.status_code == 200, res.text
    return res.json()


def count_events(client: TestClient, rid: int) -> int:
    return len(client.get(f"/api/发版单/{rid}/晋升事件").json())
