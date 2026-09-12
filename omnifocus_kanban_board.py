#!/usr/bin/env python3
"""OmniFocus Kanban board: a local drag-and-drop web UI over the Kanban lanes.

Serves one page at http://127.0.0.1:<port>/ where each column is a child tag of
the `Kanban` parent (Reviewed / To Do / In Progress / Waiting / Done, as created
by the Kanban plug-in's Display Board action). Dropping a card on a column
re-tags the task exactly like the plug-in does: remove every Kanban lane tag,
add the target lane.

Unlike the other tools there is no --apply: each drop is the user's explicit
action and writes immediately. Makes no Claude API calls.
"""

import argparse
import json
import re
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import os  # noqa: E402  (after load_dotenv so .env is present)

from omnifocus_common import (  # noqa: E402
    JxaError,
    _positive_int_env,
    clean_note,
    run_jxa_or_raise,
)

PAGE_PATH = Path(__file__).resolve().parent / "kanban_board" / "index.html"
DEFAULT_PORT = 8765
EXCERPT_CHARS = 200
SORT_KEYS = ("due", "title", "project", "added")

# Statuses never shown on the board (also filtered in OmniJS; this is the
# Python-side guarantee so a raw payload can't reintroduce them).
_HIDDEN_STATUSES = {"Completed", "Dropped"}


# ----------------------------- configuration -----------------------------
def _load_config():
    # Same env vars the reviewer uses: KANBAN_TAG names the parent tag whose
    # children are the lanes; MAX_NOTE_CHARS caps the note read per task.
    kanban = os.environ.get("KANBAN_TAG", "Kanban").strip() or "Kanban"
    max_note = _positive_int_env("MAX_NOTE_CHARS", "4000")
    return kanban, max_note


KANBAN_TAG, MAX_NOTE_CHARS = _load_config()


# ------------------------------ pure helpers ------------------------------

def _project_name(card):
    return ((card.get("project") or {}).get("name")) or ""


def _title(card):
    return (card.get("name") or "").casefold()


def sort_cards(cards, key="due"):
    """Return `cards` in sorted order; never mutates the input.

    Cards with no value for the key sort last (as in omnifocus_sorter), the
    sort is stable, and ties break by project name then title. 'added' is
    newest first; the other keys ascend."""
    if key not in SORT_KEYS:
        raise ValueError(f"unknown sort key {key!r}; valid keys: "
                         f"{', '.join(SORT_KEYS)}")
    if key == "title":
        return sorted(cards, key=lambda c: (_title(c), _project_name(c).casefold()))
    if key == "project":
        def value(c):
            return _project_name(c) or None

        def order(c):
            return (_project_name(c).casefold(), c.get("due") is None,
                    c.get("due") or 0, _title(c))
    elif key == "due":
        def value(c):
            return c.get("due")

        def order(c):
            return (c["due"], _project_name(c).casefold(), _title(c))
    else:  # added
        def value(c):
            return c.get("added")

        def order(c):
            return (-c["added"], _project_name(c).casefold(), _title(c))
    valued = [c for c in cards if value(c) is not None]
    unvalued = [c for c in cards if value(c) is None]
    return sorted(valued, key=order) + unvalued


def finish_card(raw_card, max_note_chars=MAX_NOTE_CHARS,
                excerpt_chars=EXCERPT_CHARS):
    """Turn a raw OmniJS card into the API card: the raw `note` becomes a
    short `note_excerpt` (cleaned with clean_note, then capped)."""
    card = {k: v for k, v in raw_card.items() if k != "note"}
    card.setdefault("project", None)
    card.setdefault("tags", [])
    card.setdefault("flagged", False)
    for field in ("due", "defer", "added"):
        card.setdefault(field, None)
    note = clean_note(raw_card.get("note") or "", max_note_chars)
    if len(note) > excerpt_chars:
        note = note[:excerpt_chars].rstrip() + "…"
    card["note_excerpt"] = note
    return card


