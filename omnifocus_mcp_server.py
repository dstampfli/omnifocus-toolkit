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
import omnifocus_task_reviewer as reviewer  # noqa: E402

mcp = FastMCP("OmniFocus Toolkit")

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
                 max_tasks: int = DEFAULT_MAX_TASKS) -> dict:
    """Review not-yet-reviewed tasks in the named OmniFocus project(s),
    enriching each task's title and note.

    With apply=True, write the changes and tag each task reviewed. The default
    apply=False previews the proposed enrichments and changes nothing.

    Each task review is a slow API call, so this reviews at most `max_tasks`
    tasks per call and returns `remaining` = how many unreviewed tasks are left.
    When `remaining` > 0, call this tool again with the same arguments to
    process the next batch; repeat until `remaining` is 0. This keeps each call
    short enough to finish within the scheduled task's tool timeout.
    """
    try:
        return reviewer.run_review(projects, apply=apply, max_tasks=max_tasks)
    except Exception as e:
        return {"error": f"review_tasks failed: {e}"}


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
