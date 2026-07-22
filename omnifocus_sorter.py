#!/usr/bin/env python3
"""OmniFocus project sorter: reorder the tasks inside named project(s).

Sorts a project's top-level tasks by any of the eight keys OmniFocus offers
natively in its Organize > Sort menu. Dry-run by default; --apply writes.

Unlike the triage and reviewer tools this makes no Claude API calls — it is two
osascript round-trips regardless of project size.
"""

import json
import subprocess
import sys
from typing import List, Optional, Tuple

from omnifocus_common import run_jxa

# ------------------------------- sort keys --------------------------------

# CLI/MCP key -> the read-stage field it orders by. These are the eight keys in
# OmniFocus's own Organize > Sort menu (By Title, By Status, By Added Date, ...).
SORT_KEYS = {
    "title": "name",
    "status": "status",
    "added": "added",
    "completed": "completionDate",
    "due": "dueDate",
    "planned": "plannedDate",
    "defer": "deferDate",
    "dropped": "dropDate",
}

# Urgency-first, NOT the OmniJS Task.Status.all declaration order (which is
# Blocked, Available, Next, Completed, DueSoon, Overdue, Dropped — meaningless as
# a sort). Urgent, actionable work rises; finished work sinks.
STATUS_RANK = {
    "Overdue": 0,
    "DueSoon": 1,
    "Next": 2,
    "Available": 3,
    "Blocked": 4,
    "Completed": 5,
    "Dropped": 6,
}
# An unrecognized status (a future OmniFocus release) sorts after all known ones
# rather than raising.
_UNKNOWN_STATUS_RANK = len(STATUS_RANK)


def _validate_sort(key, tag_order=None):
    if key == "tag":
        if not tag_order:
            raise SystemExit(
                'sort key "tag" requires a non-empty tag_order '
                "(a priority-ordered list of tag names)")
        return
    if key not in SORT_KEYS:
        raise SystemExit(
            f"unknown sort key {key!r}; valid keys: "
            f"{', '.join(sorted(SORT_KEYS))}, tag")


def sort_value(task, key, tag_index=None):
    """The comparable value for `task` under `key`, or None when unset.

    None means 'no value' and always sorts last (see sort_tasks). For key
    'tag', `tag_index` maps casefolded tag names to their tag_order position;
    the value is the minimum position among the task's tags, or None if none
    of the task's tags appear in tag_order."""
    if key == "tag":
        index = tag_index or {}
        positions = [index[n.casefold()] for n in (task.get("tags") or [])
                     if n.casefold() in index]
        return min(positions) if positions else None
    if key == "title":
        return (task.get("name") or "").casefold()
    if key == "status":
        return STATUS_RANK.get(task.get("status"), _UNKNOWN_STATUS_RANK)
    return task.get(SORT_KEYS[key])


def sort_tasks(tasks, key, descending=False, tag_order=None):
    """Return `tasks` in sorted order. Never mutates the input.

    Tasks with no value for `key` sort last in BOTH directions — flipping them
    to the top on a descending sort is never what the user wants. The sort is
    stable, so ties keep their input order and re-sorting is idempotent. For
    key 'tag', `tag_order` is the priority-ordered list of tag names."""
    _validate_sort(key, tag_order)
    tag_index = None
    if key == "tag":
        tag_index = {name.casefold(): i for i, name in enumerate(tag_order)}
    valued = [t for t in tasks if sort_value(t, key, tag_index) is not None]
    unvalued = [t for t in tasks if sort_value(t, key, tag_index) is None]
    ordered = sorted(valued, key=lambda t: sort_value(t, key, tag_index),
                     reverse=descending)
    return ordered + unvalued


# ------------------------------- read stage -------------------------------

def parse_read_result(stdout: str) -> Tuple[list, list]:
    payload = json.loads(stdout)
    return payload["projects"], payload["missing"]


