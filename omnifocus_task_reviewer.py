#!/usr/bin/env python3
"""OmniFocus task reviewer: enrich not-yet-reviewed tasks in named projects.

For each incomplete task in the given project(s) that does not already carry the
review tag, fetch its linked page(s) and read its attachments, then set a clearer
title and append a summary to the note. Dry-run by default; --apply writes.
"""

import json
import subprocess
import sys
from typing import List, Tuple

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
    if not tasks:
        return [], []
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


# ------------------------------- apply stage -------------------------------

import re  # noqa: E402

# Strip line/paragraph separators and C0/C1 control chars (except \n and \t)
# from model text before it is written back. Defence in depth: the title/note
# go through JXA argv (already injection-safe), but this keeps the note clean.
_UNSAFE = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u2028\u2029]")


def _sanitize(text):
    return _UNSAFE.sub("", text or "")


def build_write_config(reviewed, review_tag):
    writes = []
    for task, enrichment in reviewed:
        title = _sanitize(enrichment.new_title).strip()
        summary = _sanitize(enrichment.summary).strip()
        original = task.get("note", "")
        note = f"{original}\n\n--- Summary ---\n{summary}" if original else f"--- Summary ---\n{summary}"
        writes.append({"taskId": task["id"], "newTitle": title, "note": note})
    return {"writes": writes, "reviewTag": review_tag}


# Plain JXA sets name/note from argv (injection-safe for arbitrary text); the
# OmniJS bridge then adds the review tag, embedding ONLY task ids + the tag name.
WRITE_JXA = r"""
function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const of = Application('OmniFocus');
    const doc = of.defaultDocument;

    const byId = {};
    cfg.writes.forEach(w => { byId[w.taskId] = w; });

    const done = {};
    const failed = [];
    const all = doc.flattenedTasks();
    for (let i = 0; i < all.length; i++) {
        const t = all[i];
        let id;
        try { id = t.id(); } catch (e) { continue; }
        const w = byId[id];
        if (!w) continue;
        try {
            t.name = w.newTitle;
            t.note = w.note;
            done[id] = true;
        } catch (e) { failed.push(id); }
    }

    let applied = [];
    const ids = Object.keys(done);
    if (ids.length) {
        const tagRes = of.evaluateJavascript(
            "(() => {" +
            "  const ids = " + JSON.stringify(ids) + ";" +
            "  const name = " + JSON.stringify(cfg.reviewTag) + ";" +
            "  let tag = flattenedTags.byName(name) || new Tag(name);" +
            "  const ok = [];" +
            "  ids.forEach(id => { const t = Task.byIdentifier(id); if (t) { t.addTag(tag); ok.push(t.name); } });" +
            "  return JSON.stringify(ok);" +
            "})()"
        );
        applied = JSON.parse(tagRes);
    }

    cfg.writes.forEach(w => {
        if (!done[w.taskId] && failed.indexOf(w.taskId) === -1) failed.push(w.taskId);
    });

    return JSON.stringify({ applied: applied, failed: failed });
}
"""


def apply_enrichments(reviewed, review_tag):
    cfg = json.dumps(build_write_config(reviewed, review_tag))
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", WRITE_JXA, cfg],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("osascript (apply) failed:", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(1)
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        print("osascript (apply) returned unexpected output:", file=sys.stderr)
        print(result.stdout.strip(), file=sys.stderr)
        raise SystemExit(1)
    return payload.get("applied", []), payload.get("failed", [])


# --------------------------- reporting & CLI --------------------------------

def format_report(reviewed, failed, unresolved, applied_names, dry_run):
    lines = []
    if reviewed:
        header = "Would enrich" if dry_run else "Enriched"
        lines.append(f"{header} {len(reviewed)} task(s):")
        for task, enrichment in reviewed:
            lines.append(f"  * {task['name']}  ->  {enrichment.new_title}")
            lines.append(f"      {enrichment.summary}")
    if failed:
        lines.append("")
        lines.append(f"Failed ({len(failed)}):")
        for task, err in failed:
            lines.append(f"  = {task['name']} - {err}")
    if unresolved:
        lines.append("")
        lines.append(f"Projects not found ({len(unresolved)}): {', '.join(unresolved)}")
    if not reviewed and not failed:
        lines.append("Nothing to review.")
    return "\n".join(lines)


def main(argv):
    projects, apply = parse_args(argv)
    if not projects:
        print("usage: omnifocus_task_reviewer.py PROJECT [PROJECT ...] [--apply]",
              file=sys.stderr)
        return 2

    tasks, unresolved = read_project_tasks(projects, REVIEW_TAG)
    reviewed, failed = review_tasks(tasks)

    applied_names = []
    if apply and reviewed:
        applied_names, write_failed_ids = apply_enrichments(reviewed, REVIEW_TAG)
        if write_failed_ids:
            failed_set = set(write_failed_ids)
            for task, enrichment in reviewed:
                if task["id"] in failed_set:
                    failed.append((task, "write failed"))
            reviewed = [(t, e) for t, e in reviewed if t["id"] not in failed_set]

    print(format_report(reviewed, failed, unresolved, applied_names, dry_run=not apply))
    return 1 if (failed or unresolved) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
