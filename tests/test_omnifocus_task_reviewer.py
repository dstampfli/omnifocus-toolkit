import pytest
from omnifocus_task_reviewer import Enrichment, parse_args, _load_config


def test_enrichment_model():
    e = Enrichment(new_title="T", summary="S")
    assert e.new_title == "T" and e.summary == "S"


def test_parse_args_projects_and_apply():
    projects, apply = parse_args(["Training", "Tech", "--apply"])
    assert projects == ["Training", "Tech"]
    assert apply is True


def test_parse_args_dry_run_default():
    projects, apply = parse_args(["Training"])
    assert projects == ["Training"]
    assert apply is False


def test_parse_args_no_projects():
    projects, apply = parse_args(["--apply"])
    assert projects == []
    assert apply is True


def test_load_config_defaults(monkeypatch):
    for k in ("MODEL", "REVIEW_TAG", "KANBAN_TAG", "WEB_FETCH_MAX_USES",
              "MAX_ATTACHMENT_BYTES", "MAX_NOTE_CHARS",
              "X_BEARER_TOKEN", "X_FETCH_MAX_USES"):
        monkeypatch.delenv(k, raising=False)
    (model, tag, kanban, fetches, max_att, max_note,
     x_token, x_max) = _load_config()
    assert model == "claude-sonnet-5"
    assert tag == "reviewed"
    assert kanban == "Kanban"
    assert fetches == 3
    assert max_att == 10485760
    assert max_note == 4000
    assert x_token is None
    assert x_max == 25


def test_load_config_x_token_stripped_or_none(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "   ")
    assert _load_config()[6] is None            # whitespace-only -> None
    monkeypatch.setenv("X_BEARER_TOKEN", "  abc ")
    assert _load_config()[6] == "abc"           # trimmed


def test_load_config_rejects_bad_x_fetch_max(monkeypatch):
    monkeypatch.setenv("X_FETCH_MAX_USES", "none")
    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_rejects_bad_fetch_uses(monkeypatch):
    monkeypatch.setenv("WEB_FETCH_MAX_USES", "none")
    with pytest.raises(SystemExit):
        _load_config()


from omnifocus_task_reviewer import parse_read_result


def test_parse_read_result_splits_tasks_and_unresolved():
    stdout = (
        '{"tasks": [{"id": "t1", "name": "A story", "note": "http://x",'
        ' "attachments": [{"filename": "a.jpg", "byteLength": 10, "index": 0}]}],'
        ' "unresolved": ["Nope"]}'
    )
    tasks, unresolved = parse_read_result(stdout)
    assert tasks[0]["id"] == "t1"
    assert tasks[0]["attachments"][0]["filename"] == "a.jpg"
    assert unresolved == ["Nope"]


from omnifocus_task_reviewer import build_system_prompt, review_tasks


def test_build_system_prompt_mentions_key_rules():
    p = build_system_prompt().lower()
    assert "title" in p
    assert "summary" in p
    assert "fetch" in p  # instructs the model to fetch URLs


def test_review_tasks_isolates_per_task_failures():
    tasks = [{"id": "t1", "name": "one", "note": "", "attachments": []},
             {"id": "t2", "name": "two", "note": "", "attachments": []},
             {"id": "t3", "name": "three", "note": "", "attachments": []}]

    def fake_review(task, client):
        if task["id"] == "t2":
            raise RuntimeError("boom")
        return Enrichment(new_title=task["name"].upper(), summary="s")

    reviewed, failed = review_tasks(tasks, review_fn=fake_review)
    assert [t["id"] for t, _ in reviewed] == ["t1", "t3"]
    assert [t["id"] for t, _ in failed] == ["t2"]
    assert reviewed[0][1].new_title == "ONE"


from omnifocus_task_reviewer import build_write_config


def test_build_write_config_appends_summary_preserving_note():
    task = {"id": "t1", "name": "old", "note": "original http://x", "attachments": []}
    reviewed = [(task, Enrichment(new_title="New Title", summary="It is about X."))]
    cfg = build_write_config(reviewed, "reviewed")
    w = cfg["writes"][0]
    assert w["taskId"] == "t1"
    assert w["newTitle"] == "New Title"
    assert w["note"].startswith("original http://x")
    assert "--- Summary ---" in w["note"]
    assert "It is about X." in w["note"]
    assert cfg["reviewTag"] == "reviewed"


def test_build_write_config_includes_kanban_tag():
    task = {"id": "t1", "name": "old", "note": "", "attachments": []}
    reviewed = [(task, Enrichment(new_title="T", summary="S"))]
    cfg = build_write_config(reviewed, "Reviewed", "Kanban")
    assert cfg["reviewTag"] == "Reviewed"
    assert cfg["kanbanTag"] == "Kanban"


