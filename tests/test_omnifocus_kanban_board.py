import http.client
import json
import re
import threading
from datetime import datetime, timezone

import pytest

import omnifocus_kanban_board as board_mod
from omnifocus_common import JxaError
from omnifocus_kanban_board import (
    MISSING_TAG_MESSAGE,
    PAGE_PATH,
    MOVE_TASK_JXA,
    READ_BOARD_JXA,
    SORT_KEYS,
    BoardApp,
    BoardState,
    MoveError,
    build_board,
    finish_card,
    make_server,
    move_task,
    parse_args,
    read_board,
    serve,
    sort_cards,
    validate_move,
)


# ------------------------------- helpers ---------------------------------

def card(cid, name="T", lane="L1", project="P", **extra):
    base = {"id": cid, "name": name, "lane_id": lane,
            "project": {"id": "p-" + project, "name": project} if project else None,
            "due": None, "defer": None, "added": None, "flagged": False,
            "status": "Available", "tags": [], "note": ""}
    base.update(extra)
    return base


def ids(cards):
    return [c["id"] for c in cards]


LANES = [{"id": "L1", "name": "Reviewed"}, {"id": "L2", "name": "To Do"}]


# ------------------------------- sort_cards -------------------------------

def test_sort_keys():
    assert SORT_KEYS == ("due", "title", "project", "added")


def test_sort_cards_rejects_unknown_key():
    with pytest.raises(ValueError):
        sort_cards([], "bogus")


def test_sort_due_ascending_with_undated_last():
    cards = [card("a", due=None), card("b", due=300), card("c", due=100)]
    assert ids(sort_cards(cards, "due")) == ["c", "b", "a"]


def test_sort_due_ties_break_by_project_then_title():
    cards = [card("a", name="Zed", project="B", due=1),
             card("b", name="Alpha", project="B", due=1),
             card("c", name="Mid", project="A", due=1)]
    assert ids(sort_cards(cards, "due")) == ["c", "b", "a"]


def test_sort_title_is_casefolded():
    cards = [card("a", name="banana"), card("b", name="Apple"), card("c", name="cherry")]
    assert ids(sort_cards(cards, "title")) == ["b", "a", "c"]


def test_sort_project_then_due_with_inbox_last():
    cards = [card("a", project=None, due=1),
             card("b", project="Zeta", due=5),
             card("c", project="Alpha", due=None),
             card("d", project="Alpha", due=2)]
    assert ids(sort_cards(cards, "project")) == ["d", "c", "b", "a"]


def test_sort_added_newest_first_with_missing_last():
    cards = [card("a", added=None), card("b", added=100), card("c", added=300)]
    assert ids(sort_cards(cards, "added")) == ["c", "b", "a"]


def test_sort_is_stable_and_pure():
    cards = [card("a", due=None), card("b", due=None)]
    out = sort_cards(cards, "due")
    assert ids(out) == ["a", "b"]
    assert out is not cards


# ------------------------------- finish_card ------------------------------

def test_finish_card_replaces_note_with_excerpt():
    raw = card("a", note="Hello​   world\n\n\n\nmore")
    out = finish_card(raw)
    assert "note" not in out
    assert out["note_excerpt"] == "Hello world\n\nmore"
    assert out["id"] == "a" and out["lane_id"] == "L1"


def test_finish_card_caps_excerpt():
    raw = card("a", note="x" * 500)
    out = finish_card(raw, excerpt_chars=10)
    assert out["note_excerpt"] == "x" * 10 + "…"


def test_finish_card_handles_missing_optional_fields():
    out = finish_card({"id": "a", "name": "T", "lane_id": "L1"})
    assert out["project"] is None
    assert out["tags"] == []
    assert out["note_excerpt"] == ""


# ------------------------------- build_board ------------------------------

def test_build_board_preserves_lane_order_and_sorts_cards():
    raw = {"lanes": LANES, "cards": [card("a", due=None), card("b", due=5)]}
    board = build_board(raw)
    assert board["lanes"] == LANES
    assert ids(board["cards"]) == ["b", "a"]
    assert all("note_excerpt" in c for c in board["cards"])


