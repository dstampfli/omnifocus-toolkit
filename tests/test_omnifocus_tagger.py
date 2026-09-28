from omnifocus_tagger import build_write_config, plan_tagging


# ------------------------------- helpers ---------------------------------

def tk(tid, tag_ids=(), completed=False, dropped=False):
    return {"id": tid, "name": tid.upper(), "completed": completed,
            "dropped": dropped, "tag_ids": list(tag_ids)}


def proj(pid, name, folder, tasks):
    return {"id": pid, "name": name, "folder": folder, "tasks": tasks}


def tg(tid, name, parent=None, in_kanban=False):
    return {"id": tid, "name": name, "parent_id": parent, "in_kanban": in_kanban}


TAGS = [tg("tHome", "Home"), tg("tWork", "Work"),
        tg("tK", "Kanban", in_kanban=True),
        tg("tTodo", "To Do", "tK", True), tg("tRev", "Reviewed", "tK", True)]


# ------------------------------- planning --------------------------------

def test_plan_splits_already_tagged_from_to_tag():
    p = plan_tagging([proj("p1", "Finances", "Home",
                           [tk("a", ["tHome"]), tk("b", ["tTodo", "tRev"]), tk("c")])],
                     TAGS)[0]
    assert p["folder_tag"] == "Home"
    assert p["count"] == 3
    assert p["already_tagged"] == 1
    assert p["to_tag"] == ["b", "c"]
    assert p["skipped_reason"] is None and p["failure"] is None


def test_plan_excludes_completed_and_dropped_unless_asked():
    tasks = [tk("open"), tk("done", completed=True), tk("gone", dropped=True)]
    p = plan_tagging([proj("p1", "Cars", "Home", tasks)], TAGS)[0]
    assert (p["count"], p["to_tag"]) == (1, ["open"])
    p = plan_tagging([proj("p1", "Cars", "Home", tasks)], TAGS,
                     include_completed=True)[0]
    assert (p["count"], p["to_tag"]) == (3, ["open", "done", "gone"])


def test_plan_tags_action_group_and_every_child():
    # The read flattens a project (flattenedTasks), so a group and its three
    # children arrive as four tasks and all four are tagged.
    tasks = [tk("group"), tk("c1"), tk("c2"), tk("c3")]
    p = plan_tagging([proj("p1", "House", "Home", tasks)], TAGS)[0]
    assert p["to_tag"] == ["group", "c1", "c2", "c3"]


def test_plan_skips_project_without_folder():
    p = plan_tagging([proj("p1", "Loose", "", [tk("a")])], TAGS)[0]
    assert p["skipped_reason"] == "no_folder"
    assert p["folder_tag"] is None
    assert p["to_tag"] == [] and p["already_tagged"] == 0
    assert p["failure"] is None


def test_plan_fails_ambiguous_folder_tag():
    tags = TAGS + [tg("a", "Areas"), tg("h2", "Home", "a")]
    p = plan_tagging([proj("p1", "Pets", "Home", [tk("a")])], tags)[0]
    assert p["failure"] == "ambiguous_tag"
    assert p["to_tag"] == []


def test_plan_missing_tag_creates_or_fails():
    projects = [proj("p1", "Labs", "Enablement", [tk("a"), tk("b")])]
    p = plan_tagging(projects, TAGS)[0]
    assert p["failure"] is None and p["to_tag"] == ["a", "b"]
    assert p["resolution"]["kind"] == "create"
    p = plan_tagging(projects, TAGS, create_missing_tags=False)[0]
    assert p["failure"] == "missing_tag" and p["to_tag"] == []


# ------------------------------ write config -----------------------------

def test_write_config_uses_only_whitelisted_ids():
    plans = plan_tagging([proj("p1", "Finances", "Home", [tk("a"), tk("b")])], TAGS)
    cfg = build_write_config(plans, valid_task_ids={"a"}, valid_tag_ids={"tHome"})
    assert cfg == {"creates": [], "projects": [
        {"id": "p1", "tagId": "tHome", "createIndex": -1, "taskIds": ["a"]}]}
    # A tag id the read did not return is dropped with its whole project.
    assert build_write_config(plans, {"a", "b"}, set())["projects"] == []


def test_write_config_creates_a_shared_new_tag_once():
    plans = plan_tagging([proj("p1", "Labs", "Enablement", [tk("a")]),
                          proj("p2", "Certs", "enablement", [tk("b")])], TAGS)
    cfg = build_write_config(plans, {"a", "b"}, set())
    assert cfg["creates"] == ["Enablement"]
    assert [p["createIndex"] for p in cfg["projects"]] == [0, 0]
    assert [p["tagId"] for p in cfg["projects"]] == ["", ""]


def test_write_config_keeps_the_raw_folder_name_to_create():
    plans = plan_tagging([proj("p1", "Lab", 'Home "Lab" Café', [tk("a")])], TAGS)
    assert build_write_config(plans, {"a"}, set())["creates"] == ['Home "Lab" Café']


def test_write_config_creates_nothing_for_a_project_with_no_tasks_in_scope():
    plans = plan_tagging([proj("p1", "Labs", "Enablement",
                               [tk("a", completed=True)])], TAGS)
    assert build_write_config(plans, {"a"}, set()) == {"creates": [], "projects": []}


def test_write_config_skips_failed_skipped_and_fully_tagged_projects():
    tags = TAGS + [tg("a", "Areas"), tg("w2", "Work", "a")]
    plans = plan_tagging([proj("p1", "Loose", "", [tk("x")]),
                          proj("p2", "Admin", "Work", [tk("y")]),
                          proj("p3", "Cars", "Home", [tk("z", ["tHome"])])], tags)
    assert build_write_config(plans, {"x", "y", "z"}, {"tHome", "tWork", "w2"}) == {
        "creates": [], "projects": []}
