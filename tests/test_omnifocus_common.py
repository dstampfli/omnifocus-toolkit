import base64
import plistlib

from omnifocus_common import (
    clean_note,
    media_type_for,
    attachment_block,
    build_task_content,
    extract_webloc_url,
    strip_medium_promo,
)


def test_clean_note_strips_invisible_padding():
    # U+034F (combining grapheme joiner) is the real Byrna-email padding char.
    raw = "Get expert answers" + "͏" * 3 + " so you can keep your Byrna ready"
    assert clean_note(raw) == "Get expert answers so you can keep your Byrna ready"


def test_clean_note_collapses_whitespace_and_strips():
    # horizontal runs collapse; spaces around newlines are trimmed; blank runs cap at 2.
    assert clean_note("  a\t\t b \n\n\n\n c  ") == "a b\n\nc"


def test_clean_note_truncates_to_max_chars():
    out = clean_note("x" * 100, max_chars=10)
    assert len(out) == 11 and out.endswith("…")  # 10 chars + ellipsis


def test_clean_note_empty():
    assert clean_note("") == ""
    assert clean_note(None) == ""


_MEDIUM_NOTE = (
    "Claude Code agents: what they actually are "
    "<https://medium.com/data-science-collective/claude-code-agents-2d6ea121b936> by Jose Parreño\n"
    "Download Medium on the App Store <https://apps.apple.com/us/app/medium/id828256236> "
    "or Play Store <https://play.google.com/store/apps/details?id=com.medium.reader>\n"
    "Sent from my iPhone"
)


def test_strip_medium_promo_removes_download_line():
    out = strip_medium_promo(_MEDIUM_NOTE)
    assert "Download Medium" not in out
    assert "apps.apple.com" not in out
    assert "play.google.com" not in out
    # the article link and the signature survive
    assert "Claude Code agents" in out
    assert "medium.com/data-science-collective" in out
    assert "Sent from my iPhone" in out


def test_strip_medium_promo_unchanged_when_absent():
    note = "Just a normal note\nwith two lines"
    assert strip_medium_promo(note) == note


def test_strip_medium_promo_empty():
    assert strip_medium_promo("") == ""


def test_clean_note_strips_medium_promo():
    out = clean_note(_MEDIUM_NOTE)
    assert "Download Medium" not in out
    assert "Claude Code agents" in out and "Sent from my iPhone" in out


def test_media_type_for_maps_known_extensions():
    assert media_type_for("Day_1_v3.pdf") == "application/pdf"
    assert media_type_for("photo.PNG") == "image/png"
    assert media_type_for("a.jpg") == "image/jpeg"
    assert media_type_for("a.jpeg") == "image/jpeg"
    assert media_type_for("a.gif") == "image/gif"
    assert media_type_for("a.webp") == "image/webp"


def test_media_type_for_unknown_returns_none():
    assert media_type_for("report.docx") is None
    assert media_type_for("noextension") is None
    assert media_type_for("") is None


def test_attachment_block_pdf_is_document():
    b = attachment_block("application/pdf", "QkFTRTY0")
    assert b == {"type": "document", "source": {
        "type": "base64", "media_type": "application/pdf", "data": "QkFTRTY0"}}


def test_attachment_block_image_is_image():
    b = attachment_block("image/png", "QkFTRTY0")
    assert b == {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "QkFTRTY0"}}


def _item(attachments=None, note=""):
    return {"id": "t1", "name": "Sample Task", "note": note, "attachments": attachments or []}


def test_build_task_content_no_attachments_single_text_block():
    blocks = build_task_content(_item(note="hello"), lambda tid, i: None, 1000)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert "id=t1" in blocks[0]["text"] and "Sample Task" in blocks[0]["text"]
    assert "hello" in blocks[0]["text"]


def test_build_task_content_includes_pdf_vision_block():
    item = _item(attachments=[{"filename": "Day_1_v3.pdf", "byteLength": 500, "index": 0}])
    blocks = build_task_content(item, lambda tid, i: "QkFTRTY0", 1000)
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert "Day_1_v3.pdf" in blocks[0]["text"]
    assert blocks[1] == {"type": "document", "source": {
        "type": "base64", "media_type": "application/pdf", "data": "QkFTRTY0"}}


