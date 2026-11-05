"""Chart drawings: per-user, per-ticker storage."""
import pytest
from fastapi.testclient import TestClient

from app import db
from tests.conftest import authenticate


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "draw.db"))
    import importlib
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c


TRENDLINE = {
    "id": "d1", "kind": "trendline", "color": "#f0b429",
    "points": [{"time": "2026-01-02", "price": 100.0},
               {"time": "2026-03-02", "price": 140.0}],
}


# ---- db layer ----

def test_missing_drawings_read_as_empty(conn):
    assert db.get_drawings(conn, 1, "NVDA") == {"shapes": [], "updated_at": None}


def test_roundtrip_and_overwrite(conn):
    db.save_drawings(conn, 1, "nvda", [TRENDLINE], "2026-07-27T00:00:00+00:00")
    got = db.get_drawings(conn, 1, "NVDA")   # ticker is case-insensitive
    assert got["shapes"] == [TRENDLINE]
    assert got["updated_at"] == "2026-07-27T00:00:00+00:00"

    db.save_drawings(conn, 1, "NVDA", [], "2026-07-28T00:00:00+00:00")
    assert db.get_drawings(conn, 1, "NVDA")["shapes"] == []


def test_drawings_are_per_user_and_per_ticker(conn):
    db.save_drawings(conn, 1, "NVDA", [TRENDLINE], "t")
    assert db.get_drawings(conn, 2, "NVDA")["shapes"] == []   # other user
    assert db.get_drawings(conn, 1, "AAPL")["shapes"] == []   # other ticker


def test_corrupt_json_degrades_to_empty(conn):
    conn.execute(
        "INSERT INTO drawings (user_id, ticker, shapes_json, updated_at) VALUES (?,?,?,?)",
        (1, "NVDA", "{not json", "t"),
    )
    conn.commit()
    assert db.get_drawings(conn, 1, "NVDA")["shapes"] == []


# ---- API layer ----

def test_drawings_api_roundtrip(client):
    assert client.get("/api/drawings/NVDA").json()["shapes"] == []
    saved = client.put("/api/drawings/NVDA", json={"shapes": [TRENDLINE]}).json()
    assert saved["shapes"] == [TRENDLINE] and saved["updated_at"]
    assert client.get("/api/drawings/NVDA").json()["shapes"] == [TRENDLINE]


def test_drawings_api_requires_auth(client):
    client.post("/api/auth/logout")
    assert client.get("/api/drawings/NVDA").status_code == 401


def test_drawings_api_rejects_an_absurd_payload(client):
    many = [{**TRENDLINE, "id": f"d{i}"} for i in range(201)]
    assert client.put("/api/drawings/NVDA", json={"shapes": many}).status_code == 400


# ---- drafts: named snapshots of a ticker's drawings (Task 15) ----

def _draft(title="Breakout setup", **extra):
    return {"title": title, "description": "watching the flag", "timeframe": "1d",
            "shapes": [TRENDLINE], **extra}


def test_draft_db_crud_and_owner_scope(conn):
    d = db.create_drawing_draft(conn, 1, "nvda", "A", "desc", "1d", [TRENDLINE], "t1")
    assert d["ticker"] == "NVDA" and d["shapes"] == [TRENDLINE] and d["created_at"] == "t1"
    assert [x["id"] for x in db.list_drawing_drafts(conn, 1, "NVDA")] == [d["id"]]
    # another user can neither see, edit nor delete it
    assert db.list_drawing_drafts(conn, 2, "NVDA") == []
    assert db.get_drawing_draft(conn, 2, d["id"]) is None
    assert db.update_drawing_draft(conn, 2, d["id"], "t2", title="hijack") is None
    assert db.delete_drawing_draft(conn, 2, d["id"]) is False
    up = db.update_drawing_draft(conn, 1, d["id"], "t2", title="B", shapes=[])
    assert up["title"] == "B" and up["shapes"] == [] and up["description"] == "desc"
    assert up["updated_at"] == "t2" and up["created_at"] == "t1"
    assert db.delete_drawing_draft(conn, 1, d["id"]) is True
    assert db.get_drawing_draft(conn, 1, d["id"]) is None


def test_drafts_list_newest_first(conn):
    a = db.create_drawing_draft(conn, 1, "NVDA", "old", "", "1d", [], "2026-01-01")
    b = db.create_drawing_draft(conn, 1, "NVDA", "new", "", "1d", [], "2026-02-01")
    assert [x["id"] for x in db.list_drawing_drafts(conn, 1, "NVDA")] == [b["id"], a["id"]]


def test_draft_corrupt_json_degrades_to_empty(conn):
    d = db.create_drawing_draft(conn, 1, "NVDA", "A", "", "1d", [], "t")
    conn.execute("UPDATE drawing_drafts SET shapes_json = '{bad' WHERE id = ?", (d["id"],))
    conn.commit()
    assert db.get_drawing_draft(conn, 1, d["id"])["shapes"] == []