def build_board(raw, sort_key="due", max_note_chars=MAX_NOTE_CHARS):
    """Assemble the API board from the read stage's raw payload.

    Lanes keep OmniFocus order. Completed/dropped cards are hidden, a task
    carrying two lane tags is shown in the first lane only (the raw payload is
    emitted in lane order), and cards are sorted by `sort_key`."""
    lanes = [{"id": lane["id"], "name": lane["name"]}
             for lane in raw.get("lanes", [])]
    lane_ids = {lane["id"] for lane in lanes}
    seen = set()
    cards = []
    for raw_card in raw.get("cards", []):
        if raw_card.get("status") in _HIDDEN_STATUSES:
            continue
        if raw_card["id"] in seen or raw_card.get("lane_id") not in lane_ids:
            continue
        seen.add(raw_card["id"])
        cards.append(finish_card(raw_card, max_note_chars))
    return {"lanes": lanes, "cards": sort_cards(cards, sort_key)}


# ------------------------------ move validation ---------------------------

# OmniFocus identifiers are short base64url-ish strings. Anything else never
# reaches the OmniJS source.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MoveError(ValueError):
    """A move request that must be refused before any osascript call."""


def validate_move(body, task_ids, lane_ids, loaded=True):
    """Return (task_id, lane_id) from a /api/move body, or raise MoveError.

    Both ids must be well-formed AND present in the id sets captured by the
    most recent successful board read — the same whitelisting rule the other
    tools' write paths use, so only identifiers OmniFocus itself handed us are
    ever embedded in the write source."""
    if not isinstance(body, dict):
        raise MoveError("request body must be a JSON object")
    found = {}
    for field in ("task_id", "lane_id"):
        value = body.get(field)
        if not isinstance(value, str) or not _ID_RE.match(value):
            raise MoveError(f"{field} must be an OmniFocus identifier")
        found[field] = value
    if not loaded:
        raise MoveError("load the board before moving a task")
    if found["task_id"] not in task_ids:
        raise MoveError("unknown task; refresh the board")
    if found["lane_id"] not in lane_ids:
        raise MoveError("unknown lane; refresh the board")
    return found["task_id"], found["lane_id"]


