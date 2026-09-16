import http.client
import json
import re
import threading
from datetime import datetime, timezone

import pytest

import omnifocus_kanban_board as board_mod
from omnifocus_common import JxaError
from omnifocus_kanban_board import (
    DEFAULT_LANE_ORDER,
    MISSING_TAG_MESSAGE,
    PAGE_PATH,
    MOVE_TASK_JXA,
    READ_BOARD_JXA,
    SORT_KEYS,
    BoardApp,
    BoardState,
    MoveError,
    build_board,
    build_tags,
    expand_tag_ids,
    finish_card,
    make_server,
    move_task,
    order_lanes,
    parse_args,
    parse_lane_order,
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

# Tag tree in OmniFocus order (parents before children): Engagements > NG,
# Engagements > NetApp, then top-level Work and Me.
TAGS = [
    {"id": "eng", "name": "Engagements", "parent_id": None},
    {"id": "ng", "name": "NG", "parent_id": "eng"},
    {"id": "netapp", "name": "NetApp", "parent_id": "eng"},
    {"id": "work", "name": "Work", "parent_id": None},
    {"id": "me", "name": "Me", "parent_id": None},
]
PARENT_OF = {t["id"]: t["parent_id"] for t in TAGS}


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
    board = build_board(raw, lane_order=())
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
    raw = {"lanes": LANES, "tags": TAGS,
           "cards": [card("a", due=1, flagged=True, tags=["NG"], tag_ids=["ng"])]}
    json.dumps(build_board(raw))


# ------------------------------- tags -------------------------------------

def test_expand_tag_ids_adds_ancestors_once():
    assert expand_tag_ids(["ng", "work"], PARENT_OF) == ["ng", "eng", "work"]


def test_expand_tag_ids_dedupes_shared_ancestor():
    assert expand_tag_ids(["ng", "netapp"], PARENT_OF) == ["ng", "eng", "netapp"]


def test_expand_tag_ids_keeps_ids_missing_from_tree():
    assert expand_tag_ids(["ghost"], PARENT_OF) == ["ghost"]
    assert expand_tag_ids(["ng"], {}) == ["ng"]


def test_finish_card_defaults_tag_ids_to_empty():
    assert finish_card({"id": "a", "name": "T", "lane_id": "L1"})["tag_ids"] == []


def test_finish_card_expands_tag_ids_with_parent_of():
    out = finish_card(card("a", tags=["NG"], tag_ids=["ng"]), parent_of=PARENT_OF)
    assert out["tag_ids"] == ["ng", "eng"]
    assert out["tags"] == ["NG"]  # display names stay the leaf names


def test_build_tags_keeps_order_depth_and_counts_descendants():
    cards = [
        finish_card(card("a", tag_ids=["ng"]), parent_of=PARENT_OF),
        finish_card(card("b", tag_ids=["netapp", "work"]), parent_of=PARENT_OF),
        finish_card(card("c"), parent_of=PARENT_OF),
    ]
    assert build_tags(TAGS, cards) == [
        {"id": "eng", "name": "Engagements", "parent_id": None, "depth": 0, "count": 2},
        {"id": "ng", "name": "NG", "parent_id": "eng", "depth": 1, "count": 1},
        {"id": "netapp", "name": "NetApp", "parent_id": "eng", "depth": 1, "count": 1},
        {"id": "work", "name": "Work", "parent_id": None, "depth": 0, "count": 1},
        {"id": "me", "name": "Me", "parent_id": None, "depth": 0, "count": 0},
    ]


def test_build_board_emits_tag_tree_and_expands_card_tag_ids():
    raw = {"lanes": LANES, "tags": TAGS,
           "cards": [card("a", tag_ids=["ng"]),
                     card("b", tag_ids=["work"], status="Completed")]}
    board = build_board(raw)
    assert board["cards"][0]["tag_ids"] == ["ng", "eng"]
    counts = {t["id"]: t["count"] for t in board["tags"]}
    assert counts == {"eng": 1, "ng": 1, "netapp": 0, "work": 0, "me": 0}


def test_build_board_tolerates_missing_tag_tree():
    board = build_board({"lanes": LANES, "cards": [card("a", tag_ids=["x"])]})
    assert board["tags"] == []
    assert board["cards"][0]["tag_ids"] == ["x"]


# ------------------------------- lane order -------------------------------

def lane_names(lanes):
    return [lane["name"] for lane in lanes]


def test_default_lane_order():
    assert DEFAULT_LANE_ORDER == ("Waiting", "To Do", "In Progress", "Done", "Reviewed")


def test_parse_lane_order_splits_on_commas_and_strips():
    assert parse_lane_order(" Done, To Do ,,Waiting ") == ("Done", "To Do", "Waiting")


def test_parse_lane_order_blank_falls_back_to_default():
    assert parse_lane_order("") == DEFAULT_LANE_ORDER
    assert parse_lane_order(" , ") == DEFAULT_LANE_ORDER


def test_order_lanes_puts_named_lanes_first_then_the_rest_in_omnifocus_order():
    lanes = [{"id": "1", "name": "To Do"}, {"id": "2", "name": "Extra"},
             {"id": "3", "name": "Waiting"}, {"id": "4", "name": "Other"}]
    assert lane_names(order_lanes(lanes, ("Waiting", "To Do"))) == ["Waiting", "To Do", "Extra", "Other"]


def test_order_lanes_matches_names_exactly():
    lanes = [{"id": "1", "name": "to do"}, {"id": "2", "name": "Waiting"}]
    assert lane_names(order_lanes(lanes, ("To Do", "Waiting"))) == ["Waiting", "to do"]


def test_order_lanes_ignores_names_with_no_lane():
    lanes = [{"id": "1", "name": "Done"}]
    assert order_lanes(lanes, ("Waiting", "Done")) == lanes


def test_build_board_orders_lanes_by_default_lane_order():
    raw = {"lanes": [{"id": "a", "name": "Reviewed"}, {"id": "b", "name": "Done"},
                     {"id": "c", "name": "To Do"}, {"id": "d", "name": "In Progress"},
                     {"id": "e", "name": "Waiting"}],
           "cards": []}
    assert lane_names(build_board(raw)["lanes"]) == list(DEFAULT_LANE_ORDER)


def test_build_board_honours_custom_lane_order():
    board = build_board({"lanes": LANES, "cards": []}, lane_order=("Reviewed", "To Do"))
    assert lane_names(board["lanes"]) == ["Reviewed", "To Do"]


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


def test_board_state_remembers_tag_parents():
    state = BoardState()
    assert state.parent_of == {}
    state.remember({"lanes": LANES, "cards": [], "tags": build_tags(TAGS, [])})
    assert state.parent_of == PARENT_OF


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


def make_app(read=None, move=None, tmp_path=None, **kw):
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
        **kw,
    )


