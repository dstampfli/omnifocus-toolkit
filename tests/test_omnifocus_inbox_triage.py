import pytest

from omnifocus_inbox_triage import (
    Decision,
    should_move,
    partition_decisions,
    parse_read_result,
    active_projects,
    _load_config,
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


def test_should_move_rejects_medium_when_threshold_high():
    # Pass min_confidence explicitly so the test is independent of the ambient
    # MOVE_MIN_CONFIDENCE (a developer's .env may set it to `medium`).
    assert should_move(mk(confidence="medium"), {"t1"}, {"p1"}, min_confidence="high") is False


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
    to_move, to_leave = partition_decisions(decisions, ["t1", "t2"], ["p1"], min_confidence="high")
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
from omnifocus_inbox_triage import build_system_prompt, build_user_message, batch_items_by_size


def test_build_system_prompt_mentions_key_rules():
    prompt = build_system_prompt().lower()
    assert "project" in prompt
    assert "confidence" in prompt
    assert "description" in prompt


def _proj(pid="p1", name="Pets", desc="Pet care"):
    return {"id": pid, "name": name, "folderPath": "", "description": desc}


def _mkitem(item_id, attachments=None):
    return {"id": item_id, "name": "n-" + item_id, "note": "", "attachments": attachments or []}


def test_build_user_message_projects_block_then_items():
    items = [_mkitem("t1"), _mkitem("t2")]
    projects = [_proj()]
    content = build_user_message(items, projects, lambda tid, i: None, 1000, 4000)
    assert content[0]["type"] == "text"
    assert "Pets" in content[0]["text"] and "Pet care" in content[0]["text"]
    # both item headers present, no status leaked
    joined = "".join(b.get("text", "") for b in content if b["type"] == "text")
    assert "id=t1" in joined and "id=t2" in joined
    assert "status" not in content[0]["text"]


def test_build_user_message_includes_vision_block():
    items = [_mkitem("t1", [{"filename": "a.pdf", "byteLength": 100, "index": 0}])]
    content = build_user_message(items, [_proj()], lambda tid, i: "B64", 1000, 4000)
    assert any(b["type"] == "document" for b in content)


def test_batch_by_count():
    items = [_mkitem(f"t{i}") for i in range(5)]
    batches = [list(b) for b in batch_items_by_size(items, 2, 1000, 10000)]
    assert [len(b) for b in batches] == [2, 2, 1]


def test_batch_flushes_when_attachment_budget_exceeded():
    items = [
        _mkitem("t1", [{"filename": "a.pdf", "byteLength": 800, "index": 0}]),
        _mkitem("t2", [{"filename": "b.pdf", "byteLength": 800, "index": 0}]),
    ]
    # budget 1000: t1 (800) fits; adding t2 (800) -> 1600 > 1000 -> flush.
    batches = [list(b) for b in batch_items_by_size(items, 25, 5000, 1000)]
    assert [[i["id"] for i in b] for b in batches] == [["t1"], ["t2"]]


def test_batch_ignores_out_of_scope_bytes():
    # over-cap and unsupported attachments contribute 0 to the batch budget.
    items = [
        _mkitem("t1", [{"filename": "big.pdf", "byteLength": 9_000_000, "index": 0}]),
        _mkitem("t2", [{"filename": "x.zip", "byteLength": 9_000_000, "index": 0}]),
    ]
    batches = [list(b) for b in batch_items_by_size(items, 25, 1_000_000, 2_000_000)]
    assert len(batches) == 1 and len(batches[0]) == 2


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


def test_load_config_defaults(monkeypatch):
    for k in ("MODEL", "MOVE_MIN_CONFIDENCE", "CHUNK_SIZE",
              "MAX_ATTACHMENT_BYTES", "MAX_BATCH_ATTACHMENT_BYTES", "MAX_NOTE_CHARS",
              "X_BEARER_TOKEN", "X_FETCH_MAX_USES"):
        monkeypatch.delenv(k, raising=False)
    model, conf, chunk, max_att, max_batch, max_note, x_token, x_max = _load_config()
    assert model == "claude-sonnet-5"
    assert conf == "high"
    assert chunk == 25
    assert max_att == 10485760
    assert max_batch == 20971520
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


def test_load_config_rejects_bad_attachment_cap(monkeypatch):
    monkeypatch.setenv("MAX_ATTACHMENT_BYTES", "lots")
    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_rejects_non_positive_note_cap(monkeypatch):
    monkeypatch.setenv("MAX_NOTE_CHARS", "0")
    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_normalizes_confidence_case(monkeypatch):
    monkeypatch.setenv("MOVE_MIN_CONFIDENCE", "Medium")
    _, conf, _, _, _, _, _, _ = _load_config()
    assert conf == "medium"


def test_load_config_rejects_bad_confidence(monkeypatch):
    monkeypatch.setenv("MOVE_MIN_CONFIDENCE", "hi")
    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_rejects_non_numeric_chunk_size(monkeypatch):
    monkeypatch.setenv("CHUNK_SIZE", "lots")
    with pytest.raises(SystemExit):
        _load_config()


def test_load_config_rejects_non_positive_chunk_size(monkeypatch):
    monkeypatch.setenv("CHUNK_SIZE", "0")
    with pytest.raises(SystemExit):
        _load_config()


from omnifocus_inbox_triage import run_triage, Classification


def test_run_triage_dry_run_builds_moved_and_left():
    items = [{"id": "t1", "name": "Vet appt", "note": ""},
             {"id": "t2", "name": "Idea", "note": ""}]
    decisions = Classification(decisions=[
        Decision(item_id="t1", project_id="p1", project_name="Pets",
                 confidence="high", reason="cat"),
        Decision(item_id="t2", project_id=None, project_name="",
                 confidence="low", reason="unclear"),
    ])
    result = run_triage(
        apply=False,
        read=lambda: (items, [_proj()]),
        classify=lambda i, p: decisions,
        apply_fn=lambda m: ([], []),
    )
    assert result["dry_run"] is True
    assert result["counts"] == {"inbox": 2, "moved": 1, "left": 1, "failed": 0}
    assert result["moved"][0]["id"] == "t1"
    assert result["moved"][0]["name"] == "Vet appt"
    assert result["moved"][0]["project"] == "Pets"
    assert result["left"][0]["id"] == "t2"


def test_run_triage_apply_reports_failed_moves():
    items = [{"id": "t1", "name": "Vet appt", "note": ""}]
    decisions = Classification(decisions=[
        Decision(item_id="t1", project_id="p1", project_name="Pets",
                 confidence="high", reason="cat")])
    result = run_triage(
        apply=True,
        read=lambda: (items, [_proj()]),
        classify=lambda i, p: decisions,
        apply_fn=lambda m: ([], ["t1"]),   # t1 failed to move
    )
    assert result["dry_run"] is False
    assert result["counts"]["moved"] == 0
    assert result["counts"]["failed"] == 1
    assert result["failed"][0]["id"] == "t1"


def test_run_triage_empty_inbox_skips_classify():
    called = []
    result = run_triage(
        apply=False,
        read=lambda: ([], []),
        classify=lambda i, p: called.append(1),
        apply_fn=lambda m: ([], []),
    )
    assert called == []          # classify never invoked on empty inbox
    assert result["counts"]["inbox"] == 0
    assert result["moved"] == [] and result["left"] == []


from omnifocus_inbox_triage import build_user_message
from omnifocus_x import XPostFetcher


def _no_bytes(task_id, index):
    return None


def test_build_user_message_appends_x_post_text():
    items = [{"id": "t1", "name": "read this",
              "note": "https://x.com/jack/status/20", "attachments": []}]
    projects = [{"id": "p1", "name": "Reading", "folderPath": "", "description": ""}]
    fetcher = XPostFetcher("tok", 25, fetch_fn=lambda tid, tok: f"X post by jack (@jack): hi {tid}")
    content = build_user_message(items, projects, _no_bytes, 1000, 4000, x_fetcher=fetcher)
    header = content[1]["text"]   # content[0] is the PROJECTS block
    assert "Linked X post(s):" in header
    assert "X post by jack (@jack): hi 20" in header


def test_build_user_message_no_fetcher_unchanged():
    items = [{"id": "t1", "name": "read this",
              "note": "https://x.com/jack/status/20", "attachments": []}]
    projects = [{"id": "p1", "name": "Reading", "folderPath": "", "description": ""}]
    content = build_user_message(items, projects, _no_bytes, 1000, 4000)
    assert "Linked X post(s):" not in content[1]["text"]
