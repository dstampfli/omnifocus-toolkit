from omnifocus_inbox_triage import (
    Decision,
    should_move,
    partition_decisions,
    parse_read_result,
    active_projects,
    chunk_items,
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


def test_parse_read_result_splits_items_and_projects():
    stdout = (
        '{"items": [{"id": "t1", "name": "Vet appt", "note": ""}],'
        ' "projects": [{"id": "p1", "name": "Pets", "folderPath": "Home", "status": "active status"}]}'
    )
    items, projects = parse_read_result(stdout)
    assert items[0]["name"] == "Vet appt"
    assert projects[0]["id"] == "p1"


def test_active_projects_filters_non_active():
    projects = [
        {"id": "p1", "name": "Pets", "status": "active status"},
        {"id": "p2", "name": "Old", "status": "done status"},
        {"id": "p3", "name": "Later", "status": "on hold status"},
    ]
    kept = active_projects(projects)
    assert [p["id"] for p in kept] == ["p1"]


import json as _json
from omnifocus_inbox_triage import build_system_prompt, build_user_content


def test_build_system_prompt_mentions_key_rules():
    prompt = build_system_prompt().lower()
    assert "project" in prompt
    assert "confidence" in prompt
    assert "description" in prompt


def test_build_user_content_embeds_ids_as_json():
    items = [{"id": "t1", "name": "Vet appt", "note": ""}]
    projects = [{"id": "p1", "name": "Pets", "folderPath": "Home"}]
    content = build_user_content(items, projects)
    parsed = _json.loads(content)
    assert parsed["inbox_items"][0]["id"] == "t1"
    assert parsed["projects"][0]["id"] == "p1"


def test_build_user_content_includes_project_description():
    items = [{"id": "t1", "name": "Vet appt", "note": ""}]
    projects = [{"id": "p1", "name": "Pets", "folderPath": "Home",
                 "description": "Pet care: vet, food, grooming"}]
    parsed = _json.loads(build_user_content(items, projects))
    assert parsed["projects"][0]["description"] == "Pet care: vet, food, grooming"
    # the internal status field must not leak to the model
    assert "status" not in parsed["projects"][0]


def test_build_user_content_defaults_missing_description_to_empty():
    items = [{"id": "t1", "name": "Vet appt", "note": ""}]
    projects = [{"id": "p1", "name": "Pets", "folderPath": "Home"}]
    parsed = _json.loads(build_user_content(items, projects))
    assert parsed["projects"][0]["description"] == ""


from omnifocus_inbox_triage import build_apply_config


def test_build_apply_config_maps_ids():
    to_move = [mk(item_id="t1", project_id="p1"), mk(item_id="t2", project_id="p9")]
    cfg = build_apply_config(to_move)
    assert cfg["moves"] == [
        {"taskId": "t1", "projectId": "p1"},
        {"taskId": "t2", "projectId": "p9"},
    ]


from omnifocus_inbox_triage import format_report


def test_format_report_dry_run_says_will_move():
    items = [{"id": "t1", "name": "Vet appt", "note": ""}]
    to_move = [mk(item_id="t1", project_id="p1")]
    out = format_report(to_move, [], items, dry_run=True)
    assert "Will move" in out
    assert "Vet appt" in out
    assert "Pets" in out


def test_format_report_apply_says_moved():
    items = [{"id": "t1", "name": "Vet appt", "note": ""}]
    to_move = [mk(item_id="t1", project_id="p1")]
    out = format_report(to_move, [], items, dry_run=False)
    assert "Moved" in out


def test_format_report_lists_left_behind_items_with_reason():
    items = [{"id": "t2", "name": "Random thought", "note": ""}]
    to_leave = [mk(item_id="t2", project_id=None, confidence="low")]
    out = format_report([], to_leave, items, dry_run=True)
    assert "Left in Inbox" in out
    assert "Random thought" in out
    assert "about the cat" in out  # the reason text from mk()


def test_chunk_items_splits_with_remainder():
    assert [list(c) for c in chunk_items([1, 2, 3, 4, 5], 2)] == [[1, 2], [3, 4], [5]]


def test_chunk_items_exact_multiple():
    assert [list(c) for c in chunk_items([1, 2, 3, 4], 2)] == [[1, 2], [3, 4]]


def test_chunk_items_empty():
    assert list(chunk_items([], 3)) == []


def test_chunk_items_size_larger_than_list():
    assert [list(c) for c in chunk_items([1, 2], 10)] == [[1, 2]]


def test_format_report_failed_move_not_labeled_moved():
    items = [{"id": "t1", "name": "Vet appt", "note": ""}]
    to_move = [mk(item_id="t1", project_id="p1")]
    out = format_report(to_move, [], items, dry_run=False, failed_ids=["t1"])
    assert "Failed to move" in out
    assert "Vet appt" in out
    # the moved/success group must not claim this item
    lines = out.splitlines()
    moved_line = next((l for l in lines if l.startswith("Moved")), "")
    assert "Vet appt" not in moved_line
