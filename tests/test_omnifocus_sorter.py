import json

import pytest

from omnifocus_sorter import (
    SORT_KEYS,
    STATUS_RANK,
    build_write_config,
    format_report,
    parse_args,
    parse_read_result,
    run_sort,
    sort_tasks,
)


# ------------------------------- helpers ---------------------------------

def task(tid, name="T", status="Available", **dates):
    """A read-stage task dict; every date field defaults to None."""
    base = {"id": tid, "name": name, "status": status, "added": None,
            "completionDate": None, "dueDate": None, "plannedDate": None,
            "deferDate": None, "dropDate": None, "tags": []}
    base.update(dates)
    return base


def names(tasks):
    return [t["name"] for t in tasks]


def ids(tasks):
    return [t["id"] for t in tasks]


# ------------------------------ sort keys ---------------------------------

def test_sort_keys_cover_the_eight_native_omnifocus_keys():
    assert set(SORT_KEYS) == {"title", "status", "added", "completed",
                              "due", "planned", "defer", "dropped"}


def test_sort_by_title_is_case_insensitive():
    tasks = [task("1", "banana"), task("2", "Apple"), task("3", "cherry")]
    assert names(sort_tasks(tasks, "title")) == ["Apple", "banana", "cherry"]


def test_sort_by_date_ascending():
    tasks = [task("1", "later", dueDate=3000), task("2", "earlier", dueDate=1000)]
    assert names(sort_tasks(tasks, "due")) == ["earlier", "later"]


def test_sort_by_each_date_key_uses_its_own_field():
    # Each key must read its own field, not a neighbour's.
    for key, field in [("added", "added"), ("completed", "completionDate"),
                       ("due", "dueDate"), ("planned", "plannedDate"),
                       ("defer", "deferDate"), ("dropped", "dropDate")]:
        tasks = [task("1", "second", **{field: 2000}),
                 task("2", "first", **{field: 1000})]
        assert names(sort_tasks(tasks, key)) == ["first", "second"], key


def test_sort_by_status_uses_urgency_rank():
    order = ["Dropped", "Completed", "Blocked", "Available", "Next",
             "DueSoon", "Overdue"]
    tasks = [task(str(i), s, status=s) for i, s in enumerate(order)]
    assert names(sort_tasks(tasks, "status")) == [
        "Overdue", "DueSoon", "Next", "Available", "Blocked", "Completed",
        "Dropped"]


def test_status_rank_is_urgency_first():
    assert STATUS_RANK["Overdue"] < STATUS_RANK["Available"]
    assert STATUS_RANK["Available"] < STATUS_RANK["Completed"]
    assert STATUS_RANK["Completed"] < STATUS_RANK["Dropped"]


def test_unknown_status_sorts_last_rather_than_raising():
    tasks = [task("1", "weird", status="Nonsense"),
             task("2", "known", status="Dropped")]
    assert names(sort_tasks(tasks, "status")) == ["known", "weird"]


# ------------------------------ null handling ------------------------------

def test_tasks_without_the_date_sort_last_ascending():
    tasks = [task("1", "none"), task("2", "dated", dueDate=1000)]
    assert names(sort_tasks(tasks, "due")) == ["dated", "none"]


def test_tasks_without_the_date_sort_last_descending_too():
    """Nulls must never float to the top when the order is reversed."""
    tasks = [task("1", "none"), task("2", "early", dueDate=1000),
             task("3", "late", dueDate=3000)]
    assert names(sort_tasks(tasks, "due", descending=True)) == [
        "late", "early", "none"]


def test_nulls_keep_their_relative_order():
    tasks = [task("1", "b"), task("2", "a"), task("3", "dated", dueDate=1)]
    assert names(sort_tasks(tasks, "due")) == ["dated", "b", "a"]


# --------------------------------- tag sort --------------------------------

def test_sort_by_tag_orders_by_tag_order_position():
    order = ["Next", "Waiting", "Someday"]
    tasks = [task("1", "s", tags=["Someday"]),
             task("2", "n", tags=["Next"]),
             task("3", "w", tags=["Waiting"])]
    assert names(sort_tasks(tasks, "tag", tag_order=order)) == ["n", "w", "s"]


def test_sort_by_tag_uses_minimum_index_for_multi_tag_task():
    """A task with several listed tags sorts by its earliest-listed one."""
    order = ["Next", "Waiting", "Someday"]
    tasks = [task("1", "someday", tags=["Someday"]),
             task("2", "both", tags=["Someday", "Next"])]
    assert names(sort_tasks(tasks, "tag", tag_order=order)) == ["both", "someday"]


def test_sort_by_tag_is_case_insensitive():
    tasks = [task("1", "b", tags=["someday"]), task("2", "a", tags=["NEXT"])]
    assert names(sort_tasks(tasks, "tag", tag_order=["Next", "Someday"])) == [
        "a", "b"]


def test_tasks_without_a_listed_tag_sort_last():
    order = ["Next"]
    tasks = [task("1", "untagged", tags=["Home"]),
             task("2", "no tags", tags=[]),
             task("3", "next", tags=["Next"])]
    assert names(sort_tasks(tasks, "tag", tag_order=order)) == [
        "next", "untagged", "no tags"]


