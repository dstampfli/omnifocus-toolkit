import pytest
from omnifocus_task_reviewer import Enrichment, parse_args, _load_config


def _enr(title="T", synopsis="S", kind="other", verb="Read", by="", link=""):
    """An Enrichment with the model-facing fields; new_title/summary are derived."""
    return Enrichment(kind=kind, verb=verb, title=title, by=by, link=link, synopsis=synopsis)


def test_enrichment_model():
    e = Enrichment(kind="book", verb="Read", title="T", by="A", link="https://l", synopsis="S")
    assert e.new_title == "Read: T"
    assert e.summary == "Author: A\nLink: https://l\nSynopsis: S"


def test_enrichment_title_verb_is_fixed_by_kind():
    # book/video/course ignore whatever verb the model chose; only `other` uses it
    assert _enr(kind="book", verb="Watch", title="K8s").new_title == "Read: K8s"
    assert _enr(kind="video", verb="Read", title="Tokens").new_title == "Watch: Tokens"
    assert _enr(kind="course", verb="Read", title="Masterclass").new_title == "Do: Masterclass"
    assert _enr(kind="other", verb="Do", title="Sit the exam").new_title == "Do: Sit the exam"
    assert _enr(kind="other", verb="Watch", title="Keynote").new_title == "Watch: Keynote"


def test_enrichment_title_strips_duplicate_verb_prefix_and_whitespace():
    assert _enr(kind="book", title="  Read: K8s ").new_title == "Read: K8s"
    assert _enr(kind="video", title="watch: Tokens").new_title == "Watch: Tokens"
    assert _enr(kind="other", verb="Do", title="Do: Register").new_title == "Do: Register"


def test_enrichment_summary_label_follows_kind():
    assert _enr(kind="book", by="A").summary.startswith("Author: A\n")
    assert _enr(kind="video", by="C").summary.startswith("Creator: C\n")
    assert _enr(kind="course", by="I").summary.startswith("Instructors: I\n")
    assert _enr(kind="other", by="B").summary.startswith("By: B\n")


def test_enrichment_summary_omits_empty_by_and_link():
    assert _enr(by="", link="", synopsis="Only this.").summary == "Synopsis: Only this."
    assert _enr(by="  ", link="https://l", synopsis="S").summary == "Link: https://l\nSynopsis: S"
    assert _enr(kind="book", by="A", link=" ", synopsis="S").summary == "Author: A\nSynopsis: S"


def test_enrichment_schema_has_no_derived_fields():
    props = Enrichment.model_json_schema()["properties"]
    assert set(props) == {"kind", "verb", "title", "by", "link", "synopsis"}
    assert props["kind"]["enum"] == ["book", "video", "course", "other"]
    assert props["verb"]["enum"] == ["Read", "Watch", "Do"]


def test_parse_args_projects_and_apply():
    projects, apply, force = parse_args(["Training", "Tech", "--apply"])
    assert projects == ["Training", "Tech"]
    assert apply is True
    assert force is False


def test_parse_args_dry_run_default():
    projects, apply, force = parse_args(["Training"])
    assert projects == ["Training"]
    assert apply is False
    assert force is False


def test_parse_args_no_projects():
    projects, apply, force = parse_args(["--apply"])
    assert projects == []
    assert apply is True


def test_parse_args_force():
    projects, apply, force = parse_args(["Training", "--force", "--apply"])
    assert projects == ["Training"]
    assert apply is True and force is True