def test_build_board_hides_completed_and_dropped():
    raw = {"lanes": LANES, "cards": [card("a", status="Completed"),
                                     card("b", status="Dropped"),
                                     card("c", status="Next")]}
    assert ids(build_board(raw)["cards"]) == ["c"]


def test_build_board_keeps_first_lane_for_duplicate_task():
    raw = {"lanes": LANES, "cards": [card("a", lane="L1"), card("a", lane="L2")]}
    out = build_board(raw)["cards"]
    assert len(out) == 1 and out[0]["lane_id"] == "L1"


def test_build_board_drops_cards_in_unknown_lanes():
    raw = {"lanes": LANES, "cards": [card("a", lane="nope")]}
    assert build_board(raw)["cards"] == []


def test_build_board_result_is_json_serializable():
    raw = {"lanes": LANES, "cards": [card("a", due=1, flagged=True, tags=["x"])]}
    json.dumps(build_board(raw))


# ------------------------------- validate_move ----------------------------

TASKS = {"t1", "t2"}
LANE_IDS = {"L1", "L2"}


def test_validate_move_accepts_known_ids():
    assert validate_move({"task_id": "t1", "lane_id": "L2"}, TASKS, LANE_IDS) == ("t1", "L2")


@pytest.mark.parametrize("body", [None, [], "x", {"task_id": "t1"},
                                  {"lane_id": "L1"}, {"task_id": 1, "lane_id": "L1"}])
def test_validate_move_rejects_malformed_body(body):
    with pytest.raises(MoveError):
        validate_move(body, TASKS, LANE_IDS)


@pytest.mark.parametrize("bad", ["", "has space", "quote'x", "a" * 65, "semi;colon"])
def test_validate_move_rejects_malformed_ids(bad):
    with pytest.raises(MoveError):
        validate_move({"task_id": bad, "lane_id": "L1"}, TASKS | {bad}, LANE_IDS)
    with pytest.raises(MoveError):
        validate_move({"task_id": "t1", "lane_id": bad}, TASKS, LANE_IDS | {bad})


def test_validate_move_rejects_unknown_task_and_lane():
    with pytest.raises(MoveError, match="task"):
        validate_move({"task_id": "nope", "lane_id": "L1"}, TASKS, LANE_IDS)
    with pytest.raises(MoveError, match="lane"):
        validate_move({"task_id": "t1", "lane_id": "nope"}, TASKS, LANE_IDS)


def test_validate_move_requires_a_prior_read():
    with pytest.raises(MoveError, match="load the board"):
        validate_move({"task_id": "t1", "lane_id": "L1"}, set(), set(), loaded=False)


# ------------------------------- BoardState -------------------------------

def test_board_state_remembers_ids_from_board():
    state = BoardState()
    assert not state.loaded
    state.remember({"lanes": LANES, "cards": [card("a"), card("b", lane="L2")]})
    assert state.loaded
    assert state.task_ids == {"a", "b"}
    assert state.lane_ids == {"L1", "L2"}


# ------------------------------- JXA programs -----------------------------

def _cfg_fields(source):
    return set(re.findall(r"cfg\.(\w+)", source))


def test_read_jxa_interpolates_only_config():
    assert _cfg_fields(READ_BOARD_JXA) == {"kanbanTag", "maxNoteChars"}
    assert "JSON.stringify(cfg.kanbanTag)" in READ_BOARD_JXA
    assert "evaluateJavascript" in READ_BOARD_JXA


def test_move_jxa_interpolates_only_ids_and_config():
    assert _cfg_fields(MOVE_TASK_JXA) == {"kanbanTag", "maxNoteChars", "taskId", "laneId"}
    assert "JSON.stringify(cfg.taskId)" in MOVE_TASK_JXA
    assert "JSON.stringify(cfg.laneId)" in MOVE_TASK_JXA
    # The plug-in's exact re-tag operation.
    assert "task.removeTags(lanes)" in MOVE_TASK_JXA
    assert "task.addTag(lane)" in MOVE_TASK_JXA


