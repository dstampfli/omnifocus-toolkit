# omnifocus-toolkit

AI-assisted automation for **OmniFocus**, powered by the Claude API:

- **Inbox triage** — classify open Inbox tasks against your active projects and
  move confident matches into place.
- **Task reviewer** — enrich not-yet-reviewed tasks in named projects with a
  clearer title and a summary of any linked page or attachment.
- **MCP server** — expose both tools to Claude Desktop / Cowork so a scheduled
  agent can run them for you.

Both tools read your OmniFocus data locally via `osascript` (JXA) and are
**dry-run by default** — they report what they would do and change nothing until
you pass `--apply`.

## Requirements

- macOS with OmniFocus 3/4 installed
- [`uv`](https://docs.astral.sh/uv/) and Python 3.12+ (managed by `uv`)
- An Anthropic API key
- On first run, macOS prompts for Automation permissions (System Settings →
  Privacy & Security → Automation) so `osascript` can control OmniFocus

Install dependencies and set up your key:

```bash
uv sync
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

## Inbox triage (`omnifocus_inbox_triage.py`)

Reads every **open** OmniFocus Inbox item (completed items are skipped) and uses
the Claude API to categorize each one against your existing **active projects**,
then moves confidently-matched tasks into their project. Low-confidence or
unmatched items are left in the Inbox and reported for manual filing.

By default it's a **dry run** — it classifies and prints a report, changing
nothing. Pass `--apply` to actually move the matched tasks.

**Guide the classifier with project descriptions.** The model reads each
project's **OmniFocus note** as a description of what belongs there, and that
description takes precedence over the project name. Adding a one-line note to
each project (e.g. *"Me — personal: repairs, appliances, utilities"*) markedly
improves accuracy, since names alone are often ambiguous (two similarly-named projects).
Projects with no note fall back to name and folder path.

```bash
uv run python omnifocus_inbox_triage.py            # dry-run: classify and report, change nothing
uv run python omnifocus_inbox_triage.py --apply    # classify, then move high-confidence matches
```

`.env` settings (each falls back to a built-in default if omitted):

- `ANTHROPIC_API_KEY` — your Anthropic key (read automatically by the SDK). An
  `ant auth login` profile or an exported env var works too.
- `MODEL` — the classification model id; defaults to `claude-sonnet-5`, a
  vision-capable model needed to read attachment images/PDFs, with
  `claude-opus-4-8` available as a higher-quality, higher-cost alternative.
- `MOVE_MIN_CONFIDENCE` — `high` by default; set to `medium` to also move
  medium-confidence matches.
- `CHUNK_SIZE` — inbox items sent per classification API call; the script
  processes large inboxes in batches so a single call's output never exceeds the
  model's token limit.
- **Attachment & email enrichment.** PDF and image attachments on an Inbox item
  are read by the vision model; forwarded-email notes are cleaned of invisible
  padding. `MAX_ATTACHMENT_BYTES` (default 10 MiB) caps per-attachment size —
  larger or unsupported attachments are skipped, with their filename kept as a
  hint. `MAX_BATCH_ATTACHMENT_BYTES` bounds per-call attachment bytes and
  `MAX_NOTE_CHARS` truncates long notes.

## Task reviewer (`omnifocus_task_reviewer.py`)

Reviews not-yet-reviewed tasks in the named OmniFocus project(s) and enriches
each in place: it fetches any URL the task references (via the model's web_fetch)
and reads its attachments, then sets a clearer title and appends a `--- Summary
---` section to the note. Reviewed tasks are marked with a tag (default
`reviewed`) so re-runs skip them. Non-destructive: the original note, URL, and
attachments are preserved.

```bash
uv run python omnifocus_task_reviewer.py "Training"            # dry-run: show proposed enrichments
uv run python omnifocus_task_reviewer.py "Training" "Tech"     # multiple projects
uv run python omnifocus_task_reviewer.py "Training" --apply    # write: rename, append summary, tag reviewed
```

Uses the same `.env` and Anthropic key as the triage tool, plus `REVIEW_TAG` and
`WEB_FETCH_MAX_USES`. Requires a web_fetch-capable model (the `claude-sonnet-5`
default).

## MCP server (Claude Desktop / Cowork)

`omnifocus_mcp_server.py` is a local **stdio** MCP server (built on `mcp[cli]`
/ FastMCP) that exposes the AI-driven tools to a scheduled Claude Cowork task
running inside Claude Desktop on this Mac. There is no network transport, auth,
or tunnel — Claude Desktop launches the server as a local subprocess and speaks
MCP to it over stdio.

Tools:

- `triage_inbox(apply=false)` — classify open Inbox tasks; with `apply=true`,
  move high-confidence matches into their project.
- `review_tasks(projects, apply=false)` — enrich not-yet-reviewed tasks in the
  named project(s); with `apply=true`, write changes and tag them reviewed.
- `omnifocus_status()` — read-only Inbox/active-project counts (no API call), so
  a scheduled agent can decide whether to act before spending tokens.

`apply` defaults to `false` everywhere: a scheduled agent must explicitly pass
`apply=true` to change OmniFocus, mirroring the CLI's dry-run-by-default model.

### Claude Desktop configuration

Add a Local MCP server under **Settings → Developer → Local MCP servers**:

- Command: `/opt/homebrew/bin/uv`
- Arguments:
  `run --with mcp[cli] --with-editable /path/to/omnifocus-toolkit mcp run /path/to/omnifocus-toolkit/omnifocus_mcp_server.py`

The first run prompts macOS to allow **Claude Desktop** to control OmniFocus
(System Settings → Privacy & Security → Automation). `ANTHROPIC_API_KEY` is read
from the repo's `.env` (loaded relative to the server file, so the launch cwd
does not matter).
