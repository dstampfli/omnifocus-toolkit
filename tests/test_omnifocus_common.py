from omnifocus_common import clean_note, media_type_for, attachment_block


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