def test_unmatched_tag_tasks_stay_last_when_descending():
    order = ["Next", "Waiting"]
    tasks = [task("1", "none", tags=[]),
             task("2", "next", tags=["Next"]),
             task("3", "waiting", tags=["Waiting"])]
    assert names(sort_tasks(tasks, "tag", tag_order=order, descending=True)) == [
        "waiting", "next", "none"]


def test_tag_ties_preserve_input_order():
    order = ["Next"]
    tasks = [task("1", "first", tags=["Next"]), task("2", "second", tags=["Next"])]
    assert names(sort_tasks(tasks, "tag", tag_order=order)) == ["first", "second"]


def test_sort_by_tag_without_tag_order_exits():
    with pytest.raises(SystemExit) as excinfo:
        sort_tasks([task("1", tags=["Next"])], "tag", tag_order=[])
    assert "tag" in str(excinfo.value)


# --------------------------- stability & direction -------------------------

def test_ties_preserve_input_order():
    tasks = [task("1", "first", dueDate=1000), task("2", "second", dueDate=1000)]
    assert names(sort_tasks(tasks, "due")) == ["first", "second"]


def test_sorting_an_already_sorted_list_is_a_no_op():
    tasks = [task("1", "a", dueDate=1), task("2", "b", dueDate=2),
             task("3", "c")]
    once = sort_tasks(tasks, "due")
    assert sort_tasks(once, "due") == once


def test_descending_reverses_non_null_order():
    tasks = [task("1", "a", dueDate=1), task("2", "c", dueDate=3),
             task("3", "b", dueDate=2)]
    assert names(sort_tasks(tasks, "due", descending=True)) == ["c", "b", "a"]


def test_descending_reverses_the_status_rank():
    tasks = [task("1", "overdue", status="Overdue"),
             task("2", "dropped", status="Dropped")]
    assert names(sort_tasks(tasks, "status", descending=True)) == [
        "dropped", "overdue"]


def test_sort_does_not_mutate_the_input_list():
    tasks = [task("1", "b", dueDate=2), task("2", "a", dueDate=1)]
    sort_tasks(tasks, "due")
    assert names(tasks) == ["b", "a"]


def test_unknown_sort_key_exits_with_a_clear_message():
    with pytest.raises(SystemExit) as excinfo:
        sort_tasks([task("1")], "priority")
    assert "priority" in str(excinfo.value)
    assert "due" in str(excinfo.value)  # lists the valid keys


# -------------------------------- read stage -------------------------------

def test_parse_read_result_returns_projects_and_missing():
    payload = json.dumps({
        "projects": [{"id": "p1", "name": "Training",
                      "tasks": [task("t1", "Open Wiki")]}],
        "missing": ["Nope"],
    })
    projects, missing = parse_read_result(payload)
    assert missing == ["Nope"]
    assert projects[0]["name"] == "Training"
    assert ids(projects[0]["tasks"]) == ["t1"]


# ------------------------------- write config ------------------------------

def test_build_write_config_carries_the_sorted_id_order():
    projects = [{"id": "p1", "name": "Training",
                 "tasks": [task("t2"), task("t1")]}]
    cfg = build_write_config(projects, {"t1", "t2"})
    assert cfg["projects"] == [{"id": "p1", "taskIds": ["t2", "t1"]}]


def test_build_write_config_drops_ids_not_seen_by_the_read_stage():
    """Only ids whitelisted against the read set may reach the OmniJS source."""
    projects = [{"id": "p1", "name": "Training",
                 "tasks": [task("t1"), task("injected")]}]
    cfg = build_write_config(projects, {"t1"})
    assert cfg["projects"] == [{"id": "p1", "taskIds": ["t1"]}]


# ----------------------------------- CLI -----------------------------------

def test_parse_args_projects_key_and_flags():
    projects, by, descending, apply = parse_args(
        ["Training", "Tech", "--by", "due", "--desc", "--apply"])
    assert projects == ["Training", "Tech"]
    assert by == "due"
    assert descending is True
    assert apply is True


def test_parse_args_defaults_to_ascending_dry_run():
    projects, by, descending, apply = parse_args(["Training", "--by", "title"])
    assert projects == ["Training"]
    assert by == "title"
    assert descending is False
    assert apply is False


def test_parse_args_missing_key_returns_none():
    _projects, by, _descending, _apply = parse_args(["Training"])
    assert by is None


# ---------------------------------- run_sort -------------------------------

def _fake_read(projects_payload, missing=None):
    def read(_names):
        return projects_payload, list(missing or [])
    return read


def test_run_sort_dry_run_never_calls_the_write_seam():
    calls = []
    read = _fake_read([{"id": "p1", "name": "Training",
                        "tasks": [task("t2", "b", dueDate=2),
                                  task("t1", "a", dueDate=1)]}])

    def apply_fn(cfg):
        calls.append(cfg)
        return [], []

    out = run_sort(["Training"], "due", read=read, apply_fn=apply_fn)
    assert calls == []
    assert out["dry_run"] is True
    assert out["projects"][0]["order"] == ["a", "b"]


