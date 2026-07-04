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

# Load a local .env (if present) so ANTHROPIC_API_KEY and the settings below can
# live there instead of the shell/profile. Absent .env is a harmless no-op.
load_dotenv()

# ----------------------------- configuration -----------------------------
# Each setting falls back to the default when not set in .env / the environment.
# ANTHROPIC_API_KEY is read from the environment by the anthropic SDK directly.
MODEL = os.environ.get("MODEL", "claude-haiku-4-5")          # classification model id
MOVE_MIN_CONFIDENCE = os.environ.get("MOVE_MIN_CONFIDENCE", "high")  # min confidence to move
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "25"))         # inbox items per API call
# --------------------------------------------------------------------------

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


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


def chunk_items(items, size):
    """Yield successive `size`-length slices of `items`."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


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
        items.push({ id: t.id(), name: t.name(), note: note });
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

        projects.push({
            id: p.id(),
            name: p.name(),
            folderPath: path.join(' ▸ '),
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
        "OmniFocus projects (each with an id, name, and folder path) and a list "
        "of inbox items (each with an id, name, and note).\n\n"
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


def build_user_content(items, projects):
    return json.dumps({"projects": projects, "inbox_items": items}, ensure_ascii=False)


def classify(items, projects):
    client = anthropic.Anthropic()
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=8192,
            system=build_system_prompt(),
            messages=[{"role": "user", "content": build_user_content(items, projects)}],
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
    for batch in chunk_items(items, chunk_size):
        result = classify(batch, projects)
        decisions.extend(result.decisions)
    return Classification(decisions=decisions)


# ------------------------------- apply stage -------------------------------

def build_apply_config(to_move: List[Decision]) -> dict:
    return {"moves": [{"taskId": d.item_id, "projectId": d.project_id} for d in to_move]}


WRITE_JXA = r"""
function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const of = Application('OmniFocus');
    of.includeStandardAdditions = true;
    const ofDoc = of.defaultDocument;

    const projById = {};
    const projs = ofDoc.flattenedProjects();
    for (let i = 0; i < projs.length; i++) { projById[projs[i].id()] = projs[i]; }

    const taskById = {};
    const inbox = ofDoc.inboxTasks();
    for (let i = 0; i < inbox.length; i++) { taskById[inbox[i].id()] = inbox[i]; }

    const moved = [];
    const failed = [];
    for (let i = 0; i < cfg.moves.length; i++) {
        const m = cfg.moves[i];
        const t = taskById[m.taskId];
        const p = projById[m.projectId];
        if (!t || !p) { failed.push(m.taskId); continue; }
        try {
            t.assignedContainer = p;
            moved.push(t.name());
        } catch (e) { failed.push(m.taskId); }
    }

    return JSON.stringify({ moved: moved, failed: failed });
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
    payload = json.loads(result.stdout.strip())
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