def test_jxa_sources_share_the_card_serializer():
    assert "cardOf" in READ_BOARD_JXA and "cardOf" in MOVE_TASK_JXA
    assert "__CARD__" not in READ_BOARD_JXA and "__CARD__" not in MOVE_TASK_JXA


def test_read_board_passes_json_config(monkeypatch):
    calls = []
    monkeypatch.setattr(board_mod, "run_jxa_or_raise",
                        lambda script, *args: calls.append((script, args)) or {"lanes": [], "cards": []})
    assert read_board("Kanban", 123) == {"lanes": [], "cards": []}
    script, args = calls[0]
    assert script is READ_BOARD_JXA
    assert json.loads(args[0]) == {"kanbanTag": "Kanban", "maxNoteChars": 123}


def test_move_task_passes_json_config(monkeypatch):
    calls = []
    monkeypatch.setattr(board_mod, "run_jxa_or_raise",
                        lambda script, *args: calls.append((script, args)) or {"card": {}})
    assert move_task("Kanban", "t1", "L1", 50) == {"card": {}}
    script, args = calls[0]
    assert script is MOVE_TASK_JXA
    assert json.loads(args[0]) == {"kanbanTag": "Kanban", "maxNoteChars": 50,
                                   "taskId": "t1", "laneId": "L1"}


# --------------------------------- BoardApp -------------------------------

RAW = {"lanes": LANES, "cards": [card("a", due=None, note="n"), card("b", due=5, lane="L2")]}


def make_app(read=None, move=None, tmp_path=None):
    page = (tmp_path / "index.html") if tmp_path else None
    if page:
        page.write_text("<title>x</title>")
    return BoardApp(
        "Kanban",
        page_path=page,
        read=read or (lambda tag, n: json.loads(json.dumps(RAW))),
        move=move or (lambda tag, t, l, n: {"card": card(t, lane=l)}),
        max_note_chars=100,
        now=lambda: datetime(2026, 9, 12, 10, 30, 5, tzinfo=timezone.utc),
    )


def test_get_board_returns_board_with_tag_and_timestamp():
    app = make_app()
    status, payload = app.get_board()
    assert status == 200
    assert payload["kanban_tag"] == "Kanban"
    assert payload["read_at"] == "2026-09-12T10:30:05+00:00"
    assert payload["lanes"] == LANES
    assert ids(payload["cards"]) == ["b", "a"]
    assert app.state.task_ids == {"a", "b"} and app.state.lane_ids == {"L1", "L2"}


def test_get_board_passes_tag_and_note_cap_to_read():
    seen = []
    app = make_app(read=lambda tag, n: seen.append((tag, n)) or RAW)
    app.get_board()
    assert seen == [("Kanban", 100)]


def test_get_board_maps_missing_tag_to_409():
    app = make_app(read=lambda tag, n: {"error": "missing_kanban_tag"})
    status, payload = app.get_board()
    assert status == 409
    assert payload == {"error": MISSING_TAG_MESSAGE.format(tag="Kanban")}
    assert not app.state.loaded


def test_get_board_maps_jxa_error_to_500():
    def boom(tag, n):
        raise JxaError("osascript failed:\nOmniFocus got an error")
    status, payload = make_app(read=boom).get_board()
    assert status == 500
    assert "OmniFocus got an error" in payload["error"]


def test_post_move_requires_header():
    app = make_app()
    app.get_board()
    status, payload = app.post_move({"task_id": "a", "lane_id": "L2"}, has_header=False)
    assert status == 400 and "X-Kanban" in payload["error"]


def test_post_move_rejects_before_any_read():
    status, payload = make_app().post_move({"task_id": "a", "lane_id": "L2"}, True)
    assert status == 400 and "load the board" in payload["error"]


def test_post_move_rejects_unknown_ids_without_calling_omnifocus():
    calls = []
    app = make_app(move=lambda *a: calls.append(a))
    app.get_board()
    status, _ = app.post_move({"task_id": "zzz", "lane_id": "L2"}, True)
    assert status == 400 and calls == []