def test_run_sort_reports_changed_false_when_already_sorted():
    read = _fake_read([{"id": "p1", "name": "Training",
                        "tasks": [task("t1", "a", dueDate=1),
                                  task("t2", "b", dueDate=2)]}])
    out = run_sort(["Training"], "due", read=read, apply_fn=lambda cfg: ([], []))
    assert out["projects"][0]["changed"] is False


def test_run_sort_reports_changed_true_when_order_differs():
    read = _fake_read([{"id": "p1", "name": "Training",
                        "tasks": [task("t2", "b", dueDate=2),
                                  task("t1", "a", dueDate=1)]}])
    out = run_sort(["Training"], "due", read=read, apply_fn=lambda cfg: ([], []))
    assert out["projects"][0]["changed"] is True


def test_run_sort_applies_the_sorted_order_when_asked():
    seen = {}
    read = _fake_read([{"id": "p1", "name": "Training",
                        "tasks": [task("t2", "b", dueDate=2),
                                  task("t1", "a", dueDate=1)]}])

    def apply_fn(cfg):
        seen["cfg"] = cfg
        return ["p1"], []

    out = run_sort(["Training"], "due", apply=True, read=read, apply_fn=apply_fn)
    assert seen["cfg"]["projects"] == [{"id": "p1", "taskIds": ["t1", "t2"]}]
    assert out["dry_run"] is False
    assert out["applied"] == ["p1"]


def test_run_sort_skips_the_write_when_nothing_changed():
    """An already-sorted project needs no moves, so don't touch OmniFocus."""
    calls = []
    read = _fake_read([{"id": "p1", "name": "Training",
                        "tasks": [task("t1", "a", dueDate=1)]}])
    out = run_sort(["Training"], "due", apply=True, read=read,
                   apply_fn=lambda cfg: calls.append(cfg) or ([], []))
    assert calls == []
    assert out["projects"][0]["changed"] is False


def test_run_sort_surfaces_missing_projects():
    read = _fake_read([], missing=["Ghost"])
    out = run_sort(["Ghost"], "due", read=read, apply_fn=lambda cfg: ([], []))
    assert out["missing"] == ["Ghost"]


def test_run_sort_echoes_the_sort_settings():
    read = _fake_read([])
    out = run_sort(["X"], "status", descending=True, read=read,
                   apply_fn=lambda cfg: ([], []))
    assert out["by"] == "status" and out["descending"] is True


def test_run_sort_rejects_an_unknown_key_before_reading():
    def read(_names):
        raise AssertionError("must validate the key before touching OmniFocus")

    with pytest.raises(SystemExit):
        run_sort(["Training"], "nonsense", read=read,
                 apply_fn=lambda cfg: ([], []))


def test_run_sort_result_is_json_serializable():
    read = _fake_read([{"id": "p1", "name": "Training",
                        "tasks": [task("t1", "a", dueDate=1)]}])
    out = run_sort(["Training"], "due", read=read, apply_fn=lambda cfg: ([], []))
    json.dumps(out)  # must not raise


def test_run_sort_orders_by_tag():
    read = _fake_read([{"id": "p1", "name": "Training",
                        "tasks": [task("t1", "someday", tags=["Someday"]),
                                  task("t2", "next", tags=["Next"])]}])
    out = run_sort(["Training"], "tag", tag_order=["Next", "Someday"],
                   read=read, apply_fn=lambda cfg: ([], []))
    assert out["projects"][0]["order"] == ["next", "someday"]
    assert out["by"] == "tag"


def test_run_sort_applies_the_tag_order_when_asked():
    seen = {}
    read = _fake_read([{"id": "p1", "name": "Training",
                        "tasks": [task("t1", "someday", tags=["Someday"]),
                                  task("t2", "next", tags=["Next"])]}])

    def apply_fn(cfg):
        seen["cfg"] = cfg
        return ["p1"], []

    run_sort(["Training"], "tag", tag_order=["Next", "Someday"],
             apply=True, read=read, apply_fn=apply_fn)
    assert seen["cfg"]["projects"] == [{"id": "p1", "taskIds": ["t2", "t1"]}]


def test_run_sort_rejects_tag_without_tag_order_before_reading():
    def read(_names):
        raise AssertionError("must validate before touching OmniFocus")

    with pytest.raises(SystemExit):
        run_sort(["Training"], "tag", read=read, apply_fn=lambda cfg: ([], []))


# --------------------------------- reporting -------------------------------

def test_format_report_shows_the_new_order():
    result = {"dry_run": True, "by": "due", "descending": False,
              "projects": [{"name": "Training", "count": 2, "changed": True,
                            "order": ["a", "b"]}],
              "applied": [], "missing": []}
    text = format_report(result)
    assert "Training" in text and "a" in text and "b" in text


def test_format_report_flags_missing_projects():
    result = {"dry_run": True, "by": "due", "descending": False,
              "projects": [], "applied": [], "missing": ["Ghost"]}
    assert "Ghost" in format_report(result)
