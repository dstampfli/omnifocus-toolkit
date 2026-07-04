# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two independent macOS automation utilities for OmniFocus, plus their shared scaffolding:

- `omnifocus_omnioutliner_sync.py` syncs tagged OmniFocus Inbox tasks into an OmniOutliner document.
- `omnifocus_inbox_triage.py` reads *all* OmniFocus Inbox tasks and uses the Claude API to classify each one against the user's existing active projects, moving high-confidence matches into their project.

`main.py` is the unused `uv`-generated placeholder entry point.

## Commands

```bash
python3 omnifocus_omnioutliner_sync.py            # copy matching tasks into OmniOutliner
python3 omnifocus_omnioutliner_sync.py --dry-run  # list what would be copied, write nothing
python3 omnifocus_omnioutliner_sync.py --complete # copy, then mark copied tasks complete in OmniFocus
```

No dependencies (stdlib only), no build step, no tests. `--complete` is ignored when combined with `--dry-run`.

```bash
python3 omnifocus_inbox_triage.py            # dry-run: classify Inbox tasks and report, change nothing
python3 omnifocus_inbox_triage.py --apply    # classify, then move high-confidence matches into their project

uv sync                                      # install the anthropic/pydantic dependencies
uv run pytest                                # run the unit tests
```

`omnifocus_inbox_triage.py` follows a dry-run-by-default / `--apply`-to-write safety model, mirroring `--dry-run` above but inverted (dry-run is the implicit default rather than an opt-in flag).

## Architecture

`omnifocus_omnioutliner_sync.py` is a thin Python wrapper around a single JXA (JavaScript for Automation) program run via `osascript -l JavaScript`:

- **Python side** (`main`): parses CLI flags, serializes config (tag/doc/anchor names + flags) to JSON, invokes `osascript`, then parses the JSON result the JXA prints and formats a human-readable summary.
- **JXA side** (`JXA_TEMPLATE`): the actual work, in four stages — (1) pull OmniFocus inbox tasks whose tags include `TAG_NAME`, (2) find the target OmniOutliner document and the `ANCHOR_ROW` beneath which rows are appended, (3) append one row per task (topic = task name, note = task note), skipping topics already present so re-runs are idempotent, (4) optionally mark copied tasks complete in OmniFocus.

Config is compile-time constants at the top of the file (`TAG_NAME`, `DOC_NAME`, `ANCHOR_ROW`, `DOC_PATH`), not CLI args. All cross-process communication is JSON strings over `osascript`'s argv/stdout.

`omnifocus_inbox_triage.py` shares the same embedded-JXA-over-`osascript` pattern but inserts a Claude API classification stage between an OmniFocus read and an OmniFocus write, giving it three stages instead of one:

1. **Read** (`READ_JXA` / `read_omnifocus`): JXA pulls every Inbox task and every project (with id, name, folder path, status); the Python side filters to active projects.
2. **Classify** (`classify`): the items and projects are sent to the Claude API via `client.messages.parse(..., output_format=Classification)`, using structured output (Pydantic models `Decision`/`Classification`) so the model returns one typed decision per Inbox item instead of free text to parse.
3. **Apply** (`WRITE_JXA` / `apply_moves`): for decisions that clear `MOVE_MIN_CONFIDENCE`, a second JXA program reassigns each task's `assignedContainer` to its matched project — run only when `--apply` is passed.

As with the sync script, config is compile-time constants at the top of the file (`MODEL`, `MOVE_MIN_CONFIDENCE`), and all cross-process communication is JSON over `osascript`'s argv/stdout. Unlike the sync script, the pure decision/parsing/prompt-building/reporting logic (everything except the two JXA calls and the live API call) is unit-tested under `tests/`.

## Runtime requirements

- macOS with OmniFocus 3/4 and OmniOutliner 5+ installed.
- The target OmniOutliner document must be **open** (or `DOC_PATH` set to a `.ooutline` file the script will open) — only relevant to `omnifocus_omnioutliner_sync.py`.
- First run prompts for Automation permissions (System Settings → Privacy & Security → Automation) for the terminal app — required for `osascript` to control OmniFocus (and OmniOutliner, for the sync script).
- `omnifocus_inbox_triage.py` additionally requires the `anthropic` package (installed via `uv sync`) and an `ANTHROPIC_API_KEY` environment variable (or an active `ant auth login` profile).