def test_load_config_defaults(monkeypatch):
    for k in ("MODEL", "REVIEW_TAG", "KANBAN_TAG", "WEB_FETCH_MAX_USES",
              "MAX_ATTACHMENT_BYTES", "MAX_NOTE_CHARS",
              "X_BEARER_TOKEN", "X_FETCH_MAX_USES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("REVIEW_MAX_WORKERS", raising=False)
    (model, tag, kanban, fetches, max_att, max_note,
     x_token, x_max, workers) = _load_config()
    assert model == "claude-sonnet-5"
    assert tag == "Reviewed"  # OmniFocus tag lookup is case-sensitive
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
    for word in ("book", "video", "course", "read", "watch", "do", "title",
                 "synopsis", "link", "fetch"):
        assert word in p, word
    assert "do not invent" in p


def test_review_tasks_isolates_per_task_failures():
    tasks = [{"id": "t1", "name": "one", "note": "", "attachments": []},
             {"id": "t2", "name": "two", "note": "", "attachments": []},
             {"id": "t3", "name": "three", "note": "", "attachments": []}]

    def fake_review(task, client, x_fetcher=None):
        if task["id"] == "t2":
            raise RuntimeError("boom")
        return _enr(title=task["name"].upper())

    reviewed, failed = review_tasks(tasks, review_fn=fake_review)
    assert [t["id"] for t, _ in reviewed] == ["t1", "t3"]
    assert [t["id"] for t, _ in failed] == ["t2"]
    assert reviewed[0][1].new_title == "Read: ONE"


def test_review_tasks_runs_reviews_concurrently():
    # A Barrier that only releases once N threads reach it: if review_tasks ran
    # the reviews serially, the first thread would block until the timeout and
    # every task would fail. Passing proves the reviews run in parallel.
    import threading

    n = 4
    barrier = threading.Barrier(n, timeout=5)

    def review_fn(task, client, x_fetcher=None):
        barrier.wait()
        return _enr(title=task["name"])

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
        return _enr(title=task["name"])

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
    reviewed = [(task, _enr(title="New Title", synopsis="It is about X."))]
    cfg = build_write_config(reviewed, "reviewed")
    w = cfg["writes"][0]
    assert w["taskId"] == "t1"
    assert w["newTitle"] == "Read: New Title"
    assert w["note"].startswith("original http://x")
    assert "--- Summary ---" in w["note"]
    assert "It is about X." in w["note"]
    assert cfg["reviewTag"] == "reviewed"


def test_build_write_config_includes_kanban_tag():
    task = {"id": "t1", "name": "old", "note": "", "attachments": []}
    reviewed = [(task, _enr())]
    cfg = build_write_config(reviewed, "Reviewed", "Kanban")
    assert cfg["reviewTag"] == "Reviewed"
    assert cfg["kanbanTag"] == "Kanban"


def test_write_jxa_nests_review_tag_under_kanban():
    """Reviewed tasks land in the board's Reviewed lane: the write program
    resolves the review tag as a child of the Kanban parent, reparents a stray
    tag of that name under Kanban (keeping every task's tag), and never creates
    it at the top level."""
    from omnifocus_task_reviewer import WRITE_JXA
    assert "parent.children.byName(tagName)" in WRITE_JXA
    assert "moveTags([existing], parent)" in WRITE_JXA
    assert "new Tag(tagName, parent)" in WRITE_JXA
    assert "tags.byName(tagName)" not in WRITE_JXA
    assert "tags.ending" not in WRITE_JXA


def test_build_write_config_strips_medium_promo_from_note():
    note = ("Claude Code agents <https://medium.com/x> by Jose\n"
            "Download Medium on the App Store <https://apps.apple.com/a> "
            "or Play Store <https://play.google.com/b>\n"
            "Sent from my iPhone")
    task = {"id": "t1", "name": "old", "note": note, "attachments": []}
    reviewed = [(task, _enr())]
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
    reviewed = [(task, _enr(title="a\u2028b", synopsis="c\u2029d"))]
    cfg = build_write_config(reviewed, "reviewed")
    w = cfg["writes"][0]
    assert "\u2028" not in w["newTitle"] and w["newTitle"] == "Read: ab"
    assert "\u2029" not in w["note"] and "cd" in w["note"]


from datetime import datetime


def test_build_write_config_stamps_summary_with_datetime():
    task = {"id": "t1", "name": "old", "note": "orig", "attachments": []}
    reviewed = [(task, _enr(title="T", synopsis="It is about X."))]
    now = datetime(2026, 7, 8, 12, 28)
    cfg = build_write_config(reviewed, "Reviewed", "Kanban", now=now)
    note = cfg["writes"][0]["note"]
    # stamp on its own line directly under the header, above the summary text
    assert "--- Summary ---\n07/08/2026 1228\nSynopsis: It is about X." in note


from omnifocus_task_reviewer import format_report


def _rv(task_id="t1", name="old", new="New", summary="S"):
    task = {"id": task_id, "name": name, "note": "", "attachments": []}
    return (task, _enr(title=new, synopsis=summary))


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
        read=lambda projs, rt, kt, force=False: ([_tk("t1", "old")], []),
        review=lambda tasks: ([(_tk("t1", "old"),
                                _enr(title="New"))], []),
        apply_fn=lambda rv, rt, kt: ([], []),
    )
    assert result["dry_run"] is True
    assert result["counts"] == {"reviewed": 1, "applied": 0, "failed": 0,
                                "unresolved": 0, "remaining": 0}
    assert result["reviewed"][0]["old_name"] == "old"
    assert result["reviewed"][0]["new_title"] == "Read: New"
    assert result["reviewed"][0]["summary"] == "Synopsis: S"