def test_build_task_content_skips_over_cap_attachment_with_hint():
    item = _item(attachments=[{"filename": "big.pdf", "byteLength": 9_000_000, "index": 0}])
    blocks = build_task_content(item, lambda tid, i: "SHOULD_NOT_BE_CALLED", 1_000_000)
    assert len(blocks) == 1  # text only, no vision block
    assert "over size cap" in blocks[0]["text"]


def test_build_task_content_skips_unsupported_type_with_hint():
    item = _item(attachments=[{"filename": "report.docx", "byteLength": 10, "index": 0}])
    blocks = build_task_content(item, lambda tid, i: "X", 1000)
    assert len(blocks) == 1
    assert "unsupported" in blocks[0]["text"]


def test_build_task_content_skips_unknown_size_without_fetch():
    # byteLength -1 (metadata read failed) is skipped before fetch is attempted,
    # matching batch_items_by_size which counts it as out-of-scope (0 bytes).
    item = _item(attachments=[{"filename": "a.pdf", "byteLength": -1, "index": 0}])
    blocks = build_task_content(item, lambda tid, i: "SHOULD_NOT_BE_CALLED", 1000)
    assert len(blocks) == 1
    assert "unreadable" in blocks[0]["text"]


def test_build_task_content_skips_unreadable_attachment_with_hint():
    item = _item(attachments=[{"filename": "a.png", "byteLength": 10, "index": 0}])
    blocks = build_task_content(item, lambda tid, i: None, 1000)  # fetch fails
    assert len(blocks) == 1
    assert "unreadable" in blocks[0]["text"]


def test_build_task_content_cleans_note():
    blocks = build_task_content(_item(note="ready͏͏ now"), lambda tid, i: None, 1000)
    assert "ready now" in blocks[0]["text"]


# --- .webloc URL extraction ---------------------------------------------------

def _webloc_bytes(url, fmt=plistlib.FMT_XML):
    return plistlib.dumps({"URL": url}, fmt=fmt)


def test_extract_webloc_url_xml():
    raw = _webloc_bytes("https://www.udemy.com/course/x/")
    assert extract_webloc_url(raw) == "https://www.udemy.com/course/x/"


def test_extract_webloc_url_binary():
    raw = _webloc_bytes("https://example.com/bin", fmt=plistlib.FMT_BINARY)
    assert extract_webloc_url(raw) == "https://example.com/bin"


def test_extract_webloc_url_missing_key_returns_none():
    raw = plistlib.dumps({"Not-URL": "x"})
    assert extract_webloc_url(raw) is None


def test_extract_webloc_url_unparseable_returns_none():
    assert extract_webloc_url(b"not a plist at all") is None


def test_build_task_content_surfaces_webloc_url_no_vision_block():
    # A .webloc is a URL bookmark, not visual media: its URL is surfaced as text
    # (so the model can fetch it), and it produces no vision block.
    raw = _webloc_bytes("https://www.udemy.com/course/claude-architect/")
    item = _item(attachments=[{"filename": "Course.webloc", "byteLength": len(raw), "index": 0}])
    fetch = lambda tid, i: base64.b64encode(raw).decode()
    blocks = build_task_content(item, fetch, 1_000_000)
    assert len(blocks) == 1  # text only
    text = blocks[0]["text"]
    assert "https://www.udemy.com/course/claude-architect/" in text
    assert "Course.webloc" in text


def test_build_task_content_webloc_detected_via_preferred_filename():
    # OmniFocus reports .webloc names in preferredFilename (filename is null),
    # which the read stage folds into the "filename" field — case-insensitive.
    raw = _webloc_bytes("https://example.com/from-preferred")
    item = _item(attachments=[{"filename": "Bookmark.WEBLOC", "byteLength": len(raw), "index": 0}])
    fetch = lambda tid, i: base64.b64encode(raw).decode()
    blocks = build_task_content(item, fetch, 1_000_000)
    assert "https://example.com/from-preferred" in blocks[0]["text"]


