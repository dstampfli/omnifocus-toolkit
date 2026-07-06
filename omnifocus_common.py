#!/usr/bin/env python3
"""Shared OmniFocus task-enrichment helpers used by the triage and reviewer tools.

Turns an OmniFocus task into model-ready content: cleaned note text plus vision
content blocks (PDF/image) extracted from the task's attachments via the OmniJS
bridge. Pure helpers here have no OmniFocus dependency and are unit-tested; the
osascript/OmniJS I/O (run_jxa, fetch_attachment_b64) is added in a later task.
"""

import re
from typing import Optional

# Invisible/padding codepoints that marketing emails scatter through their text
# (soft hyphen, combining grapheme joiner, zero-width spaces/joiners, line/para
# separators, word joiner, BOM). Stripped so they don't dilute the note.
_INVISIBLE = re.compile(
    "[­͏​‌‍‎‏  ⁠﻿]"
)

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def clean_note(text: str, max_chars: int = 4000) -> str:
    """Strip invisible padding, collapse whitespace, truncate to max_chars."""
    if not text:
        return ""
    text = _INVISIBLE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)  # trim spaces around newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def media_type_for(filename: str) -> Optional[str]:
    """Return the Anthropic media_type for a filename, or None if unsupported."""
    if not filename:
        return None
    dot = filename.rfind(".")
    if dot == -1:
        return None
    return _MEDIA_TYPES.get(filename[dot:].lower())


def attachment_block(media_type: str, data_b64: str) -> dict:
    """Build one Anthropic content block: 'document' for PDF, 'image' otherwise."""
    kind = "document" if media_type == "application/pdf" else "image"
    return {
        "type": kind,
        "source": {"type": "base64", "media_type": media_type, "data": data_b64},
    }