from omnifocus_task_reviewer import review_task
from omnifocus_x import XPostFetcher


class _FakeMessages:
    def __init__(self, captured):
        self._captured = captured

    def create(self, **kwargs):
        self._captured["content"] = kwargs["messages"][0]["content"]

        class _Block:
            type = "text"
            text = ('{"kind": "other", "verb": "Read", "title": "T", '
                    '"by": "", "link": "", "synopsis": "S"}')

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
    assert result.new_title == "Read: T"


def test_review_task_no_fetcher_unchanged():
    captured = {}
    task = {"id": "t1", "name": "Post by X on X",
            "note": "https://x.com/jack/status/20", "attachments": []}
    review_task(task, _FakeClient(captured))       # no x_fetcher
    assert "Linked X post(s):" not in captured["content"][0]["text"]


def test_run_review_apply_moves_write_failures_to_failed():
    reviewed_pairs = [(_tk("t1", "old"), _enr(title="New"))]
    result = run_review(
        ["Training"],
        apply=True,
        read=lambda projs, rt, kt, force=False: ([_tk("t1", "old")], []),
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
        read=lambda projs, rt, kt, force=False: ([], ["Ghost"]),
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
        return ([(t, _enr(title="N")) for t in tasks], [])

    result = run_review(
        ["P"],
        apply=False,
        read=lambda projs, rt, kt, force=False: (list(all_tasks), []),
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
        read=lambda projs, rt, kt, force=False: (list(all_tasks), []),
        review=lambda tasks: (
            [(t, _enr(title="N")) for t in tasks], []),
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
        read=lambda projs, rt, kt, force=False: (list(all_tasks), []),
        review=lambda tasks: (
            [(t, _enr(title="N")) for t in tasks], []),
        apply_fn=lambda rv, rt, kt: ([], []),
        max_tasks=10,
    )
    assert result["counts"]["reviewed"] == 2
    assert result["remaining"] == 0


# ------------------------- re-review (--force) support -------------------------

from omnifocus_task_reviewer import READ_TASKS_JXA, WRITE_JXA  # noqa: E402


def test_strip_summary_blocks_cuts_from_first_marker_to_end():
    from omnifocus_task_reviewer import strip_summary_blocks
    assert strip_summary_blocks("keep\n\n--- Summary ---\nx\n\n--- Summary ---\ny") == "keep"
    assert strip_summary_blocks("no marker here") == "no marker here"
    assert strip_summary_blocks("--- Summary ---\nonly a block") == ""
    assert strip_summary_blocks("") == ""


def test_build_write_config_replaces_existing_summary_blocks():
    note = ("https://example.com/book\n\n--- Summary ---\n09/16/2026 2002\nOld one.\n\n"
            "--- Summary ---\n09/17/2026 2002\nOld two.")
    task = {"id": "t1", "name": "old", "note": note, "attachments": []}
    cfg = build_write_config([(task, _enr(synopsis="Fresh."))], "Reviewed", "Kanban",
                             now=datetime(2026, 9, 23, 10, 0))
    out = cfg["writes"][0]["note"]
    assert out.count("--- Summary ---") == 1
    assert "Old one." not in out and "Old two." not in out
    assert out == "https://example.com/book\n\n--- Summary ---\n09/23/2026 1000\nSynopsis: Fresh."


def test_format_report_indents_every_summary_line():
    task = {"id": "t1", "name": "old", "note": "", "attachments": []}
    e = _enr(kind="book", title="K8s", by="A", link="https://l", synopsis="S")
    out = format_report([(task, e)], [], [], [], dry_run=True)
    assert "  * old  ->  Read: K8s" in out
    assert "\n      Author: A\n      Link: https://l\n      Synopsis: S" in out


def test_read_project_tasks_passes_force_to_jxa(monkeypatch):
    import json
    import omnifocus_task_reviewer as m
    captured = {}

    def fake_run_jxa(prog, cfg):
        captured["cfg"] = json.loads(cfg)
        return {"tasks": [], "unresolved": []}
    monkeypatch.setattr(m, "run_jxa", fake_run_jxa)
    m.read_project_tasks(["P"], "Reviewed", "Kanban", force=True)
    assert captured["cfg"]["force"] is True
    m.read_project_tasks(["P"], "Reviewed", "Kanban")
    assert captured["cfg"]["force"] is False


def test_read_tasks_jxa_skips_board_and_review_tag_only_without_force():
    # The OmniJS read must consult the force flag before applying its two skips.
    assert "cfg.force" in READ_TASKS_JXA
    assert "if (!force" in READ_TASKS_JXA


def test_write_jxa_does_not_add_review_tag_to_task_already_in_a_lane():
    # A re-reviewed task that sits in To Do / In Progress / ... must stay there:
    # the write program adds the review tag only to tasks carrying no Kanban tag.
    assert "kanbanIds" in WRITE_JXA
    assert "const inLane = (t.tags || []).some(x => kanbanIds[x.id.primaryKey]);" in WRITE_JXA
    assert "if (!inLane) t.addTag(tag);" in WRITE_JXA
    assert "      t.addTag(tag);" not in WRITE_JXA


def test_run_review_force_reaches_read():
    seen = {}

    def read(projs, rt, kt, force=False):
        seen["force"] = force
        return ([], [])
    run_review(["P"], apply=False, force=True, read=read,
               review=lambda tasks: ([], []), apply_fn=lambda rv, rt, kt: ([], []))
    assert seen["force"] is True
    run_review(["P"], apply=False, read=read,
               review=lambda tasks: ([], []), apply_fn=lambda rv, rt, kt: ([], []))
    assert seen["force"] is False


def test_read_tasks_jxa_returns_project_folder():
    assert "proj.parentFolder ? proj.parentFolder.name : ''" in READ_TASKS_JXA
    assert "folder: folder" in READ_TASKS_JXA


def test_write_jxa_tags_task_with_its_project_folder():
    # The folder is resolved live in OmniJS (no hard-coded list), a folderless
    # project adds nothing, and a folder tag never resolves to a Kanban lane.
    assert "const folder = proj && proj.parentFolder;" in WRITE_JXA
    assert "if (folder) t.addTag(folderTag(folder.name));" in WRITE_JXA
    assert "!kanbanIds[x.id.primaryKey]" in WRITE_JXA
    for name in ("Personal", "Home", "Enablement", "Work"):
        assert name not in WRITE_JXA


def test_format_report_shows_folder_tag_only_when_foldered():
    task, e = _rv()
    assert "Tag: Home" in format_report([({**task, "folder": "Home"}, e)], [], [], [], dry_run=True)
    assert "Tag:" not in format_report([(task, e)], [], [], [], dry_run=True)


def test_run_review_reports_folder_tag():
    task = {**_tk("t1", "old"), "folder": "Work"}
    result = run_review(
        ["P"], apply=False,
        read=lambda projs, rt, kt, force=False: ([task], []),
        review=lambda tasks: ([(task, _enr(title="New"))], []),
        apply_fn=lambda rv, rt, kt: ([], []),
    )
    assert result["reviewed"][0]["folder_tag"] == "Work"
