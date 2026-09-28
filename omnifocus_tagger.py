#!/usr/bin/env python3
"""OmniFocus folder tagger: tag every task with its project's top-level folder.

Adds the tag named after each named project's top-level folder (Personal, Home,
Work, ...) to every task in the project that lacks it, creating the tag at the
top level of the tag tree if it does not exist yet. Additive only: it never
removes, renames or reorders a tag. Dry-run by default; --apply writes.

Like the sorter this makes no Claude API calls: one osascript read and at most
one osascript write, regardless of how many projects are named.
"""

import json
import sys

from dotenv import load_dotenv

from omnifocus_common import (
    TAG_TREE_JS,
    TOP_FOLDER_JS,
    embed_js,
    kanban_tag_env,
    resolve_folder_tag,
    run_jxa,
)

load_dotenv()

# The Kanban parent tag: its subtree (the board lanes) never counts as a folder
# tag. Same env var as the reviewer and the board.
KANBAN_TAG = kanban_tag_env()


# ------------------------------- read stage -------------------------------

# One OmniJS call: for each named ACTIVE project, its top-level folder and every
# task in flattenedTasks (action groups and all their descendants) with its tag
# ids, plus the whole tag tree. Project names that don't resolve go to
# `missing`. argv[0] = JSON {projectNames: [...], kanbanTag: "..."}.
READ_TAG_TASKS_JXA = embed_js(r"""
function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const of = Application('OmniFocus');
    const omni =
        "(() => {" +
        __TAG_TREE_JS__ + __TOP_FOLDER_JS__ +
        "  const wanted = " + JSON.stringify(cfg.projectNames) + ";" +
        "  const kanbanName = " + JSON.stringify(cfg.kanbanTag) + ";" +
        "  const missing = []; const projectsOut = [];" +
        "  wanted.forEach(nm => {" +
        "    const proj = flattenedProjects.find(p => p && p.name === nm && p.status === Project.Status.Active);" +
        "    if (!proj) { missing.push(nm); return; }" +
        "    const tasksOut = proj.flattenedTasks.filter(t => t).map(t => ({" +
        "      id: t.id.primaryKey, name: t.name, completed: !!t.completed," +
        "      dropped: t.taskStatus === Task.Status.Dropped," +
        "      tag_ids: (t.tags || []).map(x => x.id.primaryKey)" +
        "    }));" +
        "    projectsOut.push({ id: proj.id.primaryKey, name: proj.name, folder: topFolder(proj), tasks: tasksOut });" +
        "  });" +
        "  return JSON.stringify({ projects: projectsOut, missing: missing, tags: tagTree(kanbanName) });" +
        "})()";
    return of.evaluateJavascript(omni);
}
""", TAG_TREE_JS=TAG_TREE_JS, TOP_FOLDER_JS=TOP_FOLDER_JS)


def read_tag_tasks(project_names, kanban_tag=KANBAN_TAG):
    cfg = json.dumps({"projectNames": project_names, "kanbanTag": kanban_tag})
    payload = run_jxa(READ_TAG_TASKS_JXA, cfg)
    return payload["projects"], payload["tags"], payload["missing"]


# ------------------------------- plan stage -------------------------------

def _in_scope(task, include_completed):
    return include_completed or not (task.get("completed") or task.get("dropped"))


def plan_tagging(read_projects, tags, include_completed=False,
                 create_missing_tags=True):
    """Work out, per project, which tasks still need its folder tag. Pure.

    Both the preview and the write are built from this plan, so a dry run
    reports exactly what --apply would write."""
    plans = []
    for p in read_projects:
        res = resolve_folder_tag(p.get("folder", ""), tags)
        scope = [t for t in p["tasks"] if _in_scope(t, include_completed)]
        plan = {"id": p["id"], "name": p["name"],
                "folder_tag": res["folder"] or None, "resolution": res,
                "count": len(scope), "to_tag": [], "already_tagged": 0,
                "skipped_reason": None, "failure": None}
        if res["kind"] == "no_folder":
            plan["skipped_reason"] = "no_folder"
        elif res["kind"] == "ambiguous":
            plan["failure"] = "ambiguous_tag"
        elif res["kind"] == "create" and not create_missing_tags:
            plan["failure"] = "missing_tag"
        else:
            has = {t["id"] for t in scope
                   if res["tag_id"] and res["tag_id"] in (t.get("tag_ids") or [])}
            plan["already_tagged"] = len(has)
            plan["to_tag"] = [t["id"] for t in scope if t["id"] not in has]
        plans.append(plan)
    return plans


