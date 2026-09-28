#!/usr/bin/env python3
"""Local stdio MCP server exposing the OmniFocus toolkit's triage, reviewer,
sorter and folder-tagger capabilities as tools, for a scheduled Claude Cowork task in Claude Desktop.

Launched by Claude Desktop via:
  uv run --with mcp[cli] --with-editable <repo> mcp run <this file>
"""

import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env sitting next to this module BEFORE importing the toolkit modules, so
# ANTHROPIC_API_KEY and the config knobs resolve regardless of the working
# directory Claude Desktop launches the subprocess with. The toolkit modules
# read their config at import time, so this must run first.
load_dotenv(Path(__file__).resolve().parent / ".env")

from mcp.server.fastmcp import FastMCP  # noqa: E402

import omnifocus_inbox_triage as triage  # noqa: E402
import omnifocus_sorter as sorter  # noqa: E402
import omnifocus_tagger as tagger  # noqa: E402
import omnifocus_task_reviewer as reviewer  # noqa: E402

mcp = FastMCP("OmniFocus Toolkit")

# Per-project log lines go to stderr (Claude Desktop keeps each MCP server's
# stderr in ~/Library/Logs/Claude/mcp-server-<name>.log). Never print to stdout:
# it carries the stdio MCP protocol.
logger = logging.getLogger("omnifocus_toolkit")

# Each task review is a blocking Claude API call of tens of seconds, so a single
# review_tasks call over a large project can outlast the client's tool timeout.
# Bounding each call to this many tasks (and reporting `remaining`) keeps every
# call short; the agent loops until remaining is 0.
DEFAULT_MAX_TASKS = 5


@mcp.tool()
def triage_inbox(apply: bool = False) -> dict:
    """Classify open OmniFocus Inbox tasks against active projects.

    With apply=True, move high-confidence matches into their project. The
    default apply=False previews the decisions and changes nothing.
    """
    try:
        return triage.run_triage(apply=apply)
    except Exception as e:  # return a clean message instead of crashing the tool
        return {"error": f"triage_inbox failed: {e}"}


@mcp.tool()
def review_tasks(projects: list[str], apply: bool = False,
                 max_tasks: int = DEFAULT_MAX_TASKS, force: bool = False) -> dict:
    """Review not-yet-reviewed tasks in the named OmniFocus project(s),
    enriching each task's title and note.

    Each task becomes "Read: / Watch: / Do: <title>" and its note gets a
    summary block with author/creator, link, and synopsis.

    With apply=True, write the changes and tag each task reviewed. The default
    apply=False previews the proposed enrichments and changes nothing.

    force=True also re-reviews tasks that are already tagged Reviewed or sit on
    the Kanban board, replacing their earlier summary block (a task already in
    a lane keeps that lane). Use it after a summary-format change; leave it off
    for routine runs.

    Each task review is a slow API call, so this reviews at most `max_tasks`
    tasks per call and returns `remaining` = how many unreviewed tasks are left.
    When `remaining` > 0, call this tool again with the same arguments to
    process the next batch; repeat until `remaining` is 0. This keeps each call
    short enough to finish within the scheduled task's tool timeout.
    """
    try:
        return reviewer.run_review(projects, apply=apply, max_tasks=max_tasks,
                                   force=force)
    except Exception as e:
        return {"error": f"review_tasks failed: {e}"}


