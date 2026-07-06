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


# ------------------------------- read stage -------------------------------

def parse_read_result(stdout: str) -> Tuple[list, list]:
    payload = json.loads(stdout)
    return payload["tasks"], payload["unresolved"]


# Reads incomplete, non-dropped, not-yet-reviewed tasks in the named projects,
# with attachment metadata, entirely in OmniJS (attachments + tags need it).
# argv[0] = JSON {projectNames: [...], reviewTag: "..."}.
READ_TASKS_JXA = r"""
function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const of = Application('OmniFocus');
    const namesJson = JSON.stringify(cfg.projectNames);
    const tagJson = JSON.stringify(cfg.reviewTag);
    const omni =
        "(() => {" +
        "  const wanted = " + namesJson + ";" +
        "  const reviewTag = " + tagJson + ";" +
        "  const unresolved = [];" +
        "  const tasksOut = [];" +
        "  wanted.forEach(nm => {" +
        "    const proj = flattenedProjects.find(p => p && p.name === nm && p.status === Project.Status.Active);" +
        "    if (!proj) { unresolved.push(nm); return; }" +
        "    proj.flattenedTasks.forEach(t => {" +
        "      if (!t) return;" +
        "      if (t.completed || t.taskStatus === Task.Status.Dropped) return;" +
        "      const tags = (t.tags || []).map(x => x.name);" +
        "      if (tags.indexOf(reviewTag) !== -1) return;" +
        "      let atts = [];" +
        "      try { atts = t.attachments || []; } catch (e) { atts = []; }" +
        "      const meta = atts.map((a, idx) => {" +
        "        let fn = '', len = -1;" +
        "        try { fn = a.filename || a.preferredFilename || ''; } catch (e) {}" +
        "        try { len = a.contents ? a.contents.length : -1; } catch (e) {}" +
        "        return { filename: fn, byteLength: len, index: idx };" +
        "      });" +
        "      tasksOut.push({ id: t.id.primaryKey, name: t.name, note: t.note || '', attachments: meta });" +
        "    });" +
        "  });" +
        "  return JSON.stringify({ tasks: tasksOut, unresolved: unresolved });" +
        "})()";
    return of.evaluateJavascript(omni);
}
"""


def read_project_tasks(project_names, review_tag):
    cfg = json.dumps({"projectNames": project_names, "reviewTag": review_tag})
    payload = run_jxa(READ_TASKS_JXA, cfg)
    return payload["tasks"], payload["unresolved"]


# ----------------------------- review stage -----------------------------

WEB_FETCH_BETA = "web-fetch-2025-09-10"


def build_system_prompt():
    return (
        "You enrich a single OmniFocus task so its owner knows what it is "
        "without opening it. You are given the task's current name, note "
        "(which may contain a URL), and any image/PDF attachments.\n\n"
        "Read the note and attachments, and FETCH any URL the task references "
        "to understand the linked content. Then produce:\n"
        "- new_title: a concise, specific title (<= ~80 chars). If the current "
        "name is already clear, you may keep it.\n"
        "- summary: 1-3 sentences on what this is and why it matters.\n\n"
        "Base the summary on the actual fetched/attached content, not the URL "
        "string alone. Do not invent facts you cannot see."
    )


def review_task(task, client):
    content = build_task_content(
        task, fetch_attachment_b64, MAX_ATTACHMENT_BYTES, MAX_NOTE_CHARS
    )
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=1024,
        betas=[WEB_FETCH_BETA],
        tools=[{"type": "web_fetch_20260209", "name": "web_fetch",
                "max_uses": WEB_FETCH_MAX_USES}],
        system=build_system_prompt(),
        messages=[{"role": "user", "content": content}],
        output_format=Enrichment,
    )
    return resp.parsed_output


def review_tasks(tasks, review_fn=review_task):
    client = anthropic.Anthropic()
    reviewed, failed = [], []
    for task in tasks:
        try:
            enrichment = review_fn(task, client)
            reviewed.append((task, enrichment))
        except Exception as e:  # per-task isolation: never abort the whole run
            print(f"Review failed for {task.get('name', task.get('id'))!r}: {e}",
                  file=sys.stderr)
            failed.append((task, str(e)))
    return reviewed, failed
