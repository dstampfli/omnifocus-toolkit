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

from omnifocus_common import resolve_folder_tag

load_dotenv()


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
