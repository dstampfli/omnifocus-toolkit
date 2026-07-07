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
