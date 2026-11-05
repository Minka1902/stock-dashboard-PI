"""Multiple named watchlists per user (Task 13)."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.models import WatchItem
from tests.conftest import authenticate


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "wl.db"))
    import importlib
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c


# ---- db layer ----

def test_default_watchlist_auto_created(conn):
    lists = db.get_watchlists(conn, 1)
    assert len(lists) == 1 and lists[0].name == "My Watchlist"


def test_lists_isolate_tickers(conn):
    a = db.get_watchlists(conn, 1)[0].id
    b = db.create_watchlist(conn, 1, "Longs", "t").id
    db.add_watch(conn, 1, WatchItem(ticker="AAPL", note="", added_at="t"), a)
    db.add_watch(conn, 1, WatchItem(ticker="TSLA", note="", added_at="t"), b)
    assert [w.ticker for w in db.get_watchlist(conn, 1, a)] == ["AAPL"]
    assert [w.ticker for w in db.get_watchlist(conn, 1, b)] == ["TSLA"]
    # the same ticker may live in two lists
    db.add_watch(conn, 1, WatchItem(ticker="AAPL", note="", added_at="t"), b)
    assert {w.ticker for w in db.get_watchlist(conn, 1, b)} == {"AAPL", "TSLA"}
    # the pipeline/quotes universe is the union across a user's lists
    assert set(db.get_watched_tickers_for_user(conn, 1)) == {"AAPL", "TSLA"}


def test_rename_and_last_list_guard(conn):
    first = db.get_watchlists(conn, 1)[0].id
    assert db.rename_watchlist(conn, 1, first, "Core") is True
    assert db.get_watchlists(conn, 1)[0].name == "Core"
    assert db.delete_watchlist(conn, 1, first) is False  # only list — refused
    second = db.create_watchlist(conn, 1, "Spec", "t").id
    db.add_watch(conn, 1, WatchItem(ticker="GME", note="", added_at="t"), second)
    assert db.delete_watchlist(conn, 1, second) is True
    assert [lst.id for lst in db.get_watchlists(conn, 1)] == [first]
    assert db.get_watched_tickers_for_user(conn, 1) == []  # its tickers went too


def test_remove_from_specific_list(conn):
    a = db.get_watchlists(conn, 1)[0].id
    db.add_watch(conn, 1, WatchItem(ticker="AAPL", note="", added_at="t"), a)
    db.add_watch(conn, 1, WatchItem(ticker="MSFT", note="", added_at="t"), a)
    db.remove_watch(conn, 1, "AAPL", a)
    assert [w.ticker for w in db.get_watchlist(conn, 1, a)] == ["MSFT"]


def test_cannot_touch_another_users_list(conn):
    l2 = db.get_watchlists(conn, 2)[0].id
    assert db.rename_watchlist(conn, 1, l2, "hijack") is False
    assert db.delete_watchlist(conn, 1, l2) is False


# ---- moving a ticker between lists / editing its note (Tasks 5 + 6) ----

def test_move_carries_the_note(conn):
    a = db.get_watchlists(conn, 1)[0].id
    b = db.create_watchlist(conn, 1, "Spec", "t").id
    db.add_watch(conn, 1, WatchItem(ticker="AAPL", note="earnings play", added_at="t"), a)
    assert db.update_watch(conn, 1, "AAPL", a, to_list_id=b) is True
    assert db.get_watchlist(conn, 1, a) == []
    moved = db.get_watchlist(conn, 1, b)
    assert [w.ticker for w in moved] == ["AAPL"]
    assert moved[0].note == "earnings play"  # a move must never drop the note


def test_edit_note_in_place(conn):
    a = db.get_watchlists(conn, 1)[0].id
    db.add_watch(conn, 1, WatchItem(ticker="AAPL", note="old", added_at="t"), a)
    assert db.update_watch(conn, 1, "AAPL", a, note="  new read  ") is True
    assert db.get_watchlist(conn, 1, a)[0].note == "new read"


def test_move_onto_a_list_that_already_has_it(conn):
    a = db.get_watchlists(conn, 1)[0].id
    b = db.create_watchlist(conn, 1, "Spec", "t").id
    db.add_watch(conn, 1, WatchItem(ticker="AAPL", note="from a", added_at="t"), a)
    db.add_watch(conn, 1, WatchItem(ticker="AAPL", note="from b", added_at="t"), b)
    assert db.update_watch(conn, 1, "AAPL", a, to_list_id=b) is True
    assert db.get_watchlist(conn, 1, a) == []            # source row gone
    assert [w.ticker for w in db.get_watchlist(conn, 1, b)] == ["AAPL"]  # no duplicate


def test_move_rejects_unknown_or_foreign_lists(conn):
    a = db.get_watchlists(conn, 1)[0].id
    foreign = db.get_watchlists(conn, 2)[0].id
    db.add_watch(conn, 1, WatchItem(ticker="AAPL", note="", added_at="t"), a)
    assert db.update_watch(conn, 1, "AAPL", a, to_list_id=foreign) is False
    assert db.update_watch(conn, 1, "AAPL", foreign, to_list_id=a) is False
    assert db.update_watch(conn, 1, "MSFT", a, to_list_id=a) is False  # not on the list
    assert [w.ticker for w in db.get_watchlist(conn, 1, a)] == ["AAPL"]  # nothing moved


def test_move_and_note_api(client):
    lists = client.get("/api/watchlists").json()
    src = lists[0]["id"]
    dst = next(lst for lst in client.post("/api/watchlists", json={"name": "Spec"}).json()
               if lst["name"] == "Spec")["id"]
    client.post("/api/watchlist", json={"ticker": "NVDA", "note": "keep me", "list_id": src})

    edited = client.patch("/api/watchlist/NVDA",
                          json={"from_list_id": src, "note": "changed"}).json()
    assert edited[0]["note"] == "changed"

    remaining = client.patch("/api/watchlist/NVDA",
                             json={"from_list_id": src, "to_list_id": dst}).json()
    assert remaining == []
    landed = client.get(f"/api/watchlist?list_id={dst}").json()
    assert landed[0]["ticker"] == "NVDA" and landed[0]["note"] == "changed"

    missing = client.patch("/api/watchlist/NVDA", json={"from_list_id": src})
    assert missing.status_code == 404


# ---- API layer ----

def test_watchlists_api_crud(client):
    assert len(client.get("/api/watchlists").json()) == 1
    created = client.post("/api/watchlists", json={"name": "Longs"}).json()
    assert len(created) == 2
    longs = next(lst for lst in created if lst["name"] == "Longs")
    client.post("/api/watchlist", json={"ticker": "NVDA", "list_id": longs["id"]})
    assert [i["ticker"] for i in client.get(f"/api/watchlist?list_id={longs['id']}").json()] == ["NVDA"]
    assert client.get("/api/watchlist").json() == []  # default list untouched
    renamed = client.patch(f"/api/watchlists/{longs['id']}", json={"name": "Winners"}).json()
    assert any(lst["name"] == "Winners" for lst in renamed)
    assert len(client.delete(f"/api/watchlists/{longs['id']}").json()) == 1


def test_cannot_delete_only_watchlist_api(client):
    only = client.get("/api/watchlists").json()[0]
    assert client.delete(f"/api/watchlists/{only['id']}").status_code == 409
