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
    monkeypatch.delenv("REVIEW_MAX_WORKERS", raising=False)
    (model, tag, kanban, fetches, max_att, max_note,
     x_token, x_max, workers) = _load_config()
    assert model == "claude-sonnet-5"
    assert tag == "reviewed"
    assert kanban == "Kanban"
    assert fetches == 3
    assert max_att == 10485760
    assert max_note == 4000
    assert x_token is None
    assert x_max == 25
    assert workers == 5


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

    def fake_review(task, client, x_fetcher=None):
        if task["id"] == "t2":
            raise RuntimeError("boom")
        return Enrichment(new_title=task["name"].upper(), summary="s")

    reviewed, failed = review_tasks(tasks, review_fn=fake_review)
    assert [t["id"] for t, _ in reviewed] == ["t1", "t3"]
    assert [t["id"] for t, _ in failed] == ["t2"]
    assert reviewed[0][1].new_title == "ONE"


def test_review_tasks_runs_reviews_concurrently():
    # A Barrier that only releases once N threads reach it: if review_tasks ran
    # the reviews serially, the first thread would block until the timeout and
    # every task would fail. Passing proves the reviews run in parallel.
    import threading

    n = 4
    barrier = threading.Barrier(n, timeout=5)

    def review_fn(task, client, x_fetcher=None):
        barrier.wait()
        return Enrichment(new_title=task["name"], summary="s")

    tasks = [{"id": f"t{i}", "name": f"n{i}", "note": "", "attachments": []}
             for i in range(n)]
    reviewed, failed = review_tasks(tasks, review_fn=review_fn)
    assert failed == []
    assert [t["id"] for t, _ in reviewed] == ["t0", "t1", "t2", "t3"]  # order kept


def test_review_tasks_preserves_order_under_concurrency():
    # Reviews finish out of order, but results must follow input order.
    import threading

    release = {i: threading.Event() for i in range(3)}

    def review_fn(task, client, x_fetcher=None):
        i = int(task["id"][1:])
        release[i].wait(timeout=5)              # gated so t2 finishes first
        return Enrichment(new_title=task["name"], summary="s")

    tasks = [{"id": f"t{i}", "name": f"n{i}", "note": "", "attachments": []}
             for i in range(3)]
    # Let them finish in reverse order.
    for i in (2, 1, 0):
        release[i].set()
    reviewed, _ = review_tasks(tasks, review_fn=review_fn)
    assert [t["id"] for t, _ in reviewed] == ["t0", "t1", "t2"]


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
    assert result["counts"] == {"reviewed": 1, "applied": 0, "failed": 0,
                                "unresolved": 0, "remaining": 0}
    assert result["reviewed"][0]["old_name"] == "old"
    assert result["reviewed"][0]["new_title"] == "New"
    assert result["reviewed"][0]["summary"] == "S"


from omnifocus_task_reviewer import review_task
from omnifocus_x import XPostFetcher


class _FakeMessages:
    def __init__(self, captured):
        self._captured = captured

    def create(self, **kwargs):
        self._captured["content"] = kwargs["messages"][0]["content"]

        class _Block:
            type = "text"
            text = '{"new_title": "T", "summary": "S"}'

        class _Resp:
            content = [_Block()]

        return _Resp()


class _FakeClient:
    def __init__(self, captured):
        self.beta = type("B", (), {"messages": _FakeMessages(captured)})()


def test_review_task_appends_x_post_text():
    captured = {}
    task = {"id": "t1", "name": "Post by X on X",
            "note": "https://x.com/jack/status/20", "attachments": []}
    fetcher = XPostFetcher("tok", 25, fetch_fn=lambda tid, tok: f"X post by jack (@jack): hi {tid}")
    result = review_task(task, _FakeClient(captured), x_fetcher=fetcher)
    header = captured["content"][0]["text"]
    assert "Linked X post(s):" in header
    assert "X post by jack (@jack): hi 20" in header
    assert result.new_title == "T"


def test_review_task_no_fetcher_unchanged():
    captured = {}
    task = {"id": "t1", "name": "Post by X on X",
            "note": "https://x.com/jack/status/20", "attachments": []}
    review_task(task, _FakeClient(captured))       # no x_fetcher
    assert "Linked X post(s):" not in captured["content"][0]["text"]


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


def test_run_review_caps_at_max_tasks_and_reports_remaining():
    all_tasks = [_tk(f"t{i}", f"n{i}") for i in range(5)]
    seen = {}

    def review(tasks):
        seen["count"] = len(tasks)
        seen["ids"] = [t["id"] for t in tasks]
        return ([(t, Enrichment(new_title="N", summary="S")) for t in tasks], [])

    result = run_review(
        ["P"],
        apply=False,
        read=lambda projs, rt, kt: (list(all_tasks), []),
        review=review,
        apply_fn=lambda rv, rt, kt: ([], []),
        max_tasks=2,
    )
    assert seen["count"] == 2                      # only the first 2 reviewed
    assert seen["ids"] == ["t0", "t1"]
    assert result["counts"]["reviewed"] == 2
    assert result["counts"]["remaining"] == 3      # 5 read - 2 reviewed
    assert result["remaining"] == 3


def test_run_review_no_cap_reviews_all_with_zero_remaining():
    all_tasks = [_tk(f"t{i}") for i in range(3)]
    result = run_review(
        ["P"],
        apply=False,
        read=lambda projs, rt, kt: (list(all_tasks), []),
        review=lambda tasks: (
            [(t, Enrichment(new_title="N", summary="S")) for t in tasks], []),
        apply_fn=lambda rv, rt, kt: ([], []),
    )
    assert result["counts"]["reviewed"] == 3
    assert result["counts"]["remaining"] == 0
    assert result["remaining"] == 0


def test_run_review_max_tasks_above_count_reviews_all():
    all_tasks = [_tk(f"t{i}") for i in range(2)]
    result = run_review(
        ["P"],
        apply=False,
        read=lambda projs, rt, kt: (list(all_tasks), []),
        review=lambda tasks: (
            [(t, Enrichment(new_title="N", summary="S")) for t in tasks], []),
        apply_fn=lambda rv, rt, kt: ([], []),
        max_tasks=10,
    )
    assert result["counts"]["reviewed"] == 2
    assert result["remaining"] == 0