@mcp.tool()
def sort_project(projects: list[str], by: str, descending: bool = False,
                 apply: bool = False,
                 tag_order: list[str] | None = None) -> dict:
    """Reorder the tasks inside the named OmniFocus project(s).

    `by` is one of the eight keys OmniFocus sorts by natively, or "tag":
      title     - task name, case-insensitive
      status    - urgency first: Overdue, DueSoon, Next, Available, Blocked,
                  Completed, Dropped
      added     - date the task was added
      completed - completion date
      due       - due date
      planned   - planned date
      defer     - defer date
      dropped   - date the task was dropped
      tag       - by tag priority; requires tag_order (see below)

    For by="tag", pass tag_order: a priority-ordered list of tag names. Each
    task sorts by the position of its highest-priority (earliest-listed) tag;
    matching is case-insensitive and by leaf tag name (so "Reviewed" matches a
    nested "Kanban : Reviewed" tag). tag_order is ignored for the other keys.

    Tasks with no value for the chosen key (e.g. no due date, or no listed tag)
    always sort last, in both directions. Set descending=True to reverse. Only
    the project's top-level tasks move; subtasks inside action groups keep their
    order.

    With apply=True, write the new order. The default apply=False previews it
    and changes nothing. Unlike review_tasks this makes no Claude API calls, so
    it is fast regardless of project size and needs no batching loop.
    """
    try:
        return sorter.run_sort(projects, by, descending=descending,
                               apply=apply, tag_order=tag_order)
    except (Exception, SystemExit) as e:
        # run_sort's _validate_sort raises SystemExit (BaseException, not
        # Exception) on an invalid `by`/missing tag_order; catch it too so a
        # scheduled agent gets a clean {"error": ...} instead of a crash.
        return {"error": f"sort_project failed: {e}"}


@mcp.tool()
def tag_tasks(projects: list[str], apply: bool = False,
              include_completed: bool = False,
              create_missing_tags: bool = True) -> dict:
    """Add each project's top-level folder name (e.g. Home, Work) as a tag to
    every task in the named OmniFocus project(s). Additive only: existing tags,
    including the Kanban lanes and Reviewed, are never removed, renamed or
    reordered.

    The folder tag is the top-level segment of the project's folder path (a
    project in "Work : Customers" gets "Work"), matched to an existing tag by
    leaf name, case-insensitive. A project outside any folder is skipped
    (skipped_reason "no_folder"); two tags sharing the name put the project in
    `failed` ("ambiguous_tag"). A tag that does not exist yet is created at the
    top level of the tag tree, or reported as failed ("missing_tag") with
    create_missing_tags=False. Action groups and all their subtasks are tagged;
    completed and dropped tasks only with include_completed=True.

    With apply=True, write the tags. The default apply=False previews exactly
    what apply=True would write and changes nothing. Pass project names as
    returned by list_projects; unknown names are reported in `missing` and the
    other projects still run. Makes no Claude API calls, so every project fits
    in one call and it needs no batching loop; a re-run with nothing new
    reports tagged 0 and writes nothing.
    """
    try:
        result = tagger.run_tag(projects, apply=apply,
                                include_completed=include_completed,
                                create_missing_tags=create_missing_tags)
    except (Exception, SystemExit) as e:
        # run_jxa raises SystemExit on an osascript failure; return a clean
        # error instead of crashing the tool.
        return {"error": f"tag_tasks failed: {e}"}
    _log_tagging(result)
    return result


def _log_tagging(result):
    mode = "preview" if result.get("dry_run") else "apply"
    reasons = {f["project"]: f["reason"] for f in result.get("failed", [])}
    for p in result.get("projects", []):
        status = p["skipped_reason"] or reasons.get(p["name"]) or "ok"
        logger.info("tag_tasks %s %s: folder=%s tagged=%d already=%d %s", mode,
                    p["name"], p["folder_tag"], p["tagged"], p["already_tagged"],
                    status)


@mcp.tool()
def list_projects() -> dict:
    """Read-only. List the user's active OmniFocus projects — id, name, folder
    path, and the project's note (its triage description) — so an agent can
    discover project names dynamically (e.g. to pass to review_tasks)."""
    try:
        _, projects = triage.read_omnifocus()
        return {
            "projects": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "folderPath": p.get("folderPath", ""),
                    "description": p.get("description", ""),
                }
                for p in projects
            ],
            "count": len(projects),
        }
    except Exception as e:
        return {"error": f"list_projects failed: {e}"}


@mcp.tool()
def omnifocus_status() -> dict:
    """Read-only. Report the number of open Inbox tasks and active projects, so
    a scheduled agent can cheaply decide whether to act before triaging."""
    try:
        items, projects = triage.read_omnifocus()
        return {"inbox_open_count": len(items),
                "active_project_count": len(projects)}
    except Exception as e:
        return {"error": f"omnifocus_status failed: {e}"}


app = mcp  # entry point for `mcp run`


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