def test_build_write_config_strips_medium_promo_from_note():
    note = ("Claude Code agents <https://medium.com/x> by Jose\n"
            "Download Medium on the App Store <https://apps.apple.com/a> "
            "or Play Store <https://play.google.com/b>\n"
            "Sent from my iPhone")
    task = {"id": "t1", "name": "old", "note": note, "attachments": []}
    reviewed = [(task, Enrichment(new_title="T", summary="S"))]
    cfg = build_write_config(reviewed, "reviewed")
    w = cfg["writes"][0]
    assert "Download Medium" not in w["note"]
    assert "apps.apple.com" not in w["note"]
    assert "Claude Code agents" in w["note"]
    assert "Sent from my iPhone" in w["note"]
    assert "--- Summary ---" in w["note"]


def test_build_write_config_strips_line_separators():
    # U+2028 / U+2029 in model text must not survive into the write payload.
    task = {"id": "t1", "name": "old", "note": "", "attachments": []}
    reviewed = [(task, Enrichment(new_title="a\u2028b", summary="c\u2029d"))]
    cfg = build_write_config(reviewed, "reviewed")
    w = cfg["writes"][0]
    assert "\u2028" not in w["newTitle"] and w["newTitle"] == "ab"
    assert "\u2029" not in w["note"] and "cd" in w["note"]


from datetime import datetime


def test_build_write_config_stamps_summary_with_datetime():
    task = {"id": "t1", "name": "old", "note": "orig", "attachments": []}
    reviewed = [(task, Enrichment(new_title="T", summary="It is about X."))]
    now = datetime(2026, 7, 8, 12, 28)
    cfg = build_write_config(reviewed, "Reviewed", "Kanban", now=now)
    note = cfg["writes"][0]["note"]
    # stamp on its own line directly under the header, above the summary text
    assert "--- Summary ---\n07/08/2026 1228\nIt is about X." in note


from omnifocus_task_reviewer import format_report


def _rv(task_id="t1", name="old", new="New", summary="S"):
    task = {"id": task_id, "name": name, "note": "", "attachments": []}
    return (task, Enrichment(new_title=new, summary=summary))


def test_format_report_dry_run_shows_proposed():
    out = format_report([_rv()], [], [], [], dry_run=True)
    assert "Would enrich" in out
    assert "old" in out and "New" in out


def test_format_report_apply_shows_enriched():
    out = format_report([_rv()], [], [], ["New"], dry_run=False)
    assert "Enriched" in out


def test_format_report_lists_failures_and_unresolved():
    task = {"id": "t2", "name": "bad", "note": "", "attachments": []}
    out = format_report([], [(task, "boom")], ["NoProj"], [], dry_run=True)
    assert "Failed" in out and "bad" in out
    assert "NoProj" in out


from omnifocus_task_reviewer import run_review


def _tk(tid="t1", name="old"):
    return {"id": tid, "name": name, "note": "", "attachments": []}


def test_run_review_dry_run_builds_reviewed():
    result = run_review(
        ["Training"],
        apply=False,
        read=lambda projs, rt, kt: ([_tk("t1", "old")], []),
        review=lambda tasks: ([(_tk("t1", "old"),
                                Enrichment(new_title="New", summary="S"))], []),
        apply_fn=lambda rv, rt, kt: ([], []),
    )
    assert result["dry_run"] is True
    assert result["counts"] == {"reviewed": 1, "applied": 0, "failed": 0, "unresolved": 0}
    assert result["reviewed"][0]["old_name"] == "old"
    assert result["reviewed"][0]["new_title"] == "New"
    assert result["reviewed"][0]["summary"] == "S"


def test_run_review_apply_moves_write_failures_to_failed():
    reviewed_pairs = [(_tk("t1", "old"), Enrichment(new_title="New", summary="S"))]
    result = run_review(
        ["Training"],
        apply=True,
        read=lambda projs, rt, kt: ([_tk("t1", "old")], []),
        review=lambda tasks: (list(reviewed_pairs), []),
        apply_fn=lambda rv, rt, kt: ([], ["t1"]),   # write failed for t1
    )
    assert result["counts"]["reviewed"] == 0
    assert result["counts"]["failed"] == 1
    assert result["failed"][0]["id"] == "t1"
    assert result["failed"][0]["error"] == "write failed"


def test_run_review_reports_unresolved_projects():
    result = run_review(
        ["Ghost"],
        apply=False,
        read=lambda projs, rt, kt: ([], ["Ghost"]),
        review=lambda tasks: ([], []),
        apply_fn=lambda rv, rt, kt: ([], []),
    )
    assert result["unresolved"] == ["Ghost"]
    assert result["counts"]["unresolved"] == 1
