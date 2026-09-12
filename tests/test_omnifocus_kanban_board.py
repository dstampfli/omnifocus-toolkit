import json

import pytest

from omnifocus_kanban_board import (
    SORT_KEYS,
    BoardState,
    MoveError,
    build_board,
    finish_card,
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
