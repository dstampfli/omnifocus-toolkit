#!/usr/bin/env python3
"""OmniFocus Kanban board: a local drag-and-drop web UI over the Kanban lanes.

Serves one page at http://127.0.0.1:<port>/ where each column is a child tag of
the `Kanban` parent (To Do / In Progress / Waiting / Done, as created by the
Kanban plug-in's Display Board action). Reviewed tasks are not on the board. Dropping a card on a column
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
from collections import Counter
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
# The board's default column order, by lane name. Lanes named here come first
# in this order; any other lane follows in OmniFocus order. This only shapes
# the board payload — the tags in OmniFocus are never reordered.
DEFAULT_LANE_ORDER = ("Waiting", "To Do", "In Progress", "Done", "Reviewed")


def parse_lane_order(value):
    """Comma-separated lane names -> tuple; blank falls back to the default."""
    names = tuple(n.strip() for n in (value or "").split(",") if n.strip())
    return names or DEFAULT_LANE_ORDER


def _load_config():
    # Same env vars the reviewer uses: KANBAN_TAG names the parent tag whose
    # children are the lanes; MAX_NOTE_CHARS caps the note read per task.
    # KANBAN_LANE_ORDER is board-only: the default column order by lane name.
    kanban = os.environ.get("KANBAN_TAG", "Kanban").strip() or "Kanban"
    max_note = _positive_int_env("MAX_NOTE_CHARS", "4000")
    lane_order = parse_lane_order(os.environ.get("KANBAN_LANE_ORDER", ""))
    return kanban, max_note, lane_order


KANBAN_TAG, MAX_NOTE_CHARS, KANBAN_LANE_ORDER = _load_config()


# ------------------------------ pure helpers ------------------------------
def order_lanes(lanes, order):
    """Lanes whose name (exact, case-sensitive like OmniFocus) appears in
    `order` first, in that order; every other lane after them in the given
    (OmniFocus) order. Names with no matching lane are ignored."""
    by_name = {lane["name"]: lane for lane in lanes}
    named = [by_name[n] for n in order if n in by_name]
    placed = {id(lane) for lane in named}
    return named + [lane for lane in lanes if id(lane) not in placed]



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
                excerpt_chars=EXCERPT_CHARS, parent_of=None):
    """Turn a raw OmniJS card into the API card: the raw `note` becomes a
    short `note_excerpt` (cleaned with clean_note, then capped) and the
    task's own `tag_ids` grow to include every ancestor (via `parent_of`),
    so a card tagged `Engagements ▸ NG` also matches a filter on
    `Engagements`. `tags` stays the display list of leaf names."""
    card = {k: v for k, v in raw_card.items() if k != "note"}
    card.setdefault("project", None)
    card.setdefault("tags", [])
    card["tag_ids"] = expand_tag_ids(raw_card.get("tag_ids") or [], parent_of or {})
    card.setdefault("flagged", False)
    for field in ("due", "defer", "added"):
        card.setdefault(field, None)
    note = clean_note(raw_card.get("note") or "", max_note_chars)
    if len(note) > excerpt_chars:
        note = note[:excerpt_chars].rstrip() + "…"
    card["note_excerpt"] = note
    return card


def expand_tag_ids(tag_ids, parent_of):
    """The given tag ids plus every ancestor's id, each once, in a stable
    order (each id, then its chain of parents). Ids missing from `parent_of`
    are kept as-is with no ancestors."""
    out, seen = [], set()
    for tag_id in tag_ids:
        cur = tag_id
        while cur is not None and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = parent_of.get(cur)
    return out


def build_tags(raw_tags, cards):
    """The filterable tag tree for the page: every raw tag (the read stage
    emits them in OmniFocus order, parents before children, with the Kanban
    subtree left out) plus its `depth` and a `count` of the given cards whose
    ancestor-expanded `tag_ids` include it — so a parent's count covers its
    descendants' cards."""
    counts = Counter(tid for c in cards for tid in c.get("tag_ids", []))
    depth_of = {}
    tags = []
    for t in raw_tags:
        parent = t.get("parent_id")
        depth = depth_of.get(parent, -1) + 1 if parent is not None else 0
        depth_of[t["id"]] = depth
        tags.append({"id": t["id"], "name": t["name"], "parent_id": parent,
                     "depth": depth, "count": counts.get(t["id"], 0)})
    return tags


