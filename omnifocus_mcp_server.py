#!/usr/bin/env python3
"""Local stdio MCP server exposing the OmniFocus toolkit's triage and reviewer
capabilities as tools, for a scheduled Claude Cowork task in Claude Desktop.

Launched by Claude Desktop via:
  uv run --with mcp[cli] --with-editable <repo> mcp run <this file>
"""

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
import omnifocus_task_reviewer as reviewer  # noqa: E402

mcp = FastMCP("OmniFocus Toolkit")


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
def review_tasks(projects: list[str], apply: bool = False) -> dict:
    """Review not-yet-reviewed tasks in the named OmniFocus project(s),
    enriching each task's title and note.

    With apply=True, write the changes and tag each task reviewed. The default
    apply=False previews the proposed enrichments and changes nothing.
    """
    try:
        return reviewer.run_review(projects, apply=apply)
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
    except Exception as e:
        return {"error": f"sort_project failed: {e}"}


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
