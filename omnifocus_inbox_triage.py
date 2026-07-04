#!/usr/bin/env python3
"""OmniFocus Inbox triage: categorize Inbox tasks into existing projects via Claude."""

import json
import subprocess
import sys
from typing import List, Literal, Optional, Tuple

import anthropic
from pydantic import BaseModel

# ----------------------------- configuration -----------------------------
MODEL = "claude-opus-4-8"          # Anthropic model id used for classification
MOVE_MIN_CONFIDENCE = "high"       # minimum confidence required to move a task
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
    response = client.messages.parse(
        model=MODEL,
        max_tokens=8192,
        system=build_system_prompt(),
        messages=[{"role": "user", "content": build_user_content(items, projects)}],
        output_format=Classification,
    )
    return response.parsed_output
