"""反应机理可视化 本机端到端测试（真实 RDKit + 真实 HTTP 层）。

## 为什么现在能在本机跑（而非必须容器内）

计划与模块文档曾把"反应帧生成"标定为由 ``scripts/validate_reactions.py``
**容器内 E2E**，因为当时默认认为本机 RDKit 被应用控制策略拦截。
但本机隔离 venv（``.venv-xuezhi312``）实际装上了 ``rdkit 2026.03.6``，
故可在主机直接跑**真实**帧生成 + 真实端点，无需 Docker —— 这正是用户要求的"本机 E2E"。

## 为什么只挂 reaction 路由、不拉整个 app

- 跑全量 ``app.main:app`` 会触发 embedding 预热等 lifespan 重活（且本机模型可能
  缺失 / 需网络），与"机理端点不依赖 LLM / 向量库"的设计相悖。
- 最小 ``FastAPI`` + ``TestClient`` 已是干净的**端点级 E2E**：真实 RDKit 坐标生成
  + 真实 HTTP 信封 + 真实库数据，覆盖本模块全部关键链路。

## 覆盖

- 真实反应 ID → 200 + ok=true + data 结构完整（schema/name/notes/frames）
- 帧数 = 请求值；每帧原子数一致；坐标有限（非 NaN/inf）
- 未收录 ID → 200 + ok=false + reaction_not_found（不抛 5xx）
- **标注与帧自洽**：从首/末帧推导键集合，应恰好体现 breaking（首有末无）
  与 forming（首无末有）—— 这是"标注既没被忽略、也没被伪造"的端到端证据
  （标注在库定义、帧在接口输出，二者必须一致）
- 三条 draft 反应都能在本机真实 RDKit 下完整生成

运行::

    PYTHONPATH=backend .venv-xuezhi312/Scripts/python.exe -m pytest backend/tests/reaction/test_e2e.py -q
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.reaction.library import ReactionLibrary
from app.reaction.routes import router

# 最小 app：只挂 reaction 路由，不触发 embedding / agent 的 lifespan 重活。
app = FastAPI()
app.include_router(router)

REACTION_ID = "addition-ethene-bromine"
_DATA = Path(__file__).resolve().parents[3] / "data" / "reactions" / "candidates.json"


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def _pair(a: int, b: int) -> tuple[int, int]:
    return (min(a, b), max(a, b))


def test_endpoint_returns_real_frames(client: TestClient) -> None:
    """真实 RDKit 帧生成 + 端点信封正确。"""
    resp = client.post(
        "/api/v1/reaction/mechanism",
        json={"reaction_id": REACTION_ID, "frame_count": 12, "fps": 10},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["error"] is None
    data = body["data"]
    # 接口 schema 与教学字段非空
    assert data["schema"] == "reaction-mechanism/v1"
    assert data["name"]
    assert data["transition_note"]
    assert data["highlight_note"]
    # 帧数 = 请求值
    assert data["frame_count"] == 12
    frames = data["frames"]
    assert len(frames) == 12
    # 每帧原子数一致（同一分子，原子集不变）
    n_atoms = len(frames[0]["atoms"])
    assert n_atoms > 0
    for f in frames:
        assert len(f["atoms"]) == n_atoms
    # 坐标有限（真实 3D 坐标，非 NaN/inf）
    for a in frames[0]["atoms"]:
        assert all(abs(v) < 1e4 for v in (a["x"], a["y"], a["z"]))


def test_endpoint_not_found_is_200_ok_false(client: TestClient) -> None:
    """未收录 id → 200 + ok=false + reaction_not_found（不抛 5xx）。"""
    resp = client.post("/api/v1/reaction/mechanism", json={"reaction_id": "nope-not-real"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "reaction_not_found"


def test_breaking_forming_annotation_consistent_with_frames(client: TestClient) -> None:
    """标注的断键 / 成键与端点返回的帧数据自洽。

    标注在库定义里、帧在接口输出里，二者必须一致。从首 / 末帧推导键集合，
    应恰好体现 breaking（首有末无）与 forming（首无末有）。
    """
    defn = ReactionLibrary.load(_DATA).by_id(REACTION_ID)
    assert defn is not None
    breaking = {_pair(*b) for b in defn.breaking_bonds}
    forming = {_pair(*b) for b in defn.forming_bonds}

    resp = client.post("/api/v1/reaction/mechanism", json={"reaction_id": REACTION_ID})
    data = resp.json()["data"]

    # 帧中"可见键" = opacity 足够大（前端以 0.02 为可见阈值，不可见键仍留在
    # bonds 列表里供插值，但不该被算作该帧"存在"的键）。
    def visible_bonds(frame: dict) -> set[tuple[int, int]]:
        return {_pair(b["a"], b["b"]) for b in frame["bonds"] if b["opacity"] > 0.02}

    first, last = visible_bonds(data["frames"][0]), visible_bonds(data["frames"][-1])
    for b in breaking:
        assert b in first, f"标注断键 {b} 应在首帧可见"
        assert b not in last, f"标注断键 {b} 应在末帧消失"
    for b in forming:
        assert b not in first, f"标注成键 {b} 应在首帧不可见（opacity≈0）"
        assert b in last, f"标注成键 {b} 应在末帧可见（opacity≈1）"

    # 进一步：forming 键的 opacity 应在首帧≈0、末帧≈1（动画渐变），而非恒定 ——
    # 这能抓出"标注键根本没被帧生成器处理"的静默失效。
    if forming:
        b_first = next(
            b for b in data["frames"][0]["bonds"] if _pair(b["a"], b["b"]) in forming
        )
        b_last = next(
            b for b in data["frames"][-1]["bonds"] if _pair(b["a"], b["b"]) in forming
        )
        assert b_first["opacity"] < 0.5 < b_last["opacity"], (
            f"成键 {forming} 透明度应从近 0 渐变到近 1，实得 "
            f"{b_first['opacity']}→{b_last['opacity']}"
        )


def test_all_three_draft_reactions_generate(client: TestClient) -> None:
    """三条 draft 反应都能在本机真实 RDKit 下完整生成（不只第一条）。"""
    ids = [
        "addition-ethene-bromine",
        "substitution-sn2-bromoethane-hydroxide",
        "esterification-acetic-acid-ethanol",
    ]
    for rid in ids:
        resp = client.post("/api/v1/reaction/mechanism", json={"reaction_id": rid})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True, f"{rid} 生成失败: {body.get('error')}"
        assert len(body["data"]["frames"]) >= 2
