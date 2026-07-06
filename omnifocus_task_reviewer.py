#!/usr/bin/env python3
"""OmniFocus task reviewer: enrich not-yet-reviewed tasks in named projects.

For each incomplete task in the given project(s) that does not already carry the
review tag, fetch its linked page(s) and read its attachments, then set a clearer
title and append a summary to the note. Dry-run by default; --apply writes.
"""

import json
import subprocess
import sys
from typing import List, Optional, Tuple

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

from omnifocus_common import (
    build_task_content,
    fetch_attachment_b64,
    run_jxa,
    _positive_int_env,
)

load_dotenv()

import os  # noqa: E402  (after load_dotenv so .env is present)


# ----------------------------- configuration -----------------------------
def _load_config():
    model = os.environ.get("MODEL", "claude-sonnet-5")
    tag = os.environ.get("REVIEW_TAG", "reviewed").strip() or "reviewed"
    fetches = _positive_int_env("WEB_FETCH_MAX_USES", "3")
    max_att = _positive_int_env("MAX_ATTACHMENT_BYTES", "10485760")
    max_note = _positive_int_env("MAX_NOTE_CHARS", "4000")
    return model, tag, fetches, max_att, max_note


MODEL, REVIEW_TAG, WEB_FETCH_MAX_USES, MAX_ATTACHMENT_BYTES, MAX_NOTE_CHARS = _load_config()
# --------------------------------------------------------------------------


class Enrichment(BaseModel):
    new_title: str
    summary: str


def parse_args(argv) -> Tuple[List[str], bool]:
    apply = "--apply" in argv
    projects = [a for a in argv if a != "--apply"]
    return projects, apply