class BoardState:
    """Id sets from the most recent successful read, plus the lock that
    serializes every osascript call (so two quick drops cannot interleave
    their Apple Events and a read never observes a half-applied move)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.loaded = False
        self.task_ids = set()
        self.lane_ids = set()

    def remember(self, board):
        self.task_ids = {c["id"] for c in board["cards"]}
        self.lane_ids = {lane["id"] for lane in board["lanes"]}
        self.loaded = True


# ------------------------------- JXA programs -----------------------------

# OmniJS snippet shared by both programs: serializes one task as a raw card.
# Expects `laneIds` ({tagId: true} for the Kanban children) and `maxNoteChars`
# to be defined by the enclosing program. Dates are epoch ms or null (numeric
# comparison sidesteps timezone/format ambiguity, as in the sorter); `tags` are
# the task's non-lane leaf tag names; the raw note is capped here and turned
# into an excerpt on the Python side.
_CARD_OMNIJS = (
    "  const ms = d => d ? d.getTime() : null;"
    "  const statusName = t => {"
    "    const s = String(t.taskStatus);"
    "    const i = s.indexOf(': ');"
    "    return (i === -1) ? s : s.slice(i + 2, -1);"
    "  };"
    "  const cardOf = (t, laneId) => {"
    "    const proj = t.containingProject;"
    "    return {"
    "      id: t.id.primaryKey,"
    "      name: t.name,"
    "      lane_id: laneId,"
    "      project: proj ? { id: proj.id.primaryKey, name: proj.name } : null,"
    "      due: ms(t.dueDate),"
    "      defer: ms(t.deferDate),"
    "      added: ms(t.added),"
    "      flagged: !!t.flagged,"
    "      status: statusName(t),"
    "      tags: (t.tags || []).filter(x => !laneIds[x.id.primaryKey]).map(x => x.name),"
    "      note: String(t.note || '').slice(0, maxNoteChars)"
    "    };"
    "  };"
)

# Reads the Kanban parent's children (the lanes, in OmniFocus order) and every
# open task in each lane, entirely in OmniJS (tags need the bridge). A task in
# two lanes is emitted for the first lane only. argv[0] = JSON
# {kanbanTag, maxNoteChars}; only those two config values reach the source.
READ_BOARD_JXA = r"""
function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const of = Application('OmniFocus');
    const omni =
        "(() => {" +
        "  const kanbanName = " + JSON.stringify(cfg.kanbanTag) + ";" +
        "  const maxNoteChars = " + JSON.stringify(cfg.maxNoteChars) + ";" +
        "  const parent = flattenedTags.byName(kanbanName);" +
        "  if (!parent) return JSON.stringify({ error: 'missing_kanban_tag' });" +
        "  const lanes = parent.children || [];" +
        "  const laneIds = {};" +
        "  lanes.forEach(l => { laneIds[l.id.primaryKey] = true; });" +
        __CARD__ +
        "  const seen = {}; const cards = [];" +
        "  lanes.forEach(l => {" +
        "    (l.tasks || []).forEach(t => {" +
        "      if (!t) return;" +
        "      if (t.completed || t.taskStatus === Task.Status.Dropped) return;" +
        "      const id = t.id.primaryKey;" +
        "      if (seen[id]) return;" +
        "      seen[id] = true;" +
        "      cards.push(cardOf(t, l.id.primaryKey));" +
        "    });" +
        "  });" +
        "  return JSON.stringify({" +
        "    lanes: lanes.map(l => ({ id: l.id.primaryKey, name: l.name }))," +
        "    cards: cards" +
        "  });" +
        "})()";
    return of.evaluateJavascript(omni);
}
""".replace("__CARD__", json.dumps(_CARD_OMNIJS))

# Re-tags one task into one lane — the plug-in's exact operation
# (removeTags(all lanes) + addTag(lane)) — and returns the task's fresh card.
# argv[0] = JSON {kanbanTag, maxNoteChars, taskId, laneId}. The ids were
# validated (well-formed + seen in the last read) before this runs; the lane
# is checked again here to be a child of the Kanban parent.
MOVE_TASK_JXA = r"""
function run(argv) {
    const cfg = JSON.parse(argv[0]);
    const of = Application('OmniFocus');
    const omni =
        "(() => {" +
        "  const kanbanName = " + JSON.stringify(cfg.kanbanTag) + ";" +
        "  const maxNoteChars = " + JSON.stringify(cfg.maxNoteChars) + ";" +
        "  const taskId = " + JSON.stringify(cfg.taskId) + ";" +
        "  const laneId = " + JSON.stringify(cfg.laneId) + ";" +
        "  const parent = flattenedTags.byName(kanbanName);" +
        "  if (!parent) return JSON.stringify({ error: 'missing_kanban_tag' });" +
        "  const lanes = parent.children || [];" +
        "  const laneIds = {};" +
        "  lanes.forEach(l => { laneIds[l.id.primaryKey] = true; });" +
        "  const lane = Tag.byIdentifier(laneId);" +
        "  if (!lane || !laneIds[laneId]) return JSON.stringify({ error: 'lane is not a Kanban lane' });" +
        "  const task = Task.byIdentifier(taskId);" +
        "  if (!task) return JSON.stringify({ error: 'task no longer exists' });" +
        __CARD__ +
        "  task.removeTags(lanes);" +
        "  task.addTag(lane);" +
        "  return JSON.stringify({ card: cardOf(task, laneId) });" +
        "})()";
    return of.evaluateJavascript(omni);
}
""".replace("__CARD__", json.dumps(_CARD_OMNIJS))


def read_board(kanban_tag, max_note_chars=MAX_NOTE_CHARS):
    cfg = json.dumps({"kanbanTag": kanban_tag, "maxNoteChars": max_note_chars})
    return run_jxa_or_raise(READ_BOARD_JXA, cfg)


def move_task(kanban_tag, task_id, lane_id, max_note_chars=MAX_NOTE_CHARS):
    cfg = json.dumps({"kanbanTag": kanban_tag, "maxNoteChars": max_note_chars,
                      "taskId": task_id, "laneId": lane_id})
    return run_jxa_or_raise(MOVE_TASK_JXA, cfg)
