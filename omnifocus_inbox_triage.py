#!/usr/bin/env python3
"""OmniFocus Inbox triage: categorize Inbox tasks into existing projects via Claude."""

import json
import os
import subprocess
import sys
from typing import List, Literal, Optional, Tuple

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

from omnifocus_common import build_task_content, fetch_attachment_b64, media_type_for, _positive_int_env

# Load a local .env (if present) so ANTHROPIC_API_KEY and the settings below can
# live there instead of the shell/profile. Absent .env is a harmless no-op.
load_dotenv()

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


# ----------------------------- configuration -----------------------------
# Each setting falls back to the default when not set in .env / the environment.
# ANTHROPIC_API_KEY is read from the environment by the anthropic SDK directly.
# Values are validated here so a fat-fingered .env fails with a clear message
# instead of a raw traceback deep inside the run (or at test collection).

def _load_config():
    model = os.environ.get("MODEL", "claude-sonnet-5")  # vision-capable model id

    min_conf = os.environ.get("MOVE_MIN_CONFIDENCE", "high").strip().lower()
    if min_conf not in CONFIDENCE_RANK:
        print(
            f"Invalid MOVE_MIN_CONFIDENCE={os.environ.get('MOVE_MIN_CONFIDENCE')!r}; "
            f"expected one of: {', '.join(CONFIDENCE_RANK)}.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    chunk = _positive_int_env("CHUNK_SIZE", "25")               # items per API call
    max_att = _positive_int_env("MAX_ATTACHMENT_BYTES", "10485760")     # 10 MiB per attachment
    max_batch = _positive_int_env("MAX_BATCH_ATTACHMENT_BYTES", "20971520")  # 20 MiB per call
    max_note = _positive_int_env("MAX_NOTE_CHARS", "4000")      # cleaned-note truncation

    return model, min_conf, chunk, max_att, max_batch, max_note


(
    MODEL,
    MOVE_MIN_CONFIDENCE,
    CHUNK_SIZE,
    MAX_ATTACHMENT_BYTES,
    MAX_BATCH_ATTACHMENT_BYTES,
    MAX_NOTE_CHARS,
) = _load_config()
# --------------------------------------------------------------------------


class Decision(BaseModel):
    item_id: str
    project_id: Optional[str] = None
    project_name: str = ""
    confidence: Literal["high", "medium", "low"]
    reason: str = ""


class Classification(BaseModel):
    decisions: List[Decision]


def should_move(decision, valid_item_ids, valid_project_ids,
                min_confidence=MOVE_MIN_CONFIDENCE):
    if decision.project_id is None:
        return False
    if decision.item_id not in valid_item_ids:
        return False
    if decision.project_id not in valid_project_ids:
        return False
    return CONFIDENCE_RANK[decision.confidence] >= CONFIDENCE_RANK[min_confidence]


def partition_decisions(decisions, item_ids, project_ids,
                        min_confidence=MOVE_MIN_CONFIDENCE):
    valid_items = set(item_ids)
    valid_projects = set(project_ids)
    to_move: List[Decision] = []
    to_leave: List[Decision] = []
    seen = set()
    for d in decisions:
        if d.item_id in seen:
            continue
        seen.add(d.item_id)
        if should_move(d, valid_items, valid_projects, min_confidence):
            to_move.append(d)
        else:
            to_leave.append(d)
    return to_move, to_leave


# ------------------------------- read stage -------------------------------

def parse_read_result(stdout: str) -> Tuple[list, list]:
    payload = json.loads(stdout)
    return payload["items"], payload["projects"]


def active_projects(projects: list) -> list:
    return [
        p for p in projects
        if str(p.get("status", "")).lower().startswith("active")
    ]


READ_JXA = r"""
function run() {
    const of = Application('OmniFocus');
    of.includeStandardAdditions = true;
    const ofDoc = of.defaultDocument;

    // Attachments are only reachable via OmniJS; fetch metadata (no bytes) for
    // all inbox items keyed by task id, then merge onto each item below.
    let attMap = {};
    try {
        const metaScript =
            "(() => {" +
            "  const map = {};" +
            "  inbox.forEach(t => {" +
            "    if (!t) return;" +
            "    let atts = [];" +
            "    try { atts = t.attachments || []; } catch (e) { atts = []; }" +
            "    if (!atts.length) return;" +
            "    map[t.id.primaryKey] = atts.map((a, idx) => {" +
            "      let fn = '', len = -1;" +
            "      try { fn = a.filename || a.preferredFilename || ''; } catch (e) {}" +
            "      try { len = a.contents ? a.contents.length : -1; } catch (e) {}" +
            "      return { filename: fn, byteLength: len, index: idx };" +
            "    });" +
            "  });" +
            "  return JSON.stringify(map);" +
            "})()";
        attMap = JSON.parse(of.evaluateJavascript(metaScript));
    } catch (e) { attMap = {}; }

    const items = [];
    const inbox = ofDoc.inboxTasks();
    for (let i = 0; i < inbox.length; i++) {
        const t = inbox[i];
        // Skip completed tasks: inboxTasks() includes finished items that no
        // longer appear in the Inbox perspective; only triage open ones.
        let done = false;
        try { done = t.completed(); } catch (e) {}
        if (done) continue;
        let note = '';
        try { note = t.note() || ''; } catch (e) {}
        const tid = t.id();
        items.push({ id: tid, name: t.name(), note: note, attachments: attMap[tid] || [] });
    }

    const projects = [];
    const projs = ofDoc.flattenedProjects();
    for (let i = 0; i < projs.length; i++) {
        const p = projs[i];
        let status = '';
        try { status = String(p.status()); } catch (e) {}

        let path = [];
        try {
            let f = p.container();
            while (f && f.class && f.class() === 'folder') {
                path.unshift(f.name());
                f = f.container();
            }
        } catch (e) {}

        // The project's OmniFocus note doubles as its triage description:
        // one line telling the classifier what belongs in this project.
        let note = '';
        try { note = p.note() || ''; } catch (e) {}

        projects.push({
            id: p.id(),
            name: p.name(),
            folderPath: path.join(' ▸ '),
            description: note,
            status: status,
        });
    }

    return JSON.stringify({ items: items, projects: projects });
}
"""


def read_omnifocus() -> Tuple[list, list]:
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", READ_JXA],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("osascript (read) failed:", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(1)
    items, projects = parse_read_result(result.stdout.strip())
    return items, active_projects(projects)


# ----------------------------- classify stage -----------------------------

def build_system_prompt():
    return (
        "You triage a GTD inbox. You are given a list of the user's existing "
        "OmniFocus projects (each with an id, name, folder path, and a "
        "description of what belongs in it) "
        "and a list of inbox items. Each inbox item is presented as a text "
        "header (its id, name, cleaned note, and a list of any attachment "
        "filenames) optionally followed by the attachment images or PDF "
        "documents themselves — read those attachments as part of judging the "
        "item.\n\n"
        "Rely on each project's description to decide what belongs there; it is "
        "the user's own statement of the project's scope and takes precedence "
        "over the project name. When a project's description is empty, fall back "
        "to its name and folder path.\n\n"
        "For EACH inbox item, choose the single best-matching project, or decline "
        "if none is a good home. Return one decision per inbox item.\n\n"
        "For each decision provide:\n"
        "- item_id: the inbox item's id, copied exactly.\n"
        "- project_id: the matching project's id, or null if no project fits well.\n"
        "- project_name: the matching project's name (empty string if project_id is null).\n"
        "- confidence: 'high', 'medium', or 'low'. Use 'high' only when the item "
        "clearly belongs to that project.\n"
        "- reason: one short sentence justifying the choice.\n\n"
        "Only use project ids from the provided list. Do not invent projects."
    )


def build_user_message(items, projects, fetch_bytes, max_bytes, max_note_chars):
    # The model message is an ordered list of content blocks: a leading text
    # block with the project taxonomy (internal `status` filtered out), then each
    # item's blocks (text header + any attachment vision blocks).
    slim_projects = [
        {
            "id": p["id"],
            "name": p["name"],
            "folderPath": p.get("folderPath", ""),
            "description": p.get("description", ""),
        }
        for p in projects
    ]
    content = [{
        "type": "text",
        "text": "PROJECTS:\n" + json.dumps({"projects": slim_projects}, ensure_ascii=False),
    }]
    for item in items:
        content.extend(build_task_content(item, fetch_bytes, max_bytes, max_note_chars))
    return content


def batch_items_by_size(items, chunk_size, max_bytes, max_batch_bytes):
    """Yield lists of items, flushing when a batch reaches chunk_size items or
    when adding an item's in-scope attachment bytes (supported type and within
    max_bytes) would exceed max_batch_bytes. Item order is preserved."""
    batch = []
    batch_bytes = 0
    for item in items:
        item_bytes = sum(
            att.get("byteLength", 0)
            for att in item.get("attachments", [])
            if media_type_for(att.get("filename", ""))
            and 0 <= att.get("byteLength", -1) <= max_bytes
        )
        if batch and (len(batch) >= chunk_size or batch_bytes + item_bytes > max_batch_bytes):
            yield batch
            batch, batch_bytes = [], 0
        batch.append(item)
        batch_bytes += item_bytes
    if batch:
        yield batch


def classify(items, projects):
    client = anthropic.Anthropic()
    content = build_user_message(
        items, projects, fetch_attachment_b64, MAX_ATTACHMENT_BYTES, MAX_NOTE_CHARS
    )
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=8192,
            system=build_system_prompt(),
            messages=[{"role": "user", "content": content}],
            output_format=Classification,
        )
    except anthropic.APIError as e:
        # Covers auth, rate-limit, connection, and other API/status errors
        # (APIConnectionError is a subclass of APIError in this SDK version).
        print(f"Claude API request failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    return response.parsed_output


def classify_in_batches(items, projects, chunk_size=CHUNK_SIZE):
    decisions = []
    for batch in batch_items_by_size(
        items, chunk_size, MAX_ATTACHMENT_BYTES, MAX_BATCH_ATTACHMENT_BYTES
    ):
        result = classify(batch, projects)
        decisions.extend(result.decisions)
    return Classification(decisions=decisions)


# ------------------------------- apply stage -------------------------------

def build_apply_config(to_move: List[Decision]) -> dict:
    return {"moves": [{"taskId": d.item_id, "projectId": d.project_id} for d in to_move]}


WRITE_JXA = r"""
function run(argv) {
    // Move each task into its project via the Omni Automation (OmniJS) bridge.
    // Setting `assignedContainer` from JXA only marks a pending assignment and
    // does NOT relocate the task; OmniJS `moveTasks(tasks, project.ending)`
    // performs a real move. Tasks/projects are matched by identifier, which
    // equals the JXA `.id()` captured during the read.
    // Safety: only the `moves` list — objects of {taskId, projectId} that the
    // Python side has already whitelisted against real OmniFocus ids — is
    // embedded into the OmniJS source. Embed IDS ONLY here; never interpolate
    // task names, notes, or other free text into this program (JSON.stringify
    // does not escape U+2028/U+2029, so free text could break out of the source).
    const of = Application('OmniFocus');
    const movesJson = JSON.stringify(JSON.parse(argv[0]).moves);
    const omni =
        "(() => {" +
        "  const moves = " + movesJson + ";" +
        "  const moved = [];" +
        "  const failed = [];" +
        "  moves.forEach(m => {" +
        "    const task = inbox.find(t => t.id.primaryKey === m.taskId)" +
        "              || flattenedTasks.find(t => t.id.primaryKey === m.taskId);" +
        "    const proj = flattenedProjects.find(p => p.id.primaryKey === m.projectId);" +
        "    if (!task || !proj) { failed.push(m.taskId); return; }" +
        "    try { moveTasks([task], proj.ending); moved.push(task.name); }" +
        "    catch (e) { failed.push(m.taskId); }" +
        "  });" +
        "  return JSON.stringify({ moved: moved, failed: failed });" +
        "})()";
    return of.evaluateJavascript(omni);
}
"""


def apply_moves(to_move: List[Decision]) -> Tuple[list, list]:
    cfg = json.dumps(build_apply_config(to_move))
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
    return payload.get("moved", []), payload.get("failed", [])


# --------------------------- reporting & CLI --------------------------------

def format_report(to_move, to_leave, items, dry_run, failed_ids=None):
    failed_ids = set(failed_ids) if failed_ids else set()
    names = {i["id"]: i["name"] for i in items}
    lines = []

    moved = [d for d in to_move if d.item_id not in failed_ids]
    failed = [d for d in to_move if d.item_id in failed_ids]

    if moved:
        verb = "Will move" if dry_run else "Moved"
        lines.append(f"{verb} {len(moved)} item(s):")
        for d in moved:
            name = names.get(d.item_id, d.item_id)
            lines.append(f"  + {name} -> {d.project_name} ({d.reason})")

    if failed:
        if lines:
            lines.append("")
        lines.append("Failed to move (still in Inbox):")
        for d in failed:
            name = names.get(d.item_id, d.item_id)
            lines.append(f"  ! {name} -> {d.project_name} ({d.reason})")

    if to_leave:
        lines.append("")
        lines.append(f"Left in Inbox ({len(to_leave)}):")
        for d in to_leave:
            name = names.get(d.item_id, d.item_id)
            lines.append(f"  = {name} - {d.reason or 'no confident match'}")

    if not to_move and not to_leave:
        lines.append("Nothing to do.")

    return "\n".join(lines)


def main():
    apply = "--apply" in sys.argv
    dry_run = not apply

    items, projects = read_omnifocus()
    if not items:
        print('No inbox tasks to triage.')
        return 0

    classification = classify_in_batches(items, projects)
    item_ids = [i["id"] for i in items]
    project_ids = [p["id"] for p in projects]
    to_move, to_leave = partition_decisions(
        classification.decisions, item_ids, project_ids
    )

    if apply and to_move:
        _, failed = apply_moves(to_move)
        if failed:
            print(f"Warning: {len(failed)} move(s) failed: {failed}", file=sys.stderr)
    else:
        failed = []

    print(format_report(to_move, to_leave, items, dry_run=dry_run, failed_ids=failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