def test_build_task_content_unreadable_webloc_hints_without_url():
    item = _item(attachments=[{"filename": "Course.webloc", "byteLength": 50, "index": 0}])
    blocks = build_task_content(item, lambda tid, i: None, 1_000_000)  # fetch fails
    assert len(blocks) == 1
    assert "Course.webloc" in blocks[0]["text"]
    assert "http" not in blocks[0]["text"]


import os
import pytest
from omnifocus_common import _positive_int_env


def test_positive_int_env_reads_value(monkeypatch):
    monkeypatch.setenv("WIDGET_COUNT", "7")
    assert _positive_int_env("WIDGET_COUNT", "3") == 7


def test_positive_int_env_uses_default(monkeypatch):
    monkeypatch.delenv("WIDGET_COUNT", raising=False)
    assert _positive_int_env("WIDGET_COUNT", "3") == 3


def test_positive_int_env_rejects_non_numeric(monkeypatch):
    monkeypatch.setenv("WIDGET_COUNT", "lots")
    with pytest.raises(SystemExit):
        _positive_int_env("WIDGET_COUNT", "3")


def test_positive_int_env_rejects_non_positive(monkeypatch):
    monkeypatch.setenv("WIDGET_COUNT", "0")
    with pytest.raises(SystemExit):
        _positive_int_env("WIDGET_COUNT", "3")


# ------------------------------- run_jxa ---------------------------------

import pytest

import omnifocus_common
from omnifocus_common import JxaError, run_jxa, run_jxa_or_raise


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_run(monkeypatch, result):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        return result

    monkeypatch.setattr(omnifocus_common.subprocess, "run", fake_run)
    return calls


def test_run_jxa_or_raise_returns_parsed_json(monkeypatch):
    calls = _patch_run(monkeypatch, _Result(0, '{"ok": 1}\n'))
    assert run_jxa_or_raise("function run(){}", "a", "b") == {"ok": 1}
    assert calls[0] == ["osascript", "-l", "JavaScript", "-e",
                        "function run(){}", "a", "b"]


def test_run_jxa_or_raise_raises_on_nonzero_exit(monkeypatch):
    _patch_run(monkeypatch, _Result(1, "", "execution error: boom"))
    with pytest.raises(JxaError) as info:
        run_jxa_or_raise("x")
    assert "boom" in str(info.value)


def test_run_jxa_or_raise_raises_on_non_json(monkeypatch):
    _patch_run(monkeypatch, _Result(0, "not json"))
    with pytest.raises(JxaError) as info:
        run_jxa_or_raise("x")
    assert "not json" in str(info.value)


def test_run_jxa_still_exits_on_failure(monkeypatch, capsys):
    _patch_run(monkeypatch, _Result(1, "", "execution error: boom"))
    with pytest.raises(SystemExit) as info:
        run_jxa("x")
    assert info.value.code == 1
    assert "boom" in capsys.readouterr().err


from omnifocus_common import (  # noqa: E402
    FOLDER_TAG_WRITE_JS,
    TAG_TREE_JS,
    TOP_FOLDER_JS,
    embed_js,
    folder_tag_name,
    folder_tag_write_fields,
    kanban_tag_env,
    resolve_folder_tag,
    top_folder_segment,
)


def _tag(tid, name, parent=None, in_kanban=False):
    return {"id": tid, "name": name, "parent_id": parent, "in_kanban": in_kanban}


FOLDER_TAGS = [
    _tag("tHome", "Home"), _tag("tWork", "Work"),
    _tag("tK", "Kanban", in_kanban=True),
    _tag("tRev", "Reviewed", "tK", True), _tag("tTodo", "To Do", "tK", True),
]


def test_resolve_folder_tag_matches_top_level_tag():
    assert resolve_folder_tag("Home", FOLDER_TAGS) == {
        "kind": "match", "folder": "Home", "tag_id": "tHome"}


def test_resolve_folder_tag_is_case_insensitive():
    assert resolve_folder_tag("WORK", FOLDER_TAGS)["tag_id"] == "tWork"


def test_resolve_folder_tag_matches_nested_tag_by_leaf_name():
    tags = [_tag("tA", "Areas"), _tag("tH", "Home", "tA")]
    assert resolve_folder_tag("Home", tags) == {
        "kind": "match", "folder": "Home", "tag_id": "tH"}


