import omnifocus_mcp_server as server


def test_triage_inbox_passes_through(monkeypatch):
    monkeypatch.setattr(server.triage, "run_triage",
                        lambda apply=False: {"ok": True, "apply": apply})
    assert server.triage_inbox(apply=True) == {"ok": True, "apply": True}


def test_triage_inbox_defaults_to_dry_run(monkeypatch):
    seen = {}
    def fake(apply=False):
        seen["apply"] = apply
        return {}
    monkeypatch.setattr(server.triage, "run_triage", fake)
    server.triage_inbox()
    assert seen["apply"] is False


def test_triage_inbox_wraps_errors(monkeypatch):
    def boom(apply=False):
        raise RuntimeError("nope")
    monkeypatch.setattr(server.triage, "run_triage", boom)
    out = server.triage_inbox()
    assert "error" in out and "nope" in out["error"]


def test_review_tasks_passes_through(monkeypatch):
    monkeypatch.setattr(server.reviewer, "run_review",
                        lambda projects, apply=False: {"projects": projects, "apply": apply})
    assert server.review_tasks(["Training"], apply=True) == {
        "projects": ["Training"], "apply": True}


def test_review_tasks_wraps_errors(monkeypatch):
    def boom(projects, apply=False):
        raise RuntimeError("bad")
    monkeypatch.setattr(server.reviewer, "run_review", boom)
    out = server.review_tasks(["Training"])
    assert "error" in out and "bad" in out["error"]


def test_sort_project_passes_through(monkeypatch):
    monkeypatch.setattr(
        server.sorter, "run_sort",
        lambda projects, by, descending=False, apply=False: {
            "projects": projects, "by": by, "descending": descending,
            "apply": apply})
    assert server.sort_project(["Training"], "due", descending=True,
                               apply=True) == {
        "projects": ["Training"], "by": "due", "descending": True,
        "apply": True}


def test_sort_project_defaults_to_ascending_dry_run(monkeypatch):
    seen = {}

    def fake(projects, by, descending=False, apply=False):
        seen.update(descending=descending, apply=apply)
        return {}
    monkeypatch.setattr(server.sorter, "run_sort", fake)
    server.sort_project(["Training"], "title")
    assert seen == {"descending": False, "apply": False}


def test_sort_project_wraps_errors(monkeypatch):
    def boom(projects, by, descending=False, apply=False):
        raise RuntimeError("bad key")
    monkeypatch.setattr(server.sorter, "run_sort", boom)
    out = server.sort_project(["Training"], "nonsense")
    assert "error" in out and "bad key" in out["error"]


def test_sort_project_docstring_lists_every_valid_key():
    """The scheduled agent picks `by` from the docstring, so it must be complete."""
    doc = server.sort_project.__doc__
    for key in server.sorter.SORT_KEYS:
        assert key in doc, key


def test_omnifocus_status_counts(monkeypatch):
    monkeypatch.setattr(server.triage, "read_omnifocus",
                        lambda: ([1, 2, 3], [1, 2]))
    assert server.omnifocus_status() == {
        "inbox_open_count": 3, "active_project_count": 2}


def test_omnifocus_status_wraps_errors(monkeypatch):
    def boom():
        raise RuntimeError("read failed")
    monkeypatch.setattr(server.triage, "read_omnifocus", boom)
    out = server.omnifocus_status()
    assert "error" in out and "read failed" in out["error"]


def test_list_projects_returns_slim_projects(monkeypatch):
    projects = [
        {"id": "p1", "name": "Training", "folderPath": "Work ▸ Growth",
         "description": "Courses and learning material", "status": "active"},
        {"id": "p2", "name": "Personal", "description": "", "status": "active"},
    ]
    monkeypatch.setattr(server.triage, "read_omnifocus",
                        lambda: ([], projects))
    out = server.list_projects()
    assert out == {
        "projects": [
            {"id": "p1", "name": "Training", "folderPath": "Work ▸ Growth",
             "description": "Courses and learning material"},
            {"id": "p2", "name": "Personal", "folderPath": "", "description": ""},
        ],
        "count": 2,
    }


def test_list_projects_wraps_errors(monkeypatch):
    def boom():
        raise RuntimeError("no omnifocus")
    monkeypatch.setattr(server.triage, "read_omnifocus", boom)
    out = server.list_projects()
    assert "error" in out and "no omnifocus" in out["error"]