def build_write_config(plans, valid_task_ids, valid_tag_ids):
    """The write payload: tags to create and, per project, which tag to add to
    which tasks.

    Only ids the read stage returned survive (the sorter's whitelisting rule).
    A tag to create appears once however many projects need it, and only when
    some project actually has a task to tag with it."""
    creates, create_index, projects = [], {}, []
    for p in plans:
        if p["failure"] or p["skipped_reason"]:
            continue
        task_ids = [i for i in p["to_tag"] if i in valid_task_ids]
        if not task_ids:
            continue
        res = p["resolution"]
        if res["kind"] == "match":
            if res["tag_id"] not in valid_tag_ids:
                continue
            projects.append({"id": p["id"], "tagId": res["tag_id"],
                             "createIndex": -1, "taskIds": task_ids})
        else:  # create
            key = res["folder"].casefold()
            if key not in create_index:
                create_index[key] = len(creates)
                creates.append(res["folder"])
            projects.append({"id": p["id"], "tagId": "",
                             "createIndex": create_index[key], "taskIds": task_ids})
    return {"creates": creates, "projects": projects}


# ------------------------------- apply stage -------------------------------

# One OmniJS call for every project. Tags to create are made first, at the top
# level; then each project's tasks get addTag inside its own try, so one
# failing project never stops the others. Tags are only ever ADDED. Project,
# tag and task identifiers were whitelisted in Python; the only free text, the
# names to create, is percent-encoded here (encodeURIComponent output cannot
# break out of a JS string literal) and decoded inside OmniJS.
WRITE_TAG_JXA = r"""
function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const of = Application('OmniFocus');
    const creates = cfg.creates.map(n => "\"" + encodeURIComponent(n) + "\"").join(",");
    const omni =
        "(() => {" +
        "  const createNames = [" + creates + "].map(decodeURIComponent);" +
        "  const wanted = " + JSON.stringify(cfg.projects) + ";" +
        "  const newTags = []; const created = [];" +
        "  const applied = []; const failed = [];" +
        "  createNames.forEach(nm => {" +
        "    try { const tg = new Tag(nm); newTags.push(tg); created.push(tg.name); }" +
        "    catch (e) { newTags.push(null); }" +
        "  });" +
        "  wanted.forEach(p => {" +
        "    try {" +
        "      const tag = p.createIndex >= 0 ? newTags[p.createIndex] : Tag.byIdentifier(p.tagId);" +
        "      if (!tag) { failed.push(p.id); return; }" +
        "      p.taskIds.forEach(tid => {" +
        "        const t = Task.byIdentifier(tid);" +
        "        if (!t) throw new Error('task not found: ' + tid);" +
        "        t.addTag(tag);" +
        "      });" +
        "      applied.push(p.id);" +
        "    } catch (e) { failed.push(p.id); }" +
        "  });" +
        "  return JSON.stringify({ applied: applied, failed: failed, created: created });" +
        "})()";
    return of.evaluateJavascript(omni);
}
"""


def apply_tagging(cfg):
    payload = run_jxa(WRITE_TAG_JXA, json.dumps(cfg))
    return (payload.get("applied", []), payload.get("failed", []),
            payload.get("created", []))


# --------------------------- pipeline & reporting ---------------------------