def test_post_move_happy_path_returns_finished_card():
    calls = []

    def move(tag, t, l, n):
        calls.append((tag, t, l, n))
        return {"card": card(t, lane=l, note="moved note")}
    app = make_app(move=move)
    app.get_board()
    status, payload = app.post_move({"task_id": "a", "lane_id": "L2"}, True)
    assert status == 200
    assert calls == [("Kanban", "a", "L2", 100)]
    assert payload["card"]["lane_id"] == "L2"
    assert payload["card"]["note_excerpt"] == "moved note"
    assert "note" not in payload["card"]


def test_post_move_maps_omnijs_error_to_400_and_jxa_error_to_500():
    app = make_app(move=lambda *a: {"error": "task no longer exists"})
    app.get_board()
    assert app.post_move({"task_id": "a", "lane_id": "L2"}, True)[0] == 400

    def boom(*a):
        raise JxaError("osascript failed:\nnope")
    app = make_app(move=boom)
    app.get_board()
    status, payload = app.post_move({"task_id": "a", "lane_id": "L2"}, True)
    assert status == 500 and "nope" in payload["error"]


def test_page_reads_file(tmp_path):
    app = make_app(tmp_path=tmp_path)
    assert app.page() == b"<title>x</title>"


# ------------------------------- HTTP server ------------------------------

@pytest.fixture
def server(tmp_path):
    app = make_app(tmp_path=tmp_path)
    srv = make_server(app, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def test_server_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_get_root_serves_page(server):
    resp, data = request(server, "GET", "/")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/html; charset=utf-8"
    assert resp.getheader("Cache-Control") == "no-store"
    assert data == b"<title>x</title>"


def test_get_board_over_http(server):
    resp, data = request(server, "GET", "/api/board")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "application/json; charset=utf-8"
    payload = json.loads(data)
    assert payload["lanes"] == LANES and ids(payload["cards"]) == ["b", "a"]


def test_unknown_routes_404(server):
    assert request(server, "GET", "/nope")[0].status == 404
    assert request(server, "POST", "/nope")[0].status == 404


def test_post_move_over_http(server):
    request(server, "GET", "/api/board")
    body = json.dumps({"task_id": "a", "lane_id": "L2"})
    resp, data = request(server, "POST", "/api/move", body,
                         {"Content-Type": "application/json"})
    assert resp.status == 400 and "X-Kanban" in json.loads(data)["error"]
    resp, data = request(server, "POST", "/api/move", body,
                         {"Content-Type": "application/json", "X-Kanban": "1"})
    assert resp.status == 200
    assert json.loads(data)["card"]["lane_id"] == "L2"


def test_post_move_with_invalid_json_is_400(server):
    request(server, "GET", "/api/board")
    resp, data = request(server, "POST", "/api/move", "{not json",
                         {"X-Kanban": "1"})
    assert resp.status == 400
    assert "JSON object" in json.loads(data)["error"]


# ------------------------------------ CLI ---------------------------------

def test_parse_args_defaults():
    args = parse_args([])
    assert args.port == 8765 and args.no_open is False


def test_parse_args_overrides():
    args = parse_args(["--port", "9000", "--no-open"])
    assert args.port == 9000 and args.no_open is True


def test_serve_reports_port_in_use(tmp_path, capsys):
    app = make_app(tmp_path=tmp_path)
    taken = make_server(app, 0)
    try:
        with pytest.raises(SystemExit) as info:
            serve(app, taken.server_address[1], open_browser=False)
        assert info.value.code == 1
        assert "Could not listen" in capsys.readouterr().err
    finally:
        taken.server_close()


# --------------------------------- the page -------------------------------

def test_real_page_exists_and_loads_nothing_external():
    html = PAGE_PATH.read_text(encoding="utf-8")
    assert "<title>" in html
    assert "/api/board" in html and "/api/move" in html
    assert '"X-Kanban"' in html
    assert "http://" not in html and "https://" not in html
    assert "<link" not in html and 'src="' not in html
