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


# --------------------------- run_tag & report ----------------------------

from omnifocus_tagger import (  # noqa: E402
    READ_TAG_TASKS_JXA,
    WRITE_TAG_JXA,
    format_report,
    parse_args,
    run_tag,
)


def fake_read(projects, tags=TAGS, missing=()):
    return lambda names: (projects, tags, list(missing))


def recording_apply(calls, failed=()):
    def apply_fn(cfg):
        calls.append(cfg)
        ok = [p["id"] for p in cfg["projects"] if p["id"] not in failed]
        return ok, list(failed), list(cfg["creates"])
    return apply_fn


def no_apply(cfg):
    raise AssertionError("no write expected")


HOME = [proj("p1", "Finances", "Home", [tk("a", ["tHome"]), tk("b", ["tTodo", "tRev"])]),
        proj("p2", "Labs", "Enablement", [tk("c"), tk("d")])]


def test_run_tag_preview_writes_nothing_and_equals_apply():
    preview = run_tag(["Finances", "Labs"], read=fake_read(HOME), apply_fn=no_apply)
    calls = []
    applied = run_tag(["Finances", "Labs"], apply=True, read=fake_read(HOME),
                      apply_fn=recording_apply(calls))
    assert preview["dry_run"] is True and applied["dry_run"] is False
    assert preview["applied"] == []
    assert applied["applied"] == ["Finances", "Labs"]
    assert preview["projects"] == applied["projects"]
    assert preview["tags_created"] == applied["tags_created"] == ["Enablement"]
    assert preview["counts"] == applied["counts"] == {
        "projects": 2, "tagged": 3, "already_tagged": 1, "failed": 0, "missing": 0}
    assert preview["projects"][0] == {
        "id": "p1", "name": "Finances", "folder_tag": "Home", "count": 2,
        "tagged": 1, "already_tagged": 1, "skipped_reason": None}
    assert calls[0]["projects"][0]["taskIds"] == ["b"]   # To Do/Reviewed task


def test_run_tag_second_run_makes_no_write():
    done = [proj("p1", "Finances", "Home", [tk("a", ["tHome"]), tk("b", ["tHome"])])]
    result = run_tag(["Finances"], apply=True, read=fake_read(done), apply_fn=no_apply)
    assert result["counts"]["tagged"] == 0
    assert result["applied"] == [] and result["tags_created"] == []


def test_run_tag_missing_and_partial_write_failure_do_not_block_others():
    calls = []
    result = run_tag(["Finances", "Labs", "Ghost"], apply=True,
                     read=fake_read(HOME, missing=["Ghost"]),
                     apply_fn=recording_apply(calls, failed=["p2"]))
    assert result["missing"] == ["Ghost"]
    assert result["applied"] == ["Finances"]
    assert result["failed"] == [{"project": "Labs", "reason": "write_error"}]
    labs = [p for p in result["projects"] if p["name"] == "Labs"][0]
    assert labs["tagged"] == 0
    assert result["counts"]["failed"] == 1 and result["counts"]["missing"] == 1


def test_run_tag_reports_plan_failures_and_skips():
    tags = TAGS + [tg("a", "Areas"), tg("h2", "Home", "a")]
    projects = [proj("p1", "Pets", "Home", [tk("x")]),
                proj("p2", "Loose", "", [tk("y")]),
                proj("p3", "Labs", "Enablement", [tk("z")])]
    result = run_tag(["Pets", "Loose", "Labs"], apply=True,
                     read=fake_read(projects, tags=tags),
                     create_missing_tags=False, apply_fn=no_apply)
    assert result["failed"] == [{"project": "Pets", "reason": "ambiguous_tag"},
                                {"project": "Labs", "reason": "missing_tag"}]
    loose = [p for p in result["projects"] if p["name"] == "Loose"][0]
    assert loose["skipped_reason"] == "no_folder" and loose["folder_tag"] is None


def test_run_tag_passes_include_completed_through():
    projects = [proj("p1", "Cars", "Home", [tk("a"), tk("b", completed=True)])]
    assert run_tag(["Cars"], read=fake_read(projects))["counts"]["tagged"] == 1
    assert run_tag(["Cars"], read=fake_read(projects),
                   include_completed=True)["counts"]["tagged"] == 2


def test_run_tag_deduplicates_project_names():
    seen = []

    def read(names):
        seen.append(list(names))
        return HOME[:1], TAGS, []
    run_tag(["Finances", "Finances"], read=read)
    assert seen == [["Finances"]]


def test_run_tag_empty_projects_reads_nothing():
    def read(names):
        raise AssertionError("no read expected")
    result = run_tag([], read=read, apply_fn=no_apply)
    assert result["projects"] == [] and result["counts"]["projects"] == 0


def test_read_jxa_embeds_helpers_and_flattens_projects():
    assert "__TAG_TREE_JS__" not in READ_TAG_TASKS_JXA
    assert "const tagTree" in READ_TAG_TASKS_JXA
    assert "const topFolder" in READ_TAG_TASKS_JXA
    assert "proj.flattenedTasks" in READ_TAG_TASKS_JXA
    assert "folder: topFolder(proj)" in READ_TAG_TASKS_JXA


def test_write_jxa_only_adds_and_percent_encodes_new_names():
    assert "t.addTag(tag)" in WRITE_TAG_JXA
    for bad in ("removeTag", "clearTags", "moveTags"):
        assert bad not in WRITE_TAG_JXA
    assert "encodeURIComponent(n)" in WRITE_TAG_JXA
    assert "decodeURIComponent" in WRITE_TAG_JXA
    assert "JSON.stringify(cfg.creates)" not in WRITE_TAG_JXA


def test_format_report_preview_and_apply():
    result = run_tag(["Finances", "Labs", "Ghost"], read=fake_read(HOME, missing=["Ghost"]))
    out = format_report(result)
    assert "Would tag 1 of 2 task(s) in Finances with Home (1 already tagged)." in out
    assert "Would create tag(s): Enablement" in out
    assert "Projects not found (1): Ghost" in out
    calls = []
    out = format_report(run_tag(["Finances"], apply=True, read=fake_read(HOME[:1]),
                                apply_fn=recording_apply(calls)))
    assert "Tagged 1 of 2 task(s) in Finances with Home (1 already tagged)." in out


def test_format_report_skips_and_failures():
    tags = TAGS + [tg("a", "Areas"), tg("h2", "Home", "a")]
    out = format_report(run_tag(["Pets", "Loose"], read=fake_read(
        [proj("p1", "Pets", "Home", [tk("x")]), proj("p2", "Loose", "", [tk("y")])],
        tags=tags)))
    assert "Pets: failed (ambiguous_tag)." in out
    assert "Loose: skipped (no_folder)." in out


def test_parse_args():
    assert parse_args(["A", "B"]) == (["A", "B"], False, False, True)
    assert parse_args(["A", "--apply", "--include-completed", "--no-create"]) == (
        ["A"], True, True, False)