def run_tag(projects, *, apply=False, include_completed=False,
            create_missing_tags=True, read=read_tag_tasks, apply_fn=apply_tagging):
    """Tag every task in the named project(s) with its project's top-level
    folder and return a structured, JSON-serializable result. Dry-run by
    default; the preview and the write come from the same plan."""
    names = list(dict.fromkeys(projects))  # de-duplicate, keep order
    if names:
        read_projects, tags, missing = read(names)
    else:
        read_projects, tags, missing = [], [], []
    plans = plan_tagging(read_projects, tags, include_completed, create_missing_tags)
    valid_task_ids = {t["id"] for p in read_projects for t in p["tasks"]}
    cfg = build_write_config(plans, valid_task_ids, {t["id"] for t in tags})
    to_write = {p["id"]: len(p["taskIds"]) for p in cfg["projects"]}

    if not apply:
        done_ids, write_failed, created = set(to_write), [], list(cfg["creates"])
    elif cfg["projects"]:
        applied_ids, write_failed, created = apply_fn(cfg)
        done_ids = set(applied_ids)
    else:
        done_ids, write_failed, created = set(), [], []

    write_failed = set(write_failed)
    failed = [{"project": p["name"], "reason": p["failure"]}
              for p in plans if p["failure"]]
    failed += [{"project": p["name"], "reason": "write_error"}
               for p in plans if p["id"] in write_failed]
    out_projects = [
        {"id": p["id"], "name": p["name"], "folder_tag": p["folder_tag"],
         "count": p["count"],
         "tagged": to_write.get(p["id"], 0) if p["id"] in done_ids else 0,
         "already_tagged": p["already_tagged"],
         "skipped_reason": p["skipped_reason"]}
        for p in plans
    ]
    applied = [p["name"] for p in plans if p["id"] in done_ids] if apply else []
    return {
        "dry_run": not apply,
        "projects": out_projects,
        "tags_created": created,
        "applied": applied,
        "failed": failed,
        "missing": list(missing),
        "counts": {"projects": len(out_projects),
                   "tagged": sum(p["tagged"] for p in out_projects),
                   "already_tagged": sum(p["already_tagged"] for p in out_projects),
                   "failed": len(failed), "missing": len(missing)},
    }


def format_report(result):
    lines = []
    verb = "Would tag" if result["dry_run"] else "Tagged"
    reasons = {f["project"]: f["reason"] for f in result["failed"]}
    for p in result["projects"]:
        if p["skipped_reason"]:
            lines.append(f"{p['name']}: skipped ({p['skipped_reason']}).")
        elif p["name"] in reasons:
            lines.append(f"{p['name']}: failed ({reasons[p['name']]}).")
        else:
            lines.append(f"{verb} {p['tagged']} of {p['count']} task(s) in "
                         f"{p['name']} with {p['folder_tag']} "
                         f"({p['already_tagged']} already tagged).")
    if result["tags_created"]:
        head = "Would create tag(s)" if result["dry_run"] else "Created tag(s)"
        lines.append("")
        lines.append(f"{head}: {', '.join(result['tags_created'])}")
    if result["missing"]:
        lines.append("")
        lines.append(f"Projects not found ({len(result['missing'])}): "
                     f"{', '.join(result['missing'])}")
    if not result["projects"] and not result["missing"]:
        lines.append("Nothing to tag.")
    return "\n".join(lines)


# ----------------------------------- CLI -----------------------------------

_FLAGS = ("--apply", "--include-completed", "--no-create")


def parse_args(argv):
    projects = [a for a in argv if a not in _FLAGS]
    return (projects, "--apply" in argv, "--include-completed" in argv,
            "--no-create" not in argv)


USAGE = ("usage: omnifocus_tagger.py PROJECT [PROJECT ...] [--apply] "
         "[--include-completed] [--no-create]")


def main(argv):
    projects, apply, include_completed, create_missing_tags = parse_args(argv)
    if not projects:
        print(USAGE, file=sys.stderr)
        return 2
    result = run_tag(projects, apply=apply, include_completed=include_completed,
                     create_missing_tags=create_missing_tags)
    print(format_report(result))
    return 1 if (result["missing"] or result["failed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