def test_delete_user_sweeps_drafts(conn):
    user = db.create_user(conn, "gone@example.com", "hash", "2026-01-01T00:00:00+00:00")
    db.create_drawing_draft(conn, user.id, "NVDA", "A", "", "1d", [TRENDLINE], "t")
    assert "drawing_drafts" in db._PER_USER_TABLES
    assert db.delete_user(conn, user.id) is True
    left = conn.execute(
        "SELECT COUNT(*) FROM drawing_drafts WHERE user_id = ?", (user.id,)).fetchone()[0]
    assert left == 0


def test_drafts_api_crud(client):
    assert client.get("/api/drawings/NVDA/drafts").json() == {"drafts": []}
    r = client.post("/api/drawings/nvda/drafts", json=_draft())
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["title"] == "Breakout setup" and d["ticker"] == "NVDA" and d["shapes"] == [TRENDLINE]
    assert d["timeframe"] == "1d" and d["created_at"] and d["updated_at"]

    listed = client.get("/api/drawings/NVDA/drafts").json()["drafts"]
    assert [x["id"] for x in listed] == [d["id"]]
    assert client.get("/api/drawings/AAPL/drafts").json()["drafts"] == []   # per ticker

    r = client.put(f"/api/drawings/drafts/{d['id']}",
                   json={"title": "  Renamed  ", "description": "new"})
    assert r.status_code == 200 and r.json()["title"] == "Renamed"
    assert r.json()["shapes"] == [TRENDLINE]   # untouched fields survive a partial edit
    r = client.put(f"/api/drawings/drafts/{d['id']}", json={"shapes": []})
    assert r.json()["shapes"] == [] and r.json()["title"] == "Renamed"

    assert client.delete(f"/api/drawings/drafts/{d['id']}").status_code == 200
    assert client.get("/api/drawings/NVDA/drafts").json()["drafts"] == []
    assert client.delete(f"/api/drawings/drafts/{d['id']}").status_code == 404


def test_drafts_api_validation(client):
    post = lambda body: client.post("/api/drawings/NVDA/drafts", json=body)  # noqa: E731
    assert post(_draft(title="")).status_code == 422
    assert post(_draft(title="   ")).status_code == 422
    assert post(_draft(title="x" * 121)).status_code == 422
    assert post(_draft(title="x" * 120)).status_code == 201
    assert post(_draft(description="d" * 2001)).status_code == 422
    assert post(_draft(description="d" * 2000)).status_code == 201
    many = [{**TRENDLINE, "id": f"d{i}"} for i in range(201)]
    assert post(_draft(shapes=many)).status_code == 422
    assert post({"description": "no title"}).status_code == 422
    assert client.post("/api/drawings/NV%20DA/drafts", json=_draft()).status_code == 400
    d = post(_draft()).json()
    assert client.put(f"/api/drawings/drafts/{d['id']}", json={"title": ""}).status_code == 422
    assert client.put("/api/drawings/drafts/not-a-number", json={"title": "x"}).status_code == 422


def test_drafts_api_is_isolated_between_users(client):
    mine = client.post("/api/drawings/NVDA/drafts", json=_draft()).json()
    client.post("/api/auth/logout")
    authenticate(client, email="other@example.com")
    # the other account sees nothing and gets 404 (not 403) on the id
    assert client.get("/api/drawings/NVDA/drafts").json()["drafts"] == []
    assert client.put(f"/api/drawings/drafts/{mine['id']}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/drawings/drafts/{mine['id']}").status_code == 404
    theirs = client.post("/api/drawings/NVDA/drafts", json=_draft(title="Theirs")).json()
    titles = [x["title"] for x in client.get("/api/drawings/NVDA/drafts").json()["drafts"]]
    assert titles == ["Theirs"] and theirs["id"] != mine["id"]


def test_drafts_api_requires_auth(client):
    client.post("/api/auth/logout")
    assert client.get("/api/drawings/NVDA/drafts").status_code == 401
    assert client.post("/api/drawings/NVDA/drafts", json=_draft()).status_code == 401
    assert client.delete("/api/drawings/drafts/1").status_code == 401


def test_extended_route_returns_the_summary(client, monkeypatch):
    from app import chart_data
    monkeypatch.setattr(chart_data, "get_extended", lambda t: {
        "ticker": t, "supported": True, "as_of": "x",
        "pre": None, "post": {"price": 10.7, "time": 4}, "regular_close": 10.0})
    body = client.get("/api/chart/nvda/extended").json()
    assert body["ticker"] == "NVDA" and body["post"]["price"] == 10.7
    assert client.get("/api/chart/NV%20DA/extended").status_code == 400