# Reads each named project's DIRECT children (project.children, not
# flattenedTasks — action-group subtasks keep their own order) with every field
# the eight sort keys need. Dates are emitted as epoch milliseconds or null:
# numeric comparison sidesteps timezone and format ambiguity entirely.
# Project names that don't resolve are collected into `missing` rather than
# aborting the run. argv[0] = JSON {projectNames: [...]}.
READ_SORT_TASKS_JXA = r"""
function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const of = Application('OmniFocus');
    const namesJson = JSON.stringify(cfg.projectNames);
    const omni =
        "(() => {" +
        "  const wanted = " + namesJson + ";" +
        "  const ms = d => d ? d.getTime() : null;" +
        "  const statusName = t => {" +
        "    const s = String(t.taskStatus);" +
        "    const i = s.indexOf(': ');" +
        "    return (i === -1) ? s : s.slice(i + 2, -1);" +
        "  };" +
        "  const missing = [];" +
        "  const projectsOut = [];" +
        "  wanted.forEach(nm => {" +
        "    const proj = flattenedProjects.find(p => p && p.name === nm);" +
        "    if (!proj) { missing.push(nm); return; }" +
        "    const tasksOut = (proj.children || []).map(t => ({" +
        "      id: t.id.primaryKey," +
        "      name: t.name," +
        "      status: statusName(t)," +
        "      added: ms(t.added)," +
        "      completionDate: ms(t.completionDate)," +
        "      dueDate: ms(t.dueDate)," +
        "      plannedDate: ms(t.plannedDate)," +
        "      deferDate: ms(t.deferDate)," +
        "      dropDate: ms(t.dropDate)," +
        "      tags: (t.tags || []).map(tg => tg.name)" +
        "    }));" +
        "    projectsOut.push({ id: proj.id.primaryKey, name: proj.name, tasks: tasksOut });" +
        "  });" +
        "  return JSON.stringify({ projects: projectsOut, missing: missing });" +
        "})()";
    return of.evaluateJavascript(omni);
}
"""


def read_project_tasks(project_names):
    cfg = json.dumps({"projectNames": project_names})
    payload = run_jxa(READ_SORT_TASKS_JXA, cfg)
    return payload["projects"], payload["missing"]


# ------------------------------- apply stage -------------------------------

def build_write_config(projects, valid_ids):
    """Build the write payload from already-sorted projects.

    Only task ids present in `valid_ids` (the id set the read stage returned)
    survive — the same whitelisting rule the triage and reviewer write paths
    use, so nothing that did not come from OmniFocus reaches the OmniJS source.
    """
    return {
        "projects": [
            {"id": p["id"],
             "taskIds": [t["id"] for t in p["tasks"] if t["id"] in valid_ids]}
            for p in projects
        ]
    }


# Moving each task to project.ending in sorted order leaves the project in
# exactly that order. Only project and task IDENTIFIERS reach the OmniJS source
# — never names, notes, or any other free text — so there is no untrusted string
# to escape here (unlike the reviewer, which must percent-encode model output).
WRITE_SORT_JXA = r"""
function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const of = Application('OmniFocus');
    const projectsJson = JSON.stringify(cfg.projects);
    const omni =
        "(() => {" +
        "  const wanted = " + projectsJson + ";" +
        "  const sorted = []; const failed = [];" +
        "  wanted.forEach(p => {" +
        "    const proj = Project.byIdentifier(p.id);" +
        "    if (!proj) { failed.push(p.id); return; }" +
        "    try {" +
        "      p.taskIds.forEach(tid => {" +
        "        const t = Task.byIdentifier(tid);" +
        "        if (t) moveTasks([t], proj.ending);" +
        "      });" +
        "      sorted.push(proj.name);" +
        "    } catch (e) { failed.push(p.id); }" +
        "  });" +
        "  return JSON.stringify({ sorted: sorted, failed: failed });" +
        "})()";
    return of.evaluateJavascript(omni);
}
"""


def apply_order(cfg):
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", WRITE_SORT_JXA, json.dumps(cfg)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("osascript (sort apply) failed:", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(1)
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        print("osascript (sort apply) returned unexpected output:", file=sys.stderr)
        print(result.stdout.strip(), file=sys.stderr)
        raise SystemExit(1)
    return payload.get("sorted", []), payload.get("failed", [])


# --------------------------- pipeline & reporting ---------------------------

