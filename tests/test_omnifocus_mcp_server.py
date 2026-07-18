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
    monkeypatch.setattr(
        server.reviewer, "run_review",
        lambda projects, apply=False, max_tasks=None: {
            "projects": projects, "apply": apply, "max_tasks": max_tasks})
    assert server.review_tasks(["Training"], apply=True, max_tasks=3) == {
        "projects": ["Training"], "apply": True, "max_tasks": 3}


def test_review_tasks_defaults_max_tasks(monkeypatch):
    seen = {}

    def fake(projects, apply=False, max_tasks=None):
        seen["max_tasks"] = max_tasks
        return {}
    monkeypatch.setattr(server.reviewer, "run_review", fake)
    server.review_tasks(["Training"])
    assert seen["max_tasks"] == server.DEFAULT_MAX_TASKS
    assert isinstance(server.DEFAULT_MAX_TASKS, int) and server.DEFAULT_MAX_TASKS > 0


def test_review_tasks_wraps_errors(monkeypatch):
    def boom(projects, apply=False, max_tasks=None):
        raise RuntimeError("bad")
    monkeypatch.setattr(server.reviewer, "run_review", boom)
    out = server.review_tasks(["Training"])
    assert "error" in out and "bad" in out["error"]


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
