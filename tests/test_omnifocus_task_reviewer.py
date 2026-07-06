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
    for k in ("MODEL", "REVIEW_TAG", "WEB_FETCH_MAX_USES",
              "MAX_ATTACHMENT_BYTES", "MAX_NOTE_CHARS"):
        monkeypatch.delenv(k, raising=False)
    model, tag, fetches, max_att, max_note = _load_config()
    assert model == "claude-sonnet-5"
    assert tag == "reviewed"
    assert fetches == 3
    assert max_att == 10485760
    assert max_note == 4000


def test_load_config_rejects_bad_fetch_uses(monkeypatch):
    monkeypatch.setenv("WEB_FETCH_MAX_USES", "none")
    with pytest.raises(SystemExit):
        _load_config()