def _sort_pipeline(projects, by, descending, apply, tag_order, read, apply_fn):
    """Shared read -> sort -> (optional) apply.

    Returns (sorted_projects, applied, write_failed, missing), where
    sorted_projects carries each project's tasks in their new order."""
    _validate_sort(by, tag_order)  # fail before touching OmniFocus
    read_projects, missing = read(projects)
    valid_ids = {t["id"] for p in read_projects for t in p["tasks"]}

    sorted_projects = []
    for p in read_projects:
        ordered = sort_tasks(p["tasks"], by, descending, tag_order)
        sorted_projects.append({
            "id": p["id"],
            "name": p["name"],
            "tasks": ordered,
            # An already-sorted project needs no moves at all.
            "changed": [t["id"] for t in ordered] != [t["id"] for t in p["tasks"]],
        })

    applied, write_failed = [], []
    changed = [p for p in sorted_projects if p["changed"]]
    if apply and changed:
        applied, write_failed = apply_fn(build_write_config(changed, valid_ids))
    return sorted_projects, applied, write_failed, missing


def run_sort(projects, by, *, descending=False, apply=False, tag_order=None,
             read=read_project_tasks, apply_fn=apply_order):
    """Sort the tasks in the named project(s) and return a structured,
    JSON-serializable result. Dry-run by default. For by='tag', tag_order is
    the priority-ordered list of tag names."""
    sorted_projects, applied, write_failed, missing = _sort_pipeline(
        projects, by, descending, apply, tag_order, read, apply_fn)
    return {
        "dry_run": not apply,
        "by": by,
        "descending": descending,
        "projects": [
            {"id": p["id"], "name": p["name"], "count": len(p["tasks"]),
             "changed": p["changed"], "order": [t["name"] for t in p["tasks"]]}
            for p in sorted_projects
        ],
        "applied": applied,
        "failed": write_failed,
        "missing": missing,
        "counts": {"projects": len(sorted_projects),
                   "changed": sum(1 for p in sorted_projects if p["changed"]),
                   "applied": len(applied), "failed": len(write_failed),
                   "missing": len(missing)},
    }


def format_report(result):
    lines = []
    direction = "descending" if result["descending"] else "ascending"
    verb = "Would sort" if result["dry_run"] else "Sorted"
    for p in result["projects"]:
        if not p["changed"]:
            lines.append(f"{p['name']}: already sorted by {result['by']} "
                         f"({direction}), {p['count']} task(s).")
            continue
        lines.append(f"{verb} {p['name']} by {result['by']} ({direction}), "
                     f"{p['count']} task(s):")
        for i, name in enumerate(p["order"], 1):
            lines.append(f"  {i}. {name}")
    failed = result.get("failed") or []
    missing = result.get("missing") or []
    if failed:
        lines.append("")
        lines.append(f"Write failed ({len(failed)}): {', '.join(failed)}")
    if missing:
        lines.append("")
        lines.append(f"Projects not found ({len(missing)}): {', '.join(missing)}")
    if not result["projects"] and not missing:
        lines.append("Nothing to sort.")
    return "\n".join(lines)


# ----------------------------------- CLI -----------------------------------

def parse_args(argv):
    apply = "--apply" in argv
    descending = "--desc" in argv
    by = None
    tag_order = []
    rest = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--by":
            i += 1
            if i < len(argv):
                by = argv[i]
        elif arg == "--tag":
            i += 1
            if i < len(argv):
                tag_order.append(argv[i])
        elif arg not in ("--apply", "--desc"):
            rest.append(arg)
        i += 1
    return rest, by, descending, apply, tag_order


USAGE = ("usage: omnifocus_sorter.py PROJECT [PROJECT ...] --by KEY "
         "[--desc] [--apply]\n"
         "       KEY: " + ", ".join(sorted(SORT_KEYS)) + ", tag\n"
         "       for --by tag, pass the priority order with repeated --tag:\n"
         "         --by tag --tag Next --tag Waiting --tag Someday")


def main(argv):
    projects, by, descending, apply, tag_order = parse_args(argv)
    if not projects or not by:
        print(USAGE, file=sys.stderr)
        return 2

    result = run_sort(projects, by, descending=descending, apply=apply,
                      tag_order=tag_order)
    print(format_report(result))
    return 1 if (result["missing"] or result["failed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