def build_board(raw, sort_key="due", max_note_chars=MAX_NOTE_CHARS,
                lane_order=DEFAULT_LANE_ORDER):
    """Assemble the API board from the read stage's raw payload.

    Lanes are ordered by `lane_order` (see order_lanes; an empty order keeps
    OmniFocus order). Completed/dropped cards are hidden, a task
    carrying two lane tags is shown in the first lane only (the raw payload is
    emitted in lane order), and cards are sorted by `sort_key`. `tags` is the
    non-lane tag tree (see build_tags) and each card's `tag_ids` include its
    ancestors; a raw payload with no `tags` yields an empty tree."""
    lanes = order_lanes([{"id": lane["id"], "name": lane["name"]}
                         for lane in raw.get("lanes", [])], lane_order)
    lane_ids = {lane["id"] for lane in lanes}
    raw_tags = raw.get("tags") or []
    parent_of = {t["id"]: t.get("parent_id") for t in raw_tags}
    seen = set()
    cards = []
    for raw_card in raw.get("cards", []):
        if raw_card.get("status") in _HIDDEN_STATUSES:
            continue
        if raw_card["id"] in seen or raw_card.get("lane_id") not in lane_ids:
            continue
        seen.add(raw_card["id"])
        cards.append(finish_card(raw_card, max_note_chars, parent_of=parent_of))
    return {"lanes": lanes, "tags": build_tags(raw_tags, cards),
            "cards": sort_cards(cards, sort_key)}


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
    """Id sets and the tag parent map from the most recent successful read,
    plus the lock that serializes every osascript call (so two quick drops
    cannot interleave their Apple Events and a read never observes a
    half-applied move). `parent_of` lets the move path expand the returned
    card's tag_ids exactly as the board read did."""

    def __init__(self):
        self.lock = threading.Lock()
        self.loaded = False
        self.task_ids = set()
        self.lane_ids = set()
        self.parent_of = {}

    def remember(self, board):
        self.task_ids = {c["id"] for c in board["cards"]}
        self.lane_ids = {lane["id"] for lane in board["lanes"]}
        self.parent_of = {t["id"]: t.get("parent_id") for t in board.get("tags", [])}
        self.loaded = True


# ------------------------------- JXA programs -----------------------------

# OmniJS snippet shared by both programs: serializes one task as a raw card.
# Expects `laneIds` ({tagId: true} for the Kanban children) and `maxNoteChars`
# to be defined by the enclosing program. Dates are epoch ms or null (numeric
# comparison sidesteps timezone/format ambiguity, as in the sorter); `tags` are
# the task's non-lane leaf tag names and `tag_ids` the same tags' ids (only the
# directly assigned tags — OmniFocus never reports a parent as assigned, so the
# Python side adds ancestors); the raw note is capped here and turned into an
# excerpt on the Python side.
_CARD_OMNIJS = (
    "  const ms = d => d ? d.getTime() : null;"
    "  const statusName = t => {"
    "    const s = String(t.taskStatus);"
    "    const i = s.indexOf(': ');"
    "    return (i === -1) ? s : s.slice(i + 2, -1);"
    "  };"
    "  const cardOf = (t, laneId) => {"
    "    const proj = t.containingProject;"
    "    const own = (t.tags || []).filter(x => !laneIds[x.id.primaryKey]);"
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
    "      tags: own.map(x => x.name),"
    "      tag_ids: own.map(x => x.id.primaryKey),"
    "      note: String(t.note || '').slice(0, maxNoteChars)"
    "    };"
    "  };"
)

# Reads the Kanban parent's children (the lanes, in OmniFocus order), every
# open task in each lane, and the rest of the tag tree — every tag outside the
# Kanban subtree, in OmniFocus order with parents before children, dropped
# tags skipped — entirely in OmniJS (tags need the bridge). A task in two lanes
# is emitted for the first lane only. argv[0] = JSON {kanbanTag, maxNoteChars};
# only those two config values reach the source.
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
        "  const tagTree = [];" +
        "  const walkTags = (list, parentId) => {" +
        "    (list || []).forEach(tg => {" +
        "      if (tg.id.primaryKey === parent.id.primaryKey) return;" +
        "      if (String(tg.status).indexOf('Dropped') !== -1) return;" +
        "      tagTree.push({ id: tg.id.primaryKey, name: tg.name, parent_id: parentId });" +
        "      walkTags(tg.children, tg.id.primaryKey);" +
        "    });" +
        "  };" +
        "  walkTags(tags, null);" +
        "  return JSON.stringify({" +
        "    lanes: lanes.map(l => ({ id: l.id.primaryKey, name: l.name }))," +
        "    tags: tagTree," +
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


