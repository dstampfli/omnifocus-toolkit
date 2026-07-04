from omnifocus_inbox_triage import (
    Decision,
    should_move,
    partition_decisions,
)


def mk(item_id="t1", project_id="p1", confidence="high"):
    return Decision(
        item_id=item_id,
        project_id=project_id,
        project_name="Pets",
        confidence=confidence,
        reason="about the cat",
    )


def test_should_move_high_confidence_valid_ids():
    assert should_move(mk(), {"t1"}, {"p1"}) is True


def test_should_move_rejects_medium_by_default():
    assert should_move(mk(confidence="medium"), {"t1"}, {"p1"}) is False


def test_should_move_allows_medium_when_threshold_lowered():
    assert should_move(mk(confidence="medium"), {"t1"}, {"p1"}, min_confidence="medium") is True


def test_should_move_rejects_null_project():
    assert should_move(mk(project_id=None), {"t1"}, {"p1"}) is False


def test_should_move_rejects_unknown_item_id():
    assert should_move(mk(item_id="ghost"), {"t1"}, {"p1"}) is False


def test_should_move_rejects_unknown_project_id():
    assert should_move(mk(project_id="ghost"), {"t1"}, {"p1"}) is False


def test_partition_splits_move_and_leave():
    decisions = [
        mk(item_id="t1", confidence="high"),
        mk(item_id="t2", confidence="low"),
    ]
    to_move, to_leave = partition_decisions(decisions, ["t1", "t2"], ["p1"])
    assert [d.item_id for d in to_move] == ["t1"]
    assert [d.item_id for d in to_leave] == ["t2"]


def test_partition_dedupes_repeated_item_id():
    decisions = [mk(item_id="t1"), mk(item_id="t1")]
    to_move, to_leave = partition_decisions(decisions, ["t1"], ["p1"])
    assert len(to_move) + len(to_leave) == 1