def test_resolve_folder_tag_never_matches_the_kanban_subtree():
    tags = [_tag("tK", "Kanban", in_kanban=True), _tag("tW", "Work", "tK", True)]
    assert resolve_folder_tag("Work", tags)["kind"] == "create"


def test_resolve_folder_tag_refuses_to_guess_between_two_leaf_matches():
    tags = [_tag("a", "Areas"), _tag("h1", "Home", "a"),
            _tag("p", "Places"), _tag("h2", "home", "p")]
    assert resolve_folder_tag("Home", tags) == {
        "kind": "ambiguous", "folder": "Home", "tag_id": None}


def test_resolve_folder_tag_creates_when_absent():
    assert resolve_folder_tag("Enablement", FOLDER_TAGS) == {
        "kind": "create", "folder": "Enablement", "tag_id": None}


def test_resolve_folder_tag_no_folder():
    for folder in ("", None, "   "):
        assert resolve_folder_tag(folder, FOLDER_TAGS) == {
            "kind": "no_folder", "folder": "", "tag_id": None}


def test_top_folder_segment():
    assert top_folder_segment("Work ▸ Customers") == "Work"
    assert top_folder_segment("Home") == "Home"
    assert top_folder_segment("") == ""
    assert top_folder_segment(None) == ""


def test_folder_tag_name():
    assert folder_tag_name(resolve_folder_tag("Home", FOLDER_TAGS)) == "Home"
    assert folder_tag_name(resolve_folder_tag("Enablement", FOLDER_TAGS)) == "Enablement"
    assert folder_tag_name({"kind": "ambiguous", "folder": "Home", "tag_id": None}) == ""
    assert folder_tag_name(resolve_folder_tag("", FOLDER_TAGS)) == ""
    assert folder_tag_name(None) == ""


def test_folder_tag_write_fields():
    none = {"folderTagId": "", "folderTagCreate": ""}
    assert folder_tag_write_fields(resolve_folder_tag("Home", FOLDER_TAGS)) == {
        "folderTagId": "tHome", "folderTagCreate": ""}
    assert folder_tag_write_fields(resolve_folder_tag("Enablement", FOLDER_TAGS)) == {
        "folderTagId": "", "folderTagCreate": "Enablement"}
    assert folder_tag_write_fields({"kind": "ambiguous", "folder": "Home", "tag_id": None}) == none
    assert folder_tag_write_fields(resolve_folder_tag("", FOLDER_TAGS)) == none
    assert folder_tag_write_fields(None) == none
    # An id that does not look like an OmniFocus id never reaches OmniJS source.
    assert folder_tag_write_fields({"kind": "match", "folder": "Home",
                                    "tag_id": 'x"); evil("'}) == none


def test_kanban_tag_env(monkeypatch):
    monkeypatch.delenv("KANBAN_TAG", raising=False)
    assert kanban_tag_env() == "Kanban"
    monkeypatch.setenv("KANBAN_TAG", "  Board ")
    assert kanban_tag_env() == "Board"
    monkeypatch.setenv("KANBAN_TAG", "   ")
    assert kanban_tag_env() == "Kanban"


def test_embed_js_inserts_fragment_as_a_js_string_literal():
    assert embed_js('"a" + __X__ + "b"', X='const q = "hi";') == \
        '"a" + "const q = \\"hi\\";" + "b"'


def test_omnijs_fragments_encode_the_rule():
    assert "String(t.status).indexOf('Dropped') === -1" in TAG_TREE_JS
    assert "in_kanban: !!inK[t.id.primaryKey]" in TAG_TREE_JS
    # Nested folders resolve to the root-most folder.
    assert "while (f.parent) f = f.parent;" in TOP_FOLDER_JS
    # New folder tags are created once per name per run, at the top level.
    assert "new Tag(nm)" in FOLDER_TAG_WRITE_JS
    assert "decodeURIComponent(enc)" in FOLDER_TAG_WRITE_JS
    for js in (TAG_TREE_JS, TOP_FOLDER_JS, FOLDER_TAG_WRITE_JS):
        assert "removeTag" not in js and "clearTags" not in js