# --------------------------------- BoardApp -------------------------------

MISSING_TAG_MESSAGE = ("No tag named {tag!r}. Run the Kanban plug-in's "
                       "Display Board action first.")


class BoardApp:
    """The board's endpoint logic, independent of HTTP: each method returns
    (status, payload). `read`/`move` are injectable for tests."""

    def __init__(self, kanban_tag=KANBAN_TAG, *, page_path=PAGE_PATH,
                 read=read_board, move=move_task, max_note_chars=MAX_NOTE_CHARS,
                 lane_order=KANBAN_LANE_ORDER, now=None):
        self.kanban_tag = kanban_tag
        self.page_path = page_path
        self.read = read
        self.move = move
        self.max_note_chars = max_note_chars
        self.lane_order = lane_order
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.state = BoardState()

    def page(self):
        return Path(self.page_path).read_bytes()

    def get_board(self):
        with self.state.lock:
            try:
                raw = self.read(self.kanban_tag, self.max_note_chars)
            except JxaError as e:
                return 500, {"error": str(e)}
            if raw.get("error") == "missing_kanban_tag":
                return 409, {"error": MISSING_TAG_MESSAGE.format(tag=self.kanban_tag)}
            if "error" in raw:
                return 500, {"error": str(raw["error"])}
            board = build_board(raw, max_note_chars=self.max_note_chars,
                                lane_order=self.lane_order)
            self.state.remember(board)
        board["kanban_tag"] = self.kanban_tag
        board["read_at"] = self.now().isoformat(timespec="seconds")
        return 200, board

    def post_move(self, body, has_header):
        if not has_header:
            return 400, {"error": "missing X-Kanban header"}
        try:
            task_id, lane_id = validate_move(
                body, self.state.task_ids, self.state.lane_ids, self.state.loaded)
        except MoveError as e:
            return 400, {"error": str(e)}
        with self.state.lock:
            try:
                raw = self.move(self.kanban_tag, task_id, lane_id, self.max_note_chars)
            except JxaError as e:
                return 500, {"error": str(e)}
        if raw.get("error") == "missing_kanban_tag":
            return 409, {"error": MISSING_TAG_MESSAGE.format(tag=self.kanban_tag)}
        if "error" in raw:
            return 400, {"error": str(raw["error"])}
        return 200, {"card": finish_card(raw["card"], self.max_note_chars,
                                         parent_of=self.state.parent_of)}


# ------------------------------- HTTP server ------------------------------

class KanbanHandler(BaseHTTPRequestHandler):
    """Thin plumbing from HTTP to BoardApp (available as self.server.app)."""

    def _send(self, status, payload, content_type="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        app = self.server.app
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, app.page(), "text/html; charset=utf-8")
        elif path == "/api/board":
            self._send(*app.get_board())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        app = self.server.app
        if self.path.split("?", 1)[0] != "/api/move":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            body = None
        has_header = self.headers.get("X-Kanban") is not None
        self._send(*app.post_move(body, has_header))

    def log_message(self, fmt, *args):
        # One stderr line per request (including the 30 s auto-refresh) is
        # noise for a personal tool; failures are reported to the page instead.
        pass


def make_server(app, port, host="127.0.0.1"):
    server = ThreadingHTTPServer((host, port), KanbanHandler)
    server.app = app
    return server


def serve(app, port, open_browser=True):
    try:
        server = make_server(app, port)
    except OSError as e:
        print(f"Could not listen on 127.0.0.1:{port}: {e}", file=sys.stderr)
        raise SystemExit(1)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Kanban board: {url}  (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# ----------------------------------- CLI -----------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Serve a drag-and-drop Kanban board over the OmniFocus "
                    "Kanban tag lanes at http://127.0.0.1:<port>/.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port to listen on (default {DEFAULT_PORT})")
    parser.add_argument("--no-open", action="store_true",
                        help="don't open the board in your browser on start")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    serve(BoardApp(), args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