def test_get_board_returns_board_with_tag_and_timestamp():
    app = make_app()
    status, payload = app.get_board()
    assert status == 200
    assert payload["kanban_tag"] == "Kanban"
    assert payload["read_at"] == "2026-09-12T10:30:05+00:00"
    assert payload["lanes"] == [LANES[1], LANES[0]]  # default order: To Do before Reviewed
    assert ids(payload["cards"]) == ["b", "a"]
    assert app.state.task_ids == {"a", "b"} and app.state.lane_ids == {"L1", "L2"}


def test_get_board_applies_the_apps_lane_order():
    app = make_app(lane_order=("Reviewed", "To Do"))
    status, payload = app.get_board()
    assert status == 200
    assert payload["lanes"] == LANES


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


def test_post_move_expands_returned_card_tag_ids_from_last_read():
    raw = dict(RAW, tags=TAGS)
    app = make_app(read=lambda tag, n: json.loads(json.dumps(raw)),
                   move=lambda tag, t, l, n: {"card": card(t, lane=l, tag_ids=["ng"])})
    app.get_board()
    status, payload = app.post_move({"task_id": "a", "lane_id": "L2"}, True)
    assert status == 200
    assert payload["card"]["tag_ids"] == ["ng", "eng"]


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
    assert payload["lanes"] == [LANES[1], LANES[0]]  # default order: To Do before Reviewed
    assert ids(payload["cards"]) == ["b", "a"]


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
    # The only <link> allowed is the inline data: favicon -- nothing fetched.
    links = re.findall(r"<link\b[^>]*>", html)
    assert links, "expected the inline favicon <link>"
    for link in links:
        assert re.search(r"""href=["']data:""", link), link
    # Strip data: URIs before scanning for URLs: the favicon SVG carries an
    # xmlns identifier (http://www.w3.org/2000/svg) that is never fetched.
    stripped = re.sub(r"""(["'])data:.*?\1""", r"\1\1", html)
    assert "http://" not in stripped and "https://" not in stripped
    assert 'src="' not in stripped
